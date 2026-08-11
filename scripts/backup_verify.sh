#!/bin/bash
# Rafita AVP — Periodic backup with integrity verification.
# Run inside the container: docker exec rafita-agent-core bash /workspace/scripts/backup_verify.sh
# Or as a cron job on the host.

set -euo pipefail

BACKUP_DIR="/data/backups"
DB_FILE="/data/db/rafita.db"
VECTOR_DB_DIR="/data/vector_db"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="rafita_backup_${TIMESTAMP}"
BACKUP_PATH="$BACKUP_DIR/$BACKUP_NAME"
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

echo "[$(date +%H:%M:%S)] Starting backup: $BACKUP_NAME"

# 1. SQLite backup (atomic with .backup)
echo "[$(date +%H:%M:%S)] Backing up SQLite DB..."
sqlite3 "$DB_FILE" ".backup '$TEMP_DIR/rafita.db'"

# 2. Vector DB backup (copy, small files)
echo "[$(date +%H:%M:%S)] Backing up vector DB..."
cp -r "$VECTOR_DB_DIR" "$TEMP_DIR/vector_db"

# 3. Package
echo "[$(date +%H:%M:%S)] Creating archive..."
tar -czf "$BACKUP_PATH.tar.gz" -C "$TEMP_DIR" .

ACTUAL_SIZE=$(stat -c%s "$BACKUP_PATH.tar.gz" 2>/dev/null || stat -f%z "$BACKUP_PATH.tar.gz" 2>/dev/null || echo 0)
echo "[$(date +%H:%M:%S)] Backup created: $BACKUP_PATH.tar.gz ($ACTUAL_SIZE bytes)"

# 4. Verify: restore to temp and test integrity
echo "[$(date +%H:%M:%S)] Verifying backup integrity..."
VERIFY_DIR=$(mktemp -d)
tar -xzf "$BACKUP_PATH.tar.gz" -C "$VERIFY_DIR"

# Check SQLite integrity
sqlite3 "$VERIFY_DIR/rafita.db" "PRAGMA integrity_check;" > /dev/null 2>&1 && \
    echo "[$(date +%H:%M:%S)]   SQLite: integrity OK" || \
    echo "[$(date +%H:%M:%S)]   SQLite: INTEGRITY CHECK FAILED"

# Check vector DB has expected structure
if [ -f "$VERIFY_DIR/vector_db/chroma.sqlite3" ]; then
    echo "[$(date +%H:%M:%S)]   Vector DB: files present"
else
    echo "[$(date +%H:%M:%S)]   Vector DB: WARNING - missing chroma.sqlite3"
fi

# Count chunks
CHUNK_COUNT=$(sqlite3 "$VERIFY_DIR/vector_db/chroma.sqlite3" \
    "SELECT COUNT(*) FROM embeddings" 2>/dev/null || echo "N/A")
echo "[$(date +%H:%M:%S)]   Vector DB: $CHUNK_COUNT chunks"

rm -rf "$VERIFY_DIR"

# 5. Cleanup old backups
echo "[$(date +%H:%M:%S)] Cleaning backups older than $RETENTION_DAYS days..."
find "$BACKUP_DIR" -name "rafita_backup_*.tar.gz" -mtime "+$RETENTION_DAYS" -delete 2>/dev/null || true

# 6. Log result
echo "[$(date +%H:%M:%S)] Backup complete: $BACKUP_NAME.tar.gz"
echo ""
