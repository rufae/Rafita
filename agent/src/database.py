import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from src.config import settings
from src.logger import logger
from src.utils.security_manager import decrypt_value, encrypt_value


class DatabaseManager:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or settings.db_path_obj
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._create_tables()
        logger.info("Database initialized at %s", self.db_path)

    async def _create_tables(self) -> None:
        schema = [
            """
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_chat_history_chat_id
            ON chat_history(chat_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_chat_history_created_at
            ON chat_history(created_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                event_datetime TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                is_active INTEGER NOT NULL DEFAULT 1,
                google_event_id TEXT
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_events_chat_id
            ON events(chat_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_events_datetime
            ON events(event_datetime)
            """,
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                due TEXT,
                source TEXT NOT NULL DEFAULT 'local',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_chat_id
            ON tasks(chat_id)
            """,
            """
            CREATE TABLE IF NOT EXISTS email_sequences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                contact_name TEXT NOT NULL,
                contact_email TEXT NOT NULL,
                context TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS email_sequence_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sequence_id INTEGER NOT NULL,
                step_no INTEGER NOT NULL,
                delay_days INTEGER NOT NULL DEFAULT 0,
                goal TEXT NOT NULL,
                subject TEXT,
                body TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                sent_at TEXT,
                message_id TEXT
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_sequence_steps_seq
            ON email_sequence_steps(sequence_id)
            """,
            """
            CREATE TABLE IF NOT EXISTS web_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL DEFAULT '',
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 0,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                duration_s REAL NOT NULL DEFAULT 0,
                speakers TEXT NOT NULL DEFAULT '',
                transcript TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                tasks TEXT NOT NULL DEFAULT '',
                note_path TEXT NOT NULL DEFAULT '',
                audio_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                alert_type TEXT NOT NULL DEFAULT 'info',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT,
                is_read INTEGER NOT NULL DEFAULT 0,
                pattern TEXT,
                next_run TEXT
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_alerts_chat_id
            ON alerts(chat_id)
            """,
            """
            CREATE TABLE IF NOT EXISTS finance_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL CHECK(category IN ('income','expense','transfer','investment')),
                subcategory TEXT,
                description TEXT,
                currency TEXT NOT NULL DEFAULT 'MXN',
                recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_finance_chat_id
            ON finance_records(chat_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_finance_recorded_at
            ON finance_records(recorded_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_finance_category
            ON finance_records(category)
            """,
            """
            CREATE TABLE IF NOT EXISTS exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                export_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                file_path TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at TEXT,
                error_message TEXT
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_exports_chat_id
            ON exports(chat_id)
            """,
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                chat_id INTEGER PRIMARY KEY,
                preferences TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS personal_knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(chat_id, key)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_personal_knowledge_chat
            ON personal_knowledge(chat_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_personal_knowledge_category
            ON personal_knowledge(category)
            """,
            """
            CREATE TABLE IF NOT EXISTS app_connectors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                connector_type TEXT NOT NULL,
                credentials_enc TEXT,
                config_json TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS voice_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                chat_id INTEGER,
                state TEXT DEFAULT 'active',
                transcript TEXT,
                started_at TEXT,
                ended_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value TEXT,
                expires_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS second_brain_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_text TEXT NOT NULL,
                chat_id INTEGER,
                notes_found TEXT,
                chunks_retrieved INTEGER DEFAULT 0,
                top_relevance REAL DEFAULT 0.0,
                executed_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                service TEXT NOT NULL,
                value_enc TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(chat_id, service)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS knowledge_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(chat_id, subject, predicate, object)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_knowledge_relations_chat
            ON knowledge_relations(chat_id)
            """,
            """
            CREATE TABLE IF NOT EXISTS automation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT NOT NULL UNIQUE,
                workflow TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('ok','error')),
                severity TEXT NOT NULL DEFAULT 'info',
                error TEXT,
                finished_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 0,
                endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_automation_runs_workflow
            ON automation_runs(workflow, id DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS conditional_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                rule_json TEXT NOT NULL,
                message TEXT NOT NULL,
                repeats INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                fired_at TEXT
            )
            """,
        ]
        for stmt in schema:
            await self._conn.execute(stmt)

        existing_cols = set()
        cursor = await self._conn.execute("PRAGMA table_info(events)")
        for row in await cursor.fetchall():
            existing_cols.add(row[1])
        if "google_event_id" not in existing_cols:
            await self._conn.execute("ALTER TABLE events ADD COLUMN google_event_id TEXT")
        existing_cols = set()
        cursor = await self._conn.execute("PRAGMA table_info(automation_runs)")
        for row in await cursor.fetchall():
            existing_cols.add(row[1])
        if "severity" not in existing_cols:
            # Taxonomia unica de prioridades (tareas.md D12): las filas
            # viejas se derivan de status (ok->info, error->error).
            await self._conn.execute(
                "ALTER TABLE automation_runs ADD COLUMN severity TEXT NOT NULL DEFAULT 'info'"
            )
            await self._conn.execute(
                "UPDATE automation_runs SET severity = "
                "CASE status WHEN 'ok' THEN 'info' ELSE 'error' END"
            )
        existing_cols = set()
        cursor = await self._conn.execute("PRAGMA table_info(alerts)")
        for row in await cursor.fetchall():
            existing_cols.add(row[1])
        if "pattern" not in existing_cols:
            await self._conn.execute("ALTER TABLE alerts ADD COLUMN pattern TEXT")
        if "next_run" not in existing_cols:
            await self._conn.execute("ALTER TABLE alerts ADD COLUMN next_run TEXT")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed")

    @asynccontextmanager
    async def transaction(self):
        if not self._conn:
            raise RuntimeError("Database not initialized")
        try:
            yield self._conn
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        if not self._conn:
            raise RuntimeError("Database not initialized")
        return await self._conn.execute(sql, params)

    async def executemany(self, sql: str, params_list: list[tuple]) -> None:
        if not self._conn:
            raise RuntimeError("Database not initialized")
        await self._conn.executemany(sql, params_list)
        await self._conn.commit()

    async def fetchone(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        cursor = await self.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        cursor = await self.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def insert(self, sql: str, params: tuple = ()) -> int:
        cursor = await self.execute(sql, params)
        await self._conn.commit()
        return cursor.lastrowid

    async def execute_fetchall(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        return await self.fetchall(sql, params)

    async def execute_fetchone(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        return await self.fetchone(sql, params)

    async def execute_insert(self, sql: str, params: tuple = ()) -> int:
        return await self.insert(sql, params)

    async def save_chat_message(self, chat_id: int, role: str, content: str) -> int:
        sql = """
            INSERT INTO chat_history (chat_id, role, content, created_at)
            VALUES (?, ?, ?, datetime('now'))
        """
        return await self.insert(sql, (chat_id, role, content))

    async def update_last_chat_message(self, chat_id: int, role: str, new_content: str) -> None:
        sql = """
            UPDATE chat_history
            SET content = ?
            WHERE id = (
                SELECT id FROM chat_history
                WHERE chat_id = ? AND role = ?
                ORDER BY created_at DESC, id DESC LIMIT 1
            )
        """
        await self.execute(sql, (new_content, chat_id, role))
        await self._conn.commit()

    async def delete_chat_history(self, chat_id: int) -> None:
        await self.execute("DELETE FROM chat_history WHERE chat_id = ?", (chat_id,))
        await self._conn.commit()

    async def get_chat_history(
        self, chat_id: int, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT id, chat_id, role, content, created_at
            FROM chat_history
            WHERE chat_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """
        rows = await self.fetchall(sql, (chat_id, limit, max(0, offset)))
        rows.reverse()
        return rows

    async def clear_chat_history(self, chat_id: int) -> None:
        await self.execute("DELETE FROM chat_history WHERE chat_id = ?", (chat_id,))
        await self._conn.commit()

    async def search_chat_history(
        self, chat_id: int, query: str, days: int = 30, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Busqueda literal (LIKE escapado) en el historial del chat.

        `created_at` se guarda en UTC (datetime('now') de SQLite), por eso el
        limite se calcula en UTC tambien.
        """
        consulta = str(query or "").strip()
        if not consulta:
            return []
        desde = (datetime.now(UTC) - timedelta(days=max(1, days))).strftime("%Y-%m-%d %H:%M:%S")
        pat = "%" + consulta.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        sql = """
            SELECT role, content, created_at
            FROM chat_history
            WHERE chat_id = ? AND created_at >= ? AND content LIKE ? ESCAPE '\\'
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        """
        return await self.fetchall(sql, (chat_id, desde, pat, max(1, min(int(limit), 50))))

    async def add_event(
        self, chat_id: int, title: str, event_datetime: str, description: str | None = None
    ) -> int:
        sql = """
            INSERT INTO events (chat_id, title, description, event_datetime)
            VALUES (?, ?, ?, ?)
        """
        return await self.insert(sql, (chat_id, title, description, event_datetime))

    async def get_upcoming_events(self, chat_id: int, limit: int = 10) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM events
            WHERE chat_id = ? AND is_active = 1
                AND event_datetime >= datetime('now')
            ORDER BY event_datetime ASC
            LIMIT ?
        """
        return await self.fetchall(sql, (chat_id, limit))

    async def delete_event(self, chat_id: int, event_id: int) -> None:
        await self.execute(
            "UPDATE events SET is_active = 0 WHERE id = ? AND chat_id = ?",
            (event_id, chat_id),
        )
        await self._conn.commit()

    async def update_event_datetime(self, chat_id: int, event_id: int, new_datetime: str) -> None:
        await self.execute(
            "UPDATE events SET event_datetime = ? WHERE id = ? AND chat_id = ?",
            (new_datetime, event_id, chat_id),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------ tareas
    # (capa local: Rafita funciona sin Google; si Google esta conectado se usa
    # Google Tasks y esta tabla queda como respaldo/offline)

    async def add_task(self, chat_id: int, title: str, due: str | None = None) -> int:
        sql = """
            INSERT INTO tasks (chat_id, title, due)
            VALUES (?, ?, ?)
        """
        return await self.insert(sql, (chat_id, title, due))

    async def list_tasks(self, chat_id: int, show_completed: bool = False) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM tasks
            WHERE chat_id = ?%s
            ORDER BY status ASC, due IS NULL, due ASC, id ASC
            LIMIT 50
        """ % ("" if show_completed else " AND status = 'pending'")
        return await self.fetchall(sql, (chat_id,))

    async def complete_task(self, chat_id: int, task_id: int) -> int:
        """Marca completada y devuelve cuantas filas toco (0 = no existia).

        Bug 2026-10-06 (llamada): sin rowcount el caller devolvia
        success=True aunque no hubiera tarea y el modelo decia "hecho".
        """
        cursor = await self.execute(
            "UPDATE tasks SET status = 'completed' WHERE id = ? AND chat_id = ?",
            (task_id, chat_id),
        )
        await self._conn.commit()
        return cursor.rowcount or 0

    async def delete_task(self, chat_id: int, task_id: int) -> int:
        """Borra la tarea y devuelve cuantas filas toco (0 = no existia)."""
        cursor = await self.execute(
            "DELETE FROM tasks WHERE id = ? AND chat_id = ?", (task_id, chat_id)
        )
        await self._conn.commit()
        return cursor.rowcount or 0

    async def delete_stale_chat_history(self, chat_id: int, hours: int = 2) -> int:
        """Poda solo los mensajes mas antiguos que `hours` (bug 2026-10-06).

        Al empezar cada llamada se borraba TODO el historial de voz: una
        reconexion (WS, pestana, movil) perdia el hilo y el modelo volvia a
        saludar "Hola, ¿en que puedo ayudarte?". Con la poda por antigüedad
        las reconexiones conservan el contexto y solo se limpia lo viejo.
        """
        cursor = await self.execute(
            "DELETE FROM chat_history WHERE chat_id = ? AND created_at < datetime('now', ?)",
            (chat_id, "-%d hours" % hours),
        )
        await self._conn.commit()
        return cursor.rowcount or 0

    # ---------- reglas condicionales (2026-10-06) ----------

    async def add_conditional_rule(
        self, chat_id: int, rule_json: str, message: str, repeats: bool = False
    ) -> int:
        sql = """
            INSERT INTO conditional_rules
                (chat_id, rule_json, message, repeats, is_active, created_at)
            VALUES (?, ?, ?, ?, 1, datetime('now'))
        """
        return await self.insert(sql, (chat_id, rule_json, message, 1 if repeats else 0))

    async def list_active_conditional_rules(self) -> list[dict[str, Any]]:
        sql = """
            SELECT id, chat_id, rule_json, message, repeats, fired_at, created_at
            FROM conditional_rules WHERE is_active = 1 ORDER BY id ASC
        """
        return await self.fetchall(sql)

    async def list_conditional_rules(self, chat_id: int) -> list[dict[str, Any]]:
        sql = """
            SELECT id, rule_json, message, repeats, is_active, fired_at, created_at
            FROM conditional_rules WHERE chat_id = ? ORDER BY id ASC
        """
        return await self.fetchall(sql, (chat_id,))

    async def fire_conditional_rule(self, rule_id: int, repeats: bool = False) -> None:
        if repeats:
            await self.execute(
                "UPDATE conditional_rules SET fired_at = datetime('now') WHERE id = ?",
                (rule_id,),
            )
        else:
            await self.execute(
                "UPDATE conditional_rules SET fired_at = datetime('now'), is_active = 0 "
                "WHERE id = ?",
                (rule_id,),
            )
        await self._conn.commit()

    async def delete_conditional_rule(self, chat_id: int, rule_id: int) -> int:
        cursor = await self.execute(
            "DELETE FROM conditional_rules WHERE id = ? AND chat_id = ?",
            (rule_id, chat_id),
        )
        await self._conn.commit()
        return cursor.rowcount or 0

    # ---------- suscripciones web push (PWA, mejora 6) ----------

    async def add_push_subscription(
        self, user_id: int, endpoint: str, p256dh: str, auth: str
    ) -> None:
        await self.execute(
            "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(endpoint) DO UPDATE SET "
            "user_id = excluded.user_id, p256dh = excluded.p256dh, "
            "auth = excluded.auth",
            (user_id, endpoint, p256dh, auth),
        )
        await self._conn.commit()

    async def list_push_subscriptions(self) -> list[dict[str, Any]]:
        return await self.fetchall("SELECT * FROM push_subscriptions ORDER BY id")

    async def delete_push_subscription(self, endpoint: str) -> None:
        await self.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        await self._conn.commit()

    # ---------- secuencias de email (Fase 2) ----------

    async def create_sequence(
        self, name: str, contact_name: str, contact_email: str, context: str = ""
    ) -> int:
        sql = """
            INSERT INTO email_sequences (name, contact_name, contact_email, context)
            VALUES (?, ?, ?, ?)
        """
        return await self.insert(sql, (name, contact_name, contact_email, context))

    async def add_sequence_step(
        self, sequence_id: int, step_no: int, delay_days: int, goal: str
    ) -> int:
        sql = """
            INSERT INTO email_sequence_steps (sequence_id, step_no, delay_days, goal)
            VALUES (?, ?, ?, ?)
        """
        return await self.insert(sql, (sequence_id, step_no, delay_days, goal))

    async def list_sequences(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return await self.fetchall(
                "SELECT * FROM email_sequences WHERE status = ? ORDER BY id DESC", (status,)
            )
        return await self.fetchall("SELECT * FROM email_sequences ORDER BY id DESC")

    async def get_sequence(self, sequence_id: int) -> dict[str, Any] | None:
        return await self.fetchone("SELECT * FROM email_sequences WHERE id = ?", (sequence_id,))

    async def list_sequence_steps(self, sequence_id: int) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM email_sequence_steps WHERE sequence_id = ? ORDER BY step_no",
            (sequence_id,),
        )

    async def update_sequence_status(self, sequence_id: int, status: str) -> None:
        await self.execute(
            "UPDATE email_sequences SET status = ? WHERE id = ?", (status, sequence_id)
        )
        await self._conn.commit()

    async def update_sequence_step(
        self,
        step_id: int,
        status: str,
        subject: str | None = None,
        body: str | None = None,
        message_id: str | None = None,
    ) -> None:
        await self.execute(
            """
            UPDATE email_sequence_steps
            SET status = ?, subject = COALESCE(?, subject), body = COALESCE(?, body),
                message_id = COALESCE(?, message_id),
                sent_at = CASE WHEN ? = 'sent' THEN datetime('now') ELSE sent_at END
            WHERE id = ?
            """,
            (status, subject, body, message_id, status, step_id),
        )
        await self._conn.commit()

    async def due_sequence_steps(self) -> list[dict[str, Any]]:
        """Pasos pendientes de secuencias activas cuya fecha ya llego."""
        sql = """
            SELECT s.id AS step_id, s.step_no, s.delay_days, s.goal,
                   q.id AS sequence_id, q.name AS sequence_name, q.contact_name,
                   q.contact_email, q.context, q.created_at AS sequence_created
            FROM email_sequence_steps s
            JOIN email_sequences q ON q.id = s.sequence_id
            WHERE s.status = 'pending' AND q.status = 'active'
              AND date(q.created_at, '+' || s.delay_days || ' day') <= date('now')
            ORDER BY q.id, s.step_no
        """
        return await self.fetchall(sql)

    async def delete_sequence(self, sequence_id: int) -> None:
        await self.execute("DELETE FROM email_sequence_steps WHERE sequence_id = ?", (sequence_id,))
        await self.execute("DELETE FROM email_sequences WHERE id = ?", (sequence_id,))
        await self._conn.commit()

    # ---------- usuarios web (Fase 3) ----------

    async def create_web_user(self, email: str, password_hash: str, is_admin: bool = False) -> int:
        sql = """
            INSERT INTO web_users (email, password_hash, is_admin)
            VALUES (?, ?, ?)
        """
        return await self.insert(sql, (email.strip().lower(), password_hash, 1 if is_admin else 0))

    async def get_web_user_by_email(self, email: str) -> dict[str, Any] | None:
        return await self.fetchone(
            "SELECT * FROM web_users WHERE email = ?", (email.strip().lower(),)
        )

    async def get_web_user(self, user_id: int) -> dict[str, Any] | None:
        return await self.fetchone("SELECT * FROM web_users WHERE id = ?", (user_id,))

    async def count_web_users(self) -> int:
        row = await self.fetchone("SELECT COUNT(*) AS total FROM web_users")
        return int((row or {}).get("total", 0))

    async def list_web_users(self) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT id, email, is_admin, created_at FROM web_users ORDER BY id"
        )

    async def update_web_user_password(self, user_id: int, password_hash: str) -> None:
        await self.execute(
            "UPDATE web_users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
        )
        await self._conn.commit()

    # ---------- reuniones (Fase 4) ----------

    async def create_meeting(self, user_id: int, title: str) -> int:
        sql = "INSERT INTO meetings (user_id, title) VALUES (?, ?)"
        return await self.insert(sql, (user_id, title))

    async def update_meeting(self, meeting_id: int, **campos: Any) -> None:
        permitidos = (
            "title",
            "status",
            "duration_s",
            "speakers",
            "transcript",
            "summary",
            "tasks",
            "note_path",
            "audio_path",
        )
        sets = [("%s = ?" % k) for k in permitidos if k in campos]
        if not sets:
            return
        valores = [campos[k] for k in permitidos if k in campos]
        valores.append(meeting_id)
        await self.execute("UPDATE meetings SET %s WHERE id = ?" % ", ".join(sets), tuple(valores))
        await self._conn.commit()

    async def get_meeting(self, meeting_id: int) -> dict[str, Any] | None:
        return await self.fetchone("SELECT * FROM meetings WHERE id = ?", (meeting_id,))

    async def list_meetings(
        self, user_id: int | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if user_id is not None:
            return await self.fetchall(
                "SELECT * FROM meetings WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            )
        return await self.fetchall("SELECT * FROM meetings ORDER BY id DESC LIMIT ?", (limit,))

    async def delete_meeting(self, meeting_id: int) -> None:
        await self.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        await self._conn.commit()

    async def add_alert(
        self, chat_id: int, message: str, alert_type: str = "info", expires_at: str | None = None
    ) -> int:
        sql = """
            INSERT INTO alerts (chat_id, message, alert_type, expires_at)
            VALUES (?, ?, ?, ?)
        """
        return await self.insert(sql, (chat_id, message, alert_type, expires_at))

    async def get_active_alerts(self, chat_id: int) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM alerts
            WHERE chat_id = ? AND is_read = 0
                AND (expires_at IS NULL OR expires_at >= datetime('now'))
            ORDER BY created_at DESC, id DESC
        """
        return await self.fetchall(sql, (chat_id,))

    async def mark_alert_read(self, alert_id: int) -> None:
        await self.execute("UPDATE alerts SET is_read = 1 WHERE id = ?", (alert_id,))
        await self._conn.commit()

    async def add_finance_record(
        self,
        chat_id: int,
        amount: float,
        category: str,
        subcategory: str | None = None,
        description: str | None = None,
        currency: str | None = None,
        recorded_at: str | None = None,
    ) -> int:
        currency = currency or settings.default_currency
        if recorded_at is None:
            recorded_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        sql = """
            INSERT INTO finance_records
                (chat_id, amount, category, subcategory, description, currency, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        return await self.insert(
            sql, (chat_id, amount, category, subcategory, description, currency, recorded_at)
        )

    async def get_finance_summary(
        self, chat_id: int, start_date: str | None = None, end_date: str | None = None
    ) -> dict[str, Any]:
        if not end_date:
            end_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        if not start_date:
            start_date = datetime.utcnow().replace(day=1).strftime("%Y-%m-%d 00:00:00")

        income_sql = """
            SELECT COALESCE(SUM(amount), 0) as total
            FROM finance_records
            WHERE chat_id = ? AND category = 'income'
                AND recorded_at >= ? AND recorded_at <= ?
        """
        expense_sql = """
            SELECT COALESCE(SUM(amount), 0) as total
            FROM finance_records
            WHERE chat_id = ? AND category = 'expense'
                AND recorded_at >= ? AND recorded_at <= ?
        """
        by_category_sql = """
            SELECT category, subcategory, SUM(amount) as total
            FROM finance_records
            WHERE chat_id = ? AND recorded_at >= ? AND recorded_at <= ?
            GROUP BY category, subcategory
            ORDER BY total DESC
        """
        count_sql = """
            SELECT COUNT(*) as count FROM finance_records
            WHERE chat_id = ? AND recorded_at >= ? AND recorded_at <= ?
        """

        income_row = await self.fetchone(income_sql, (chat_id, start_date, end_date))
        expense_row = await self.fetchone(expense_sql, (chat_id, start_date, end_date))
        category_rows = await self.fetchall(by_category_sql, (chat_id, start_date, end_date))
        count_row = await self.fetchone(count_sql, (chat_id, start_date, end_date))

        total_income = income_row["total"] if income_row else 0.0
        total_expenses = expense_row["total"] if expense_row else 0.0

        expense_by_category = {}
        income_by_category = {}
        for row in category_rows:
            key = row["subcategory"] or row["category"]
            if row["category"] == "expense":
                expense_by_category[key] = row["total"]
            elif row["category"] == "income":
                income_by_category[key] = row["total"]

        return {
            "total_income": total_income,
            "total_expenses": total_expenses,
            "balance": total_income - total_expenses,
            "expense_by_category": expense_by_category,
            "income_by_category": income_by_category,
            "period_start": start_date,
            "period_end": end_date,
            "transaction_count": count_row["count"] if count_row else 0,
        }

    async def get_finance_records(
        self,
        chat_id: int,
        start_date: str | None = None,
        end_date: str | None = None,
        category: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        conditions = ["chat_id = ?"]
        params: list = [chat_id]
        if start_date:
            conditions.append("recorded_at >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("recorded_at <= ?")
            params.append(end_date)
        if category:
            conditions.append("category = ?")
            params.append(category)
        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT * FROM finance_records
            WHERE {where_clause}
            ORDER BY recorded_at DESC
            LIMIT ?
        """
        params.append(limit)
        return await self.fetchall(sql, tuple(params))

    async def create_export(self, chat_id: int, export_type: str) -> int:
        sql = """
            INSERT INTO exports (chat_id, export_type, status)
            VALUES (?, ?, 'pending')
        """
        return await self.insert(sql, (chat_id, export_type))

    async def update_export(
        self,
        export_id: int,
        status: str,
        file_path: str | None = None,
        error_message: str | None = None,
    ) -> None:
        updates = ["status = ?"]
        params: list = [status]
        if file_path is not None:
            updates.append("file_path = ?")
            params.append(file_path)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if status in ("completed", "failed"):
            updates.append("completed_at = datetime('now')")
        sql = f"UPDATE exports SET {', '.join(updates)} WHERE id = ?"
        params.append(export_id)
        await self.execute(sql, tuple(params))
        await self._conn.commit()

    async def get_or_create_preferences(self, chat_id: int) -> dict[str, Any]:
        row = await self.fetchone(
            "SELECT preferences FROM user_preferences WHERE chat_id = ?", (chat_id,)
        )
        if row is None:
            default_prefs = json.dumps(
                {"currency": settings.default_currency, "voice_replies": False}
            )
            await self.insert(
                "INSERT INTO user_preferences (chat_id, preferences) VALUES (?, ?)",
                (chat_id, default_prefs),
            )
            return json.loads(default_prefs)
        return json.loads(row["preferences"])

    async def update_preferences(self, chat_id: int, prefs: dict[str, Any]) -> None:
        prefs_json = json.dumps(prefs)
        await self.execute(
            """INSERT INTO user_preferences (chat_id, preferences, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(chat_id) DO UPDATE SET
                   preferences = excluded.preferences,
                   updated_at = excluded.updated_at""",
            (chat_id, prefs_json),
        )
        await self._conn.commit()

    async def get_preference(self, chat_id: int, key: str, default: Any = None) -> Any:
        prefs = await self.get_or_create_preferences(chat_id)
        return prefs.get(key, default)

    async def set_preference(self, chat_id: int, key: str, value: Any) -> None:
        prefs = await self.get_or_create_preferences(chat_id)
        prefs[key] = value
        await self.update_preferences(chat_id, prefs)

    async def cleanup_inactive_chats(self, inactivity_days: int = 30) -> int:
        cutoff = datetime.utcnow().timestamp() - (inactivity_days * 86400)
        cutoff_str = datetime.utcfromtimestamp(cutoff).strftime("%Y-%m-%d %H:%M:%S")
        count_sql = """
            SELECT COUNT(DISTINCT chat_id) as count
            FROM chat_history
            WHERE created_at < ?
        """
        row = await self.fetchone(count_sql, (cutoff_str,))
        delete_sql = """
            DELETE FROM chat_history WHERE created_at < ?
        """
        await self.execute(delete_sql, (cutoff_str,))
        await self._conn.commit()
        return row["count"] if row else 0

    async def get_expiring_events(self, days_from_now: int) -> list[dict[str, Any]]:
        target = datetime.utcnow().timestamp() + (days_from_now * 86400)
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        next_day_str = datetime.utcfromtimestamp(target + 86400).strftime("%Y-%m-%d %H:%M:%S")
        sql = """
            SELECT * FROM events
            WHERE is_active = 1
                AND event_datetime >= ? AND event_datetime < ?
            ORDER BY event_datetime ASC
        """
        return await self.fetchall(sql, (now_str, next_day_str))

    async def get_all_chat_ids(self) -> list[int]:
        sql = """
            SELECT DISTINCT chat_id FROM chat_history
            UNION
            SELECT DISTINCT chat_id FROM events
            UNION
            SELECT DISTINCT chat_id FROM alerts
            UNION
            SELECT DISTINCT chat_id FROM finance_records
        """
        rows = await self.fetchall(sql)
        return [r["chat_id"] for r in rows]

    async def get_expiring_alerts(self, days_from_now: int) -> list[dict[str, Any]]:
        target = datetime.utcnow().timestamp() + (days_from_now * 86400)
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        next_day_str = datetime.utcfromtimestamp(target + 86400).strftime("%Y-%m-%d %H:%M:%S")
        sql = """
            SELECT * FROM alerts
            WHERE is_read = 0
                AND expires_at IS NOT NULL
                AND expires_at >= ? AND expires_at < ?
            ORDER BY expires_at ASC
        """
        return await self.fetchall(sql, (now_str, next_day_str))

    async def get_unread_alert_count(self, chat_id: int) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) as count FROM alerts WHERE chat_id = ? AND is_read = 0",
            (chat_id,),
        )
        return row["count"] if row else 0

    async def store_personal_knowledge(
        self, chat_id: int, key: str, value: str, category: str = "general"
    ) -> int:
        encrypted_value = encrypt_value(value.strip())
        sql = """
            INSERT INTO personal_knowledge (chat_id, key, value, category, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(chat_id, key) DO UPDATE SET
                value = excluded.value,
                category = excluded.category,
                updated_at = excluded.updated_at
        """
        cursor = await self.execute(sql, (chat_id, key.lower().strip(), encrypted_value, category))
        await self._conn.commit()
        return cursor.lastrowid

    async def search_personal_knowledge(
        self, chat_id: int, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        # El valor se guarda cifrado (Fernet), asi que un LIKE de SQL sobre
        # `value` jamas coincidia con texto plano: buscar por contenido
        # fallaba en silencio. Se filtra en Python tras descifrar (la tabla
        # personal es pequena).
        sql = """
            SELECT * FROM personal_knowledge
            WHERE chat_id = ?
            ORDER BY updated_at DESC
        """
        rows = await self.fetchall(sql, (chat_id,))
        needle = query.strip().lower()
        results = []
        for row in rows:
            row["value"] = decrypt_value(row["value"])
            if not needle or (
                needle in (row.get("key") or "").lower()
                or needle in (row.get("category") or "").lower()
                or needle in (row.get("value") or "").lower()
            ):
                results.append(row)
            if len(results) >= limit:
                break
        return results

    async def get_all_personal_knowledge(self, chat_id: int) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM personal_knowledge
            WHERE chat_id = ?
            ORDER BY category, key
        """
        rows = await self.fetchall(sql, (chat_id,))
        for row in rows:
            row["value"] = decrypt_value(row["value"])
        return rows

    async def delete_personal_knowledge(self, chat_id: int, key: str) -> bool:
        cursor = await self.execute(
            "DELETE FROM personal_knowledge WHERE chat_id = ? AND key = ?",
            (chat_id, key.lower().strip()),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def count_personal_knowledge(self, chat_id: int) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) as count FROM personal_knowledge WHERE chat_id = ?",
            (chat_id,),
        )
        return row["count"] if row else 0

    # ------------------------------------------------------------------
    # Grafo de conocimiento (mejora 8): relaciones tipadas
    # ------------------------------------------------------------------

    async def add_relation(
        self, chat_id: int, subject: str, predicate: str, obj: str, source: str = ""
    ) -> dict[str, Any]:
        subject = subject.strip()
        predicate = predicate.strip().lower()
        obj = obj.strip()
        if not subject or not predicate or not obj:
            return {"success": False, "message": "Sujeto, predicado y objeto son obligatorios."}
        await self.execute(
            """
            INSERT OR IGNORE INTO knowledge_relations
                (chat_id, subject, predicate, object, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chat_id, subject, predicate, obj, source),
        )
        await self._conn.commit()
        row = await self.fetchone(
            """
            SELECT * FROM knowledge_relations
            WHERE chat_id = ? AND subject = ? AND predicate = ? AND object = ?
            """,
            (chat_id, subject, predicate, obj),
        )
        return {"success": True, "relation": dict(row) if row else None}

    async def search_relations(
        self, chat_id: int, query: str = "", subject: str = ""
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM knowledge_relations WHERE chat_id = ?"
        params: list[Any] = [chat_id]
        if subject:
            sql += " AND subject = ?"
            params.append(subject.strip())
        needle = query.strip().lower()
        rows = await self.fetchall(sql + " ORDER BY subject, predicate, object", tuple(params))
        if not needle:
            return rows
        return [
            r
            for r in rows
            if needle in (r.get("subject") or "").lower()
            or needle in (r.get("predicate") or "").lower()
            or needle in (r.get("object") or "").lower()
            or needle in (r.get("source") or "").lower()
        ]

    async def delete_relation(self, chat_id: int, relation_id: int) -> bool:
        cursor = await self.execute(
            "DELETE FROM knowledge_relations WHERE chat_id = ? AND id = ?",
            (chat_id, relation_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # RGPD (mejora 7): exportar y borrar los datos de un usuario
    # ------------------------------------------------------------------

    _GDPR_TABLES = (
        ("chat_history", "chat_id"),
        ("events", "chat_id"),
        ("tasks", "chat_id"),
        ("alerts", "chat_id"),
        ("finance_records", "chat_id"),
        ("exports", "chat_id"),
        ("user_preferences", "chat_id"),
        ("personal_knowledge", "chat_id"),
        ("voice_sessions", "chat_id"),
        ("second_brain_log", "chat_id"),
        ("knowledge_relations", "chat_id"),
        ("credentials", "chat_id"),
        ("meetings", "user_id"),
        ("push_subscriptions", "user_id"),
    )

    # --- Ejecuciones de automatizaciones n8n (tareas.md, Fase 1) ---

    async def record_automation_run(
        self,
        execution_id: str,
        workflow: str,
        status: str,
        error: str | None = None,
        finished_at: str | None = None,
        severity: str = "",
    ) -> bool:
        """Registra una ejecucion de automatizacion.

        Idempotente por `execution_id` (UNIQUE): si n8n reintenta el
        informe, no se duplica la fila. Devuelve False si ya existia.
        `severity` usa la taxonomia unica (info/warning/error/critical);
        vacio se deriva de status (ok->info, error->error).
        """
        if not severity:
            severity = "info" if status == "ok" else "error"
        cursor = await self.execute(
            "INSERT OR IGNORE INTO automation_runs "
            "(execution_id, workflow, status, severity, error, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (execution_id, workflow, status, severity, error, finished_at),
        )
        # execute() no commitea: sin esto la fila queda en la transaccion
        # abierta y otros procesos (otras tools) no la ven (visto en vivo
        # 2026-10-04: automation_runs vacia pese a respuestas success:true).
        await self._conn.commit()
        return cursor.rowcount > 0

    async def list_automation_runs(
        self, days: int = 7, only_errors: bool = False, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Ejecuciones de los ultimos N dias (errores primero no: por fecha)."""
        sql = (
            "SELECT execution_id, workflow, status, severity, error, finished_at, created_at "
            "FROM automation_runs WHERE created_at >= datetime('now', ?)"
        )
        params: list[Any] = ["-%d days" % max(1, int(days))]
        if only_errors:
            sql += " AND status = 'error'"
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(min(max(1, int(limit)), 200))
        return await self.fetchall(sql, tuple(params))

    async def last_automation_run(self, workflow: str) -> dict[str, Any] | None:
        """Ultima ejecucion registrada de un workflow (para alerta 2 fallos)."""
        rows = await self.fetchall(
            "SELECT status, severity, created_at FROM automation_runs "
            "WHERE workflow = ? ORDER BY id DESC LIMIT 1",
            (workflow,),
        )
        return rows[0] if rows else None

    async def export_user_data(self, chat_id: int, *extra_ids: int) -> dict[str, Any]:
        """Exporta todos los datos del usuario en JSON (derecho de acceso).

        `extra_ids` cubre los espacios de id alternativos (p. ej. el usuario
        web guarda su chat en WEB_CHAT_BASE + id y sus reuniones en user_id).
        """
        ids: tuple[Any, ...] = (chat_id, *extra_ids)
        placeholders = ",".join("?" for _ in ids)
        data: dict[str, Any] = {"chat_id": chat_id, "exported_at": datetime.utcnow().isoformat()}
        for table, column in self._GDPR_TABLES:
            try:
                rows = await self.fetchall(
                    "SELECT * FROM %s WHERE %s IN (%s)" % (table, column, placeholders), ids
                )
            except Exception:
                rows = []
            if table == "personal_knowledge":
                for row in rows:
                    row["value"] = decrypt_value(row["value"])
            if rows:
                data[table] = [dict(r) for r in rows]
        return data

    async def delete_user_data(self, chat_id: int, *extra_ids: int) -> dict[str, int]:
        """Borra todos los datos del usuario (derecho de supresión)."""
        ids: tuple[Any, ...] = (chat_id, *extra_ids)
        placeholders = ",".join("?" for _ in ids)
        deleted: dict[str, int] = {}
        for table, column in self._GDPR_TABLES:
            try:
                cursor = await self.execute(
                    "DELETE FROM %s WHERE %s IN (%s)" % (table, column, placeholders), ids
                )
                deleted[table] = cursor.rowcount
            except Exception:
                deleted[table] = 0
        await self._conn.commit()
        return {k: v for k, v in deleted.items() if v}

    async def add_recurring_alert(
        self,
        chat_id: int,
        message: str,
        pattern: str = "daily",
        alert_type: str = "info",
        first_run: str | None = None,
    ) -> int:
        if first_run is None:
            first_run = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        sql = """
            INSERT INTO alerts (chat_id, message, alert_type, pattern, next_run)
            VALUES (?, ?, ?, ?, ?)
        """
        return await self.insert(sql, (chat_id, message, alert_type, pattern, first_run))

    async def get_due_recurring_alerts(self) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM alerts
            WHERE pattern IS NOT NULL
              AND next_run IS NOT NULL
              AND next_run <= datetime('now')
            ORDER BY next_run ASC
        """
        return await self.fetchall(sql)

    async def compute_next_run(self, pattern: str, current_run: str) -> str | None:
        try:
            dt = datetime.fromisoformat(current_run)
        except ValueError:
            try:
                dt = datetime.strptime(current_run, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
        if pattern == "daily":
            return (dt + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        elif pattern == "weekly":
            return (dt + timedelta(weeks=1)).strftime("%Y-%m-%d %H:%M:%S")
        elif pattern.startswith("every_"):
            try:
                hours = int(pattern.replace("every_", "").replace("_hours", "").replace("h", ""))
                return (dt + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
        elif pattern == "weekdays":
            next_dt = dt + timedelta(days=1)
            while next_dt.weekday() >= 5:
                next_dt += timedelta(days=1)
            return next_dt.strftime("%Y-%m-%d %H:%M:%S")
        elif pattern == "weekends":
            next_dt = dt + timedelta(days=1)
            while next_dt.weekday() < 5:
                next_dt += timedelta(days=1)
            return next_dt.strftime("%Y-%m-%d %H:%M:%S")
        return None

    async def update_alert_next_run(self, alert_id: int, next_run: str) -> None:
        await self.execute("UPDATE alerts SET next_run = ? WHERE id = ?", (next_run, alert_id))
        await self._conn.commit()

    async def get_events_missing_google_sync(self) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM events
            WHERE is_active = 1
              AND google_event_id IS NULL
              AND event_datetime >= datetime('now')
            ORDER BY event_datetime ASC
            LIMIT 50
        """
        return await self.fetchall(sql)

    async def update_event_google_id(self, event_id: int, google_event_id: str) -> None:
        await self.execute(
            "UPDATE events SET google_event_id = ? WHERE id = ?",
            (google_event_id, event_id),
        )
        await self._conn.commit()

    async def kv_set(self, key: str, value: str, expires_at: str | None = None) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO kv_store (key, value, expires_at) VALUES (?, ?, ?)",
            (key, value, expires_at),
        )
        await self._conn.commit()

    async def kv_get(self, key: str) -> str | None:
        row = await self.fetchone("SELECT value, expires_at FROM kv_store WHERE key = ?", (key,))
        if row is None:
            return None
        if row.get("expires_at"):
            try:
                from datetime import datetime as _dt

                exp = _dt.fromisoformat(row["expires_at"])
                if _dt.utcnow() > exp:
                    await self.execute("DELETE FROM kv_store WHERE key = ?", (key,))
                    await self._conn.commit()
                    return None
            except Exception:
                pass
        return row.get("value")

    async def kv_delete(self, key: str) -> None:
        await self.execute("DELETE FROM kv_store WHERE key = ?", (key,))
        await self._conn.commit()

    async def save_voice_session(
        self, session_id: str, chat_id: int, state: str, transcript: str
    ) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO voice_sessions (session_id, chat_id, state, transcript, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, datetime('now'), NULL)",
            (session_id, chat_id, state, transcript),
        )
        await self._conn.commit()

    async def update_voice_session_state(self, session_id: str, state: str) -> None:
        await self.execute(
            "UPDATE voice_sessions SET state = ? WHERE session_id = ?",
            (state, session_id),
        )
        await self._conn.commit()

    async def log_second_brain_query(
        self,
        query_text: str,
        chat_id: int | None,
        notes_found: list[str],
        chunks_retrieved: int,
        top_relevance: float,
    ) -> int:
        sql = """
            INSERT INTO second_brain_log (query_text, chat_id, notes_found, chunks_retrieved, top_relevance)
            VALUES (?, ?, ?, ?, ?)
        """
        return await self.insert(
            sql,
            (
                query_text[:300],
                chat_id,
                ",".join(notes_found[:10]) if notes_found else "",
                chunks_retrieved,
                top_relevance,
            ),
        )

    async def get_second_brain_stats(self) -> dict[str, Any]:
        total = await self.fetchone("SELECT COUNT(*) as c FROM second_brain_log")
        recent = await self.fetchall(
            "SELECT * FROM second_brain_log ORDER BY executed_at DESC LIMIT 10"
        )
        return {
            "total_queries": total["c"] if total else 0,
            "recent_queries": recent,
        }

    async def store_credential(self, chat_id: int, service: str, value: str) -> int:
        encrypted = encrypt_value(value)
        sql = """
            INSERT INTO credentials (chat_id, service, value_enc, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(chat_id, service) DO UPDATE SET
                value_enc = excluded.value_enc,
                updated_at = excluded.updated_at
        """
        cursor = await self.execute(sql, (chat_id, service.lower().strip(), encrypted))
        await self._conn.commit()
        return cursor.lastrowid

    async def get_credential(self, chat_id: int, service: str) -> str | None:
        sql = """
            SELECT value_enc FROM credentials
            WHERE chat_id = ? AND service = ?
        """
        row = await self.fetchone(sql, (chat_id, service.lower().strip()))
        if row is None:
            return None
        return decrypt_value(row["value_enc"])

    async def list_credentials(self, chat_id: int) -> list[dict[str, Any]]:
        sql = """
            SELECT id, service, created_at, updated_at FROM credentials
            WHERE chat_id = ?
            ORDER BY service
        """
        return await self.fetchall(sql, (chat_id,))

    async def delete_credential(self, chat_id: int, service: str) -> bool:
        cursor = await self.execute(
            "DELETE FROM credentials WHERE chat_id = ? AND service = ?",
            (chat_id, service.lower().strip()),
        )
        await self._conn.commit()
        return cursor.rowcount > 0
        total = await self.fetchone("SELECT COUNT(*) as c FROM second_brain_log")
        recent = await self.fetchall(
            "SELECT * FROM second_brain_log ORDER BY executed_at DESC LIMIT 10"
        )
        return {
            "total_queries": total["c"] if total else 0,
            "recent_queries": recent,
        }


db = DatabaseManager()
