#!/bin/bash
# Secret scanning for the new git repo
echo "=== Secret scan (tracked files only) ==="
errors=0

# Pattern: real-looking Telegram tokens (8+ digit numbers, not 123456)
echo "--- Telegram tokens ---"
git grep -n 'TELEGRAM_TOKEN\s*=\s*[0-9]\{8,\}' 2>/dev/null && errors=1 || echo "  Clean"

# Pattern: MongoDB connection strings
echo "--- MongoDB URIs ---"
git grep -n 'mongodb+srv://' 2>/dev/null && errors=1 || echo "  Clean"

# Pattern: API keys (hex 20+ chars)
echo "--- API keys ---"
git grep -nE 'api.?key\s*=\s*[a-f0-9]{20,}' 2>/dev/null && errors=1 || echo "  Clean"

# Pattern: Fernet keys (base64 32+ chars)
echo "--- Fernet keys ---"
git grep -nE 'ENCRYPTION_KEY\s*=\s*[a-zA-Z0-9_\-=]{32,}' 2>/dev/null && errors=1 || echo "  Clean"

# Known sensitive data
echo "--- Known PII ---"
git grep -n '29560575D\|WzVjksNjyV' 2>/dev/null && errors=1 || echo "  Clean"

echo ""
echo "Result: $errors potential issues found"
exit $errors
