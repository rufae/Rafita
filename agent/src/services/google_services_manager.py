"""Módulo centralizado de servicios de Google (auditoría/refactor 2026-09-26).

Único punto de autenticación para Calendar, Drive, Sheets, Docs, Tasks y
Gmail. Soporta cuenta de servicio (preferida, con recursos compartidos) y
OAuth 2.0 con token almacenado (necesario para Tasks/Gmail, que no se pueden
compartir con una cuenta de servicio).

Características:
- Scopes de mínimo privilegio y ampliables por configuración.
- Manejo explícito de HttpError (401/403/404/429/5xx) con reintentos y
  backoff exponencial, y mensajes accionables para el usuario.
- Resolución del calendario: GOOGLE_CALENDAR_ID > override en BD > detección
  en calendarList > "primary".
- Carpeta destino opcional para creaciones (GOOGLE_DRIVE_FOLDER_ID).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.config import settings
from src.database import db
from src.logger import logger
from src.utils.security_manager import decrypt_value

CRED_DIR = Path("/workspace/credentials")
SERVICE_ACCOUNT_FILE = CRED_DIR / "service_account.json"
OAUTH_CREDENTIALS_FILE = CRED_DIR / "credentials.json"
OAUTH_TOKEN_FILE = CRED_DIR / "token.json"

# Mínimo privilegio. `drive.file` permite crear/borrar SOLO lo creado por la
# app (limpieza de pruebas y documentos propios); `drive.readonly` da lectura
# de lo compartido. Tasks/Gmail solo funcionan con OAuth (no se pueden
# compartir con una cuenta de servicio).
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/contacts.other.readonly",
    "https://www.googleapis.com/auth/fitness.activity.read",
]

WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}


def parse_relative_datetime(text: str, tz: ZoneInfo | None = None) -> datetime | None:
    """Interpreta fechas relativas en español (determinista, sin el modelo).

    Soporta: hoy, mañana, pasado mañana, días de la semana ("el lunes"),
    "en N días" y horas ("a las 10", "10:30"). Hora por defecto: 09:00.
    Devuelve None si no reconoce ninguna fecha.
    """
    if not text:
        return None
    t = text.lower().strip()
    if tz is None:
        try:
            tz = ZoneInfo(settings.timezone)
        except Exception:
            tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    target = None
    if "pasado mañana" in t or "pasado manana" in t:
        target = now.date() + timedelta(days=2)
    elif "mañana" in t or "manana" in t:
        target = now.date() + timedelta(days=1)
    elif "hoy" in t:
        target = now.date()
    else:
        import re

        match = re.search(r"en (\d+) d[ií]as?", t)
        if match:
            target = now.date() + timedelta(days=int(match.group(1)))
        else:
            for name, index in WEEKDAYS.items():
                if re.search(r"\b%s\b" % name, t):
                    delta = (index - now.weekday()) % 7
                    if delta == 0:
                        delta = 7
                    target = now.date() + timedelta(days=delta)
                    break
    if target is None:
        return None
    import re

    match = re.search(r"(\d{1,2})[:.](\d{2})", t)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
    else:
        match = re.search(r"a las (\d{1,2})", t)
        if match:
            hour, minute = int(match.group(1)), 0
        else:
            hour, minute = 9, 0
    if hour > 23 or minute > 59:
        hour, minute = 9, 0
    return datetime(target.year, target.month, target.day, hour, minute, tzinfo=tz)


SERVICE_NAMES = {
    "calendar": "Calendar API",
    "drive": "Drive API",
    "sheets": "Sheets API",
    "docs": "Docs API",
    "tasks": "Tasks API",
    "gmail": "Gmail API",
}


class GoogleServiceError(RuntimeError):
    """Error legible de un servicio de Google (con pista de acción)."""


def event_start_error(start_datetime: str) -> str | None:
    """Rechaza fechas en el pasado (evita fechas inventadas por el modelo).

    Devuelve un mensaje accionable con la fecha actual, o None si la fecha es
    válida o no se puede interpretar.
    """
    try:
        start = datetime.fromisoformat(start_datetime)
    except (ValueError, TypeError):
        return None
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception:
        tz = UTC
    now = datetime.now(tz)
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    if start < now - timedelta(minutes=5):
        return (
            "La fecha '%s' está en el pasado y HOY es %s (%s). Vuelve a calcular la "
            "fecha relativa ('mañana', 'el viernes'...) desde hoy y llama de nuevo a "
            "la herramienta."
            % (start_datetime, now.strftime("%A %d/%m/%Y %H:%M"), settings.timezone)
        )
    return None


def _humanize_http_error(exc: HttpError, action: str, sa_email: str = "") -> str:
    status = getattr(getattr(exc, "resp", None), "status", None)
    detail = str(exc)
    if "SERVICE_DISABLED" in detail or "has not been used in project" in detail:
        return (
            "La API necesaria no está habilitada en el proyecto de Google Cloud "
            "(acción: habilítala en APIs y servicios). Detalle: %s" % detail[:200]
        )
    if status == 401:
        return "Credenciales caducadas o revocadas (401). Reautoriza la conexión."
    if status == 403:
        if "insufficient" in detail.lower() or "forbidden" in detail.lower():
            return (
                "Sin permisos (403): comparte el recurso con %s (o revisa los "
                "scopes). Detalle: %s" % (sa_email or "la cuenta de servicio", detail[:200])
            )
        return "Permiso denegado (403) en %s: %s" % (action, detail[:200])
    if status == 404:
        return "No encontrado o no accesible (404) en %s: %s" % (action, detail[:200])
    if status == 429:
        return "Límite de peticiones alcanzado (429) en %s. Reintenta en unos segundos." % action
    return "Error de Google (%s) en %s: %s" % (status, action, detail[:250])


class GoogleServicesManager:
    def __init__(self) -> None:
        self._calendar = None
        self._drive = None
        self._sheets = None
        self._docs = None
        self._tasks = None
        self._gmail = None
        self._creds = None
        self._auth_method: str | None = None
        self._sa_email = ""
        self._ready = False
        self._calendar_id = (settings.google_calendar_id or "primary").strip() or "primary"

    # ------------------------------------------------------------------ auth
    def _sa_file(self) -> Path:
        env_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if env_path and Path(env_path).exists():
            return Path(env_path)
        return SERVICE_ACCOUNT_FILE

    def _build(self, service: str, version: str) -> Any:
        return build(service, version, credentials=self._creds, cache_discovery=False)

    def _load_service_account_sync(self) -> bool:
        sa_file = self._sa_file()
        if not sa_file.exists():
            return False
        self._creds = service_account.Credentials.from_service_account_file(
            str(sa_file), scopes=SCOPES
        )
        self._sa_email = getattr(self._creds, "service_account_email", "")
        self._auth_method = "service_account"
        return True

    async def _load_oauth_sync(self) -> bool:
        """Carga el token OAuth (BD cifrada o fichero) y refresca si caducó."""
        token_json = None
        try:
            token_enc = await db.kv_get("google_token")
            if token_enc:
                token_json = decrypt_value(token_enc)
        except Exception:
            token_json = None
        if token_json is None and OAUTH_TOKEN_FILE.exists():
            token_json = OAUTH_TOKEN_FILE.read_text(encoding="utf-8")
        if not token_json:
            return False
        try:
            self._creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        except Exception:
            return False
        loop = asyncio.get_running_loop()
        if self._creds.expired and self._creds.refresh_token:
            await loop.run_in_executor(None, self._creds.refresh, Request())
        if not self._creds.valid:
            return False
        self._auth_method = "oauth"
        self._sa_email = ""
        return True

    async def initialize(self, force: bool = False) -> bool:
        if self._ready and not force:
            return True
        loop = asyncio.get_running_loop()
        mode = (settings.google_auth_mode or "auto").strip().lower()
        loaded = False
        if mode in ("auto", "oauth"):
            # En auto, si ya hay token OAuth se prefiere (el usuario lo
            # autorizo explicitamente y da acceso completo).
            loaded = await self._load_oauth_sync()
            if loaded:
                self._auth_method = "oauth"
        if not loaded and mode in ("auto", "service_account"):
            loaded = await loop.run_in_executor(None, self._load_service_account_sync)
        if not loaded:
            logger.info("GoogleServices: sin credenciales válidas (modo=%s) en %s", mode, CRED_DIR)
            return False
        self._calendar = self._build("calendar", "v3")
        self._drive = self._build("drive", "v3")
        stored = await db.kv_get("google_calendar_id")
        if stored:
            self._calendar_id = stored
        elif self._auth_method == "service_account":
            self._calendar_id = self._resolve_calendar_id_sync()
        self._ready = True
        logger.info(
            "GoogleServices: autenticado por %s (%s), calendario=%s",
            self._auth_method,
            self._sa_email or "oauth",
            self._calendar_id,
        )
        return True

    # -------------------------------------------------------------- ejecucion
    def _execute(self, request: Callable[[], Any], action: str, retries: int = 3) -> Any:
        """Ejecuta una petición con backoff exponencial en 429/5xx."""
        delay = 1.0
        last_exc: HttpError | None = None
        for attempt in range(retries + 1):
            try:
                return request().execute()
            except HttpError as exc:
                last_exc = exc
                status = getattr(getattr(exc, "resp", None), "status", None)
                if status == 429 or (status is not None and 500 <= status < 600):
                    if attempt < retries:
                        time.sleep(delay)
                        delay *= 2
                        continue
                raise GoogleServiceError(_humanize_http_error(exc, action, self._sa_email)) from exc
        raise GoogleServiceError(
            _humanize_http_error(last_exc, action, self._sa_email)
            if last_exc
            else "Error desconocido en %s" % action
        )

    async def _run(self, fn: Callable[[], Any], action: str) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self._execute(fn, action))

    # -------------------------------------------------------------- servicios
    def _ensure(self, service: str) -> Any:
        if not self._ready:
            raise GoogleServiceError("Google no está autenticado (initialize() primero).")
        cached = getattr(self, f"_{service}", None)
        if cached is not None:
            return cached
        versions = {
            "sheets": ("sheets", "v4"),
            "docs": ("docs", "v1"),
            "tasks": ("tasks", "v1"),
            "gmail": ("gmail", "v1"),
            "people": ("people", "v1"),
            "fitness": ("fitness", "v1"),
        }
        if service not in versions:
            raise GoogleServiceError("Servicio desconocido: %s" % service)
        built = self._build(*versions[service])
        setattr(self, f"_{service}", built)
        return built

    @property
    def calendar(self) -> Any:
        return self._ensure("calendar")

    @property
    def drive(self) -> Any:
        return self._ensure("drive")

    @property
    def sheets(self) -> Any:
        return self._ensure("sheets")

    @property
    def docs(self) -> Any:
        return self._ensure("docs")

    @property
    def tasks(self) -> Any:
        return self._ensure("tasks")

    @property
    def gmail(self) -> Any:
        return self._ensure("gmail")

    @property
    def people(self) -> Any:
        return self._ensure("people")

    @property
    def fitness(self) -> Any:
        return self._ensure("fitness")

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def auth_method(self) -> str | None:
        return self._auth_method

    @property
    def service_account_email(self) -> str:
        return self._sa_email

    @property
    def calendar_id(self) -> str:
        return self._calendar_id

    @property
    def drive_folder_id(self) -> str:
        return (settings.google_drive_folder_id or "").strip()

    # -------------------------------------------------------------- calendario
    def _resolve_calendar_id_sync(self) -> str:
        configured = (settings.google_calendar_id or "").strip()
        if configured and configured != "primary":
            return configured
        try:
            items = self._execute(
                lambda: self._calendar.calendarList().list(), "listar calendarios"
            ).get("items", [])
        except Exception as exc:
            logger.warning("GoogleServices: no se pudo listar calendarios (%s)", exc)
            return configured or "primary"
        candidates = [c for c in items if c.get("id") and c.get("id") != self._sa_email]
        for role in ("owner", "writer", "reader"):
            for cal in candidates:
                if cal.get("accessRole") == role:
                    logger.info(
                        "GoogleServices: calendario detectado '%s' (%s)",
                        cal.get("summary", cal.get("id")),
                        cal.get("accessRole"),
                    )
                    return str(cal.get("id"))
        return configured or "primary"

    async def oauth_missing_scopes(self) -> list[str]:
        """Scopes requeridos que el token OAuth actual no tiene (para reautorizar)."""
        try:
            token_enc = await db.kv_get("google_token")
            if not token_enc:
                return list(SCOPES)
            data = json.loads(decrypt_value(token_enc))
            granted = set(data.get("scopes") or [])
            return [scope for scope in SCOPES if scope not in granted]
        except Exception:
            return list(SCOPES)

    async def set_calendar_id(self, calendar_id: str) -> dict[str, Any]:
        cid = (calendar_id or "").strip()
        if not cid:
            return {
                "success": False,
                "message": "Indica el calendario: /calendario tu-correo@gmail.com",
            }
        if not self._ready:
            await self.initialize()
        if not self._ready:
            return {"success": False, "message": "Google no está autenticado todavía."}
        try:
            info = await self._run(
                lambda: self._calendar.calendars().get(calendarId=cid), "leer calendario"
            )
            await self._run(
                lambda: self._calendar.events().list(calendarId=cid, maxResults=1),
                "listar eventos",
            )
        except GoogleServiceError as exc:
            return {"success": False, "message": str(exc)}
        await db.kv_set("google_calendar_id", cid)
        self._calendar_id = cid
        logger.info("GoogleServices: calendario fijado a '%s'", cid)
        return {
            "success": True,
            "message": "Calendario configurado: '%s'. Ya puedo leer y crear eventos ahí."
            % info.get("summary", cid),
        }

    @staticmethod
    def _normalize_time(value: str) -> str:
        """Google exige RFC3339 con zona; añade la local si falta (bug 400)."""
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return value
        if dt.tzinfo is None:
            try:
                dt = dt.replace(tzinfo=ZoneInfo(settings.timezone))
            except Exception:
                dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()

    async def list_events(self, max_results: int = 10, time_min: str | None = None) -> list[dict]:
        params: dict[str, Any] = {
            "calendarId": self._calendar_id,
            "maxResults": max(1, min(int(max_results), 50)),
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if time_min:
            params["timeMin"] = self._normalize_time(time_min)
        data = await self._run(lambda: self._calendar.events().list(**params), "listar eventos")
        return data.get("items", [])

    async def create_event(
        self,
        title: str,
        start_datetime: str,
        end_datetime: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        start_error = event_start_error(start_datetime)
        if start_error:
            return {"success": False, "message": start_error}
        if not end_datetime:
            try:
                end_datetime = (
                    datetime.fromisoformat(start_datetime) + timedelta(hours=1)
                ).isoformat()
            except ValueError:
                end_datetime = start_datetime
        body = {
            "summary": title,
            "description": description or "",
            "start": {"dateTime": start_datetime, "timeZone": settings.timezone},
            "end": {"dateTime": end_datetime, "timeZone": settings.timezone},
        }
        event = await self._run(
            lambda: self._calendar.events().insert(calendarId=self._calendar_id, body=body),
            "crear evento",
        )
        return {
            "success": True,
            "message": "Evento creado: '%s' para %s" % (title, start_datetime),
            "event_id": event.get("id"),
            "html_link": event.get("htmlLink"),
        }

    async def delete_event(self, event_id: str) -> dict[str, Any]:
        await self._run(
            lambda: self._calendar.events().delete(calendarId=self._calendar_id, eventId=event_id),
            "borrar evento",
        )
        return {"success": True, "message": "Evento borrado (%s)" % event_id}

    # ------------------------------------------------------------------ drive
    async def search_files(
        self, query: str, max_results: int = 10, folder_id: str | None = None
    ) -> list[dict]:
        escaped = (query or "").replace("'", "\\'")
        clauses = ["(name contains '%s') or (fullText contains '%s')" % (escaped, escaped)]
        folder = folder_id or self.drive_folder_id
        if folder:
            clauses.append("'%s' in parents" % folder)
        data = await self._run(
            lambda: self.drive.files().list(
                q=" and ".join(clauses),
                pageSize=max(1, min(int(max_results), 50)),
                fields="files(id,name,mimeType,modifiedTime,webViewLink,size,parents)",
                orderBy="modifiedTime desc",
            ),
            "buscar en Drive",
        )
        return data.get("files", [])

    async def read_file(self, file_id: str, max_chars: int = 8000) -> dict[str, Any]:
        fid = (file_id or "").strip()
        if not fid:
            return {"success": False, "message": "Falta el id del fichero."}
        meta = await self._run(
            lambda: self.drive.files().get(fileId=fid, fields="id,name,mimeType,webViewLink,size"),
            "leer metadatos",
        )
        mime = meta.get("mimeType", "")
        exports = {
            "application/vnd.google-apps.document": "text/plain",
            "application/vnd.google-apps.spreadsheet": "text/csv",
            "application/vnd.google-apps.presentation": "text/plain",
        }
        if mime in exports:
            data = await self._run(
                lambda: self.drive.files().export(fileId=fid, mimeType=exports[mime]),
                "exportar fichero",
            )
        else:
            data = await self._run(
                lambda: self.drive.files().get_media(fileId=fid), "descargar fichero"
            )
        if isinstance(data, bytes):
            if mime == "application/pdf":
                try:
                    import io as _io

                    from pypdf import PdfReader

                    reader = PdfReader(_io.BytesIO(data))
                    text = "\n".join((page.extract_text() or "") for page in reader.pages)
                except Exception as exc:
                    return {"success": False, "message": "PDF no legible: %s" % str(exc)[:150]}
            else:
                text = data.decode("utf-8", errors="replace")
        else:
            text = str(data)
        return {
            "success": True,
            "name": meta.get("name", fid),
            "mime_type": mime,
            "link": meta.get("webViewLink", ""),
            "truncated": len(text) > max_chars,
            "text": text[:max_chars],
        }

    async def delete_file(self, file_id: str) -> dict[str, Any]:
        """Borra un fichero creado por la app (scope drive.file)."""
        await self._run(lambda: self.drive.files().delete(fileId=file_id), "borrar fichero")
        return {"success": True, "message": "Fichero borrado (%s)" % file_id}

    # ----------------------------------------------------------------- sheets
    async def create_spreadsheet(self, title: str, sheet_name: str = "Hoja 1") -> dict[str, Any]:
        body: dict[str, Any] = {"properties": {"title": title}}
        folder = self.drive_folder_id
        if folder:
            body["parents"] = [folder]
        data = await self._run(
            lambda: self.sheets.spreadsheets().create(body=body), "crear hoja de cálculo"
        )
        return {
            "success": True,
            "id": data.get("spreadsheetId"),
            "url": data.get("spreadsheetUrl"),
            "sheet": data.get("sheets", [{}])[0].get("properties", {}).get("title", sheet_name),
        }

    async def read_range(self, spreadsheet_id: str, cell_range: str = "A1:Z100") -> dict[str, Any]:
        data = await self._run(
            lambda: (
                self.sheets.spreadsheets()
                .values()
                .get(spreadsheetId=spreadsheet_id, range=cell_range)
            ),
            "leer rango",
        )
        return {"success": True, "values": data.get("values", [])}

    async def append_row(
        self, spreadsheet_id: str, values: list[Any], sheet_name: str = "Hoja 1"
    ) -> dict[str, Any]:
        data = await self._run(
            lambda: (
                self.sheets.spreadsheets()
                .values()
                .append(
                    spreadsheetId=spreadsheet_id,
                    range="%s!A1" % sheet_name,
                    valueInputOption="USER_ENTERED",
                    body={"values": [values]},
                )
            ),
            "añadir fila",
        )
        return {"success": True, "updated": data.get("updates", {}).get("updatedRows", 0)}

    # ------------------------------------------------------------------- docs
    async def create_document(self, title: str) -> dict[str, Any]:
        body: dict[str, Any] = {"title": title}
        folder = self.drive_folder_id
        if folder:
            body["parents"] = [folder]
        data = await self._run(lambda: self.docs.documents().create(body=body), "crear documento")
        return {
            "success": True,
            "id": data.get("documentId"),
            "title": data.get("title", title),
        }

    async def read_document(self, document_id: str, max_chars: int = 8000) -> dict[str, Any]:
        data = await self._run(
            lambda: self.docs.documents().get(documentId=document_id), "leer documento"
        )
        chunks: list[str] = []
        for element in data.get("body", {}).get("content", []):
            paragraph = element.get("paragraph")
            if not paragraph:
                continue
            for run in paragraph.get("elements", []):
                text = run.get("textRun", {}).get("content", "")
                if text:
                    chunks.append(text)
        text = "".join(chunks)
        return {"success": True, "text": text[:max_chars], "truncated": len(text) > max_chars}

    async def append_document_text(self, document_id: str, text: str) -> dict[str, Any]:
        data = await self._run(
            lambda: self.docs.documents().get(documentId=document_id), "leer documento"
        )
        end_index = data.get("body", {}).get("content", [{}])[-1].get("endIndex", 1)
        await self._run(
            lambda: self.docs.documents().batchUpdate(
                documentId=document_id,
                body={
                    "requests": [
                        {"insertText": {"location": {"index": max(1, end_index - 1)}, "text": text}}
                    ]
                },
            ),
            "escribir documento",
        )
        return {"success": True, "message": "Texto añadido al documento"}

    # ------------------------------------------------------------- tasks/gmail
    async def list_tasklists(self) -> list[dict]:
        data = await self._run(lambda: self.tasks.tasklists().list(), "listar listas de tareas")
        return data.get("items", [])

    async def create_task(self, title: str, tasklist: str = "@default") -> dict[str, Any]:
        data = await self._run(
            lambda: self.tasks.tasks().insert(tasklist=tasklist, body={"title": title}),
            "crear tarea",
        )
        return {"success": True, "id": data.get("id"), "title": data.get("title", title)}

    async def delete_task(self, task_id: str, tasklist: str = "@default") -> dict[str, Any]:
        # La API espera el parámetro `task` (no `taskId`) — bug detectado por el E2E.
        await self._run(
            lambda: self.tasks.tasks().delete(tasklist=tasklist, task=task_id), "borrar tarea"
        )
        return {"success": True, "message": "Tarea borrada"}

    async def gmail_profile(self) -> dict[str, Any]:
        data = await self._run(
            lambda: self.gmail.users().getProfile(userId="me"), "leer perfil de Gmail"
        )
        return {
            "success": True,
            "email": data.get("emailAddress"),
            "messages": data.get("messagesTotal"),
        }

    async def search_gmail(self, query: str, max_results: int = 5) -> dict[str, Any]:
        """Busca correos (solo metadatos: asunto, remitente, fecha, extracto).

        Si la consulta no especifica ubicacion (`in:`, `label:`, `category:`),
        se limita a la **bandeja principal** (`in:inbox category:primary`),
        excluyendo Promociones/Social/Notificaciones/Foros (peticion del
        usuario 2026-09-27).
        """
        import re

        limit = max(1, min(int(max_results), 10))
        q = (query or "").strip()
        if not re.search(r"\b(in|label|category):", q):
            q = ("in:inbox category:primary " + q).strip()
        data = await self._run(
            lambda: self.gmail.users().messages().list(userId="me", q=q, maxResults=limit),
            "buscar correos",
        )
        messages = []
        for ref in data.get("messages", []):
            meta = await self._run(
                lambda mid=ref["id"]: (
                    self.gmail.users()
                    .messages()
                    .get(
                        userId="me",
                        id=mid,
                        format="metadata",
                        metadataHeaders=["Subject", "From", "Date"],
                    )
                ),
                "leer correo",
            )
            headers = {
                h.get("name", "").lower(): h.get("value", "")
                for h in meta.get("payload", {}).get("headers", [])
            }
            messages.append(
                {
                    "subject": headers.get("subject", "(sin asunto)"),
                    "from": headers.get("from", ""),
                    "date": headers.get("date", ""),
                    "snippet": (meta.get("snippet") or "")[:200],
                }
            )
        return {"success": True, "messages": messages, "count": len(messages)}

    async def list_tasks(
        self, show_completed: bool = False, max_results: int = 20
    ) -> dict[str, Any]:
        data = await self._run(
            lambda: self.tasks.tasks().list(
                tasklist="@default",
                showCompleted=show_completed,
                maxResults=max(1, min(int(max_results), 50)),
            ),
            "listar tareas",
        )
        tasks = [
            {
                "id": item.get("id"),
                "title": item.get("title", ""),
                "due": item.get("due", ""),
                "status": item.get("status", ""),
            }
            for item in data.get("items", [])
        ]
        return {"success": True, "tasks": tasks}

    async def complete_task(self, task_id: str, tasklist: str = "@default") -> dict[str, Any]:
        await self._run(
            lambda: self.tasks.tasks().patch(
                tasklist=tasklist, task=task_id, body={"status": "completed"}
            ),
            "completar tarea",
        )
        return {"success": True, "message": "Tarea completada"}

    @staticmethod
    def _normalize_match(text: str) -> str:
        """Minusculas y sin acentos para comparar (mama == mamá)."""
        import unicodedata

        decomposed = unicodedata.normalize("NFKD", (text or "").lower())
        return "".join(c for c in decomposed if not unicodedata.combining(c))

    async def find_contact(self, query: str, max_results: int = 5) -> dict[str, Any]:
        """Busca contactos por nombre, correo o telefono (People API).

        Comparacion sin acentos ni mayusculas (bug 2026-09-27: 'mama' no
        encontraba el contacto 'Aa Mama').
        """
        data = await self._run(
            lambda: (
                self.people.people()
                .connections()
                .list(
                    resourceName="people/me",
                    pageSize=200,
                    personFields="names,emailAddresses,phoneNumbers",
                )
            ),
            "buscar contactos",
        )
        needle = self._normalize_match((query or "").strip())
        found = []
        for person in data.get("connections", []):
            names = person.get("names", [{}])
            name = names[0].get("displayName", "") if names else ""
            emails = person.get("emailAddresses", [{}])
            email = emails[0].get("value", "") if emails else ""
            phones = person.get("phoneNumbers", [{}])
            phone = phones[0].get("value", "") if phones else ""
            haystack = self._normalize_match("%s %s %s" % (name, email, phone))
            if not needle or needle in haystack:
                found.append({"name": name, "email": email, "phone": phone})
            if len(found) >= max(1, int(max_results)):
                break
        if not found and needle:
            # Fallback: "Otros contactos" (personas de correos, no guardadas)
            try:
                extra = await self._run(
                    lambda: self.people.otherContacts().search(
                        query=needle, readMask="names,emailAddresses,phoneNumbers"
                    ),
                    "buscar otros contactos",
                )
                for person in extra.get("results", []):
                    names = person.get("names", [{}])
                    name = names[0].get("displayName", "") if names else ""
                    emails = person.get("emailAddresses", [{}])
                    email = emails[0].get("value", "") if emails else ""
                    phones = person.get("phoneNumbers", [{}])
                    phone = phones[0].get("value", "") if phones else ""
                    found.append({"name": name, "email": email, "phone": phone})
                    if len(found) >= max(1, int(max_results)):
                        break
            except Exception as e:
                logger.info("GoogleServices: otros contactos no disponibles (%s)", str(e)[:120])
        return {"success": True, "contacts": found}

    async def list_drive(
        self, kind: str = "all", max_results: int = 20, folder_id: str | None = None
    ) -> dict[str, Any]:
        """Lista ficheros y/o carpetas del Drive (bug 2026-09-27: al pedir
        carpetas devolvia todo mezclado)."""
        clauses = []
        if kind == "folders":
            clauses.append("mimeType = 'application/vnd.google-apps.folder'")
        elif kind == "files":
            clauses.append("mimeType != 'application/vnd.google-apps.folder'")
        folder = folder_id or self.drive_folder_id
        if folder:
            clauses.append("'%s' in parents" % folder)
        params: dict[str, Any] = {
            "pageSize": max(1, min(int(max_results), 50)),
            "fields": "files(id,name,mimeType,modifiedTime,webViewLink,size)",
            "orderBy": "modifiedTime desc",
        }
        if clauses:
            params["q"] = " and ".join(clauses)
        data = await self._run(lambda: self.drive.files().list(**params), "listar Drive")
        return {"success": True, "files": data.get("files", [])}

    async def list_contacts(self, page_size: int = 10) -> dict[str, Any]:
        data = await self._run(
            lambda: (
                self.people.people()
                .connections()
                .list(
                    resourceName="people/me",
                    pageSize=max(1, min(int(page_size), 50)),
                    personFields="names,emailAddresses,phoneNumbers",
                )
            ),
            "listar contactos",
        )
        contacts = []
        for person in data.get("connections", []):
            names = person.get("names", [{}])
            emails = person.get("emailAddresses", [{}])
            phones = person.get("phoneNumbers", [{}])
            contacts.append(
                {
                    "name": names[0].get("displayName", "") if names else "",
                    "email": emails[0].get("value", "") if emails else "",
                    "phone": phones[0].get("value", "") if phones else "",
                }
            )
        return {"success": True, "contacts": contacts}

    async def fitness_daily_steps(self) -> dict[str, Any]:
        """Pasos de hoy (Fitness API, requiere OAuth)."""
        tz = ZoneInfo(settings.timezone)
        now = datetime.now(tz)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        body = {
            "aggregateBy": [{"dataTypeName": "com.google.step_count.delta"}],
            "bucketByTime": {"durationMillis": 86400000},
            "startTimeMillis": int(start.timestamp() * 1000),
            "endTimeMillis": int(now.timestamp() * 1000),
        }
        data = await self._run(
            lambda: self.fitness.users().dataset().aggregate(userId="me", body=body),
            "leer pasos",
        )
        steps = 0
        for bucket in data.get("bucket", []):
            for dataset in bucket.get("dataset", []):
                for point in dataset.get("point", []):
                    for value in point.get("value", []):
                        steps += int(value.get("intVal", 0))
        return {"success": True, "date": start.date().isoformat(), "steps": steps}

    # --------------------------------------------------------------- estado
    async def status(self) -> dict[str, Any]:
        """Diagnóstico de cada servicio (para el script E2E y el panel)."""
        if not self._ready:
            await self.initialize()
        result: dict[str, Any] = {
            "authenticated": self._ready,
            "auth_method": self._auth_method,
            "service_account": self._sa_email,
            "calendar_id": self._calendar_id,
            "drive_folder_id": self.drive_folder_id or None,
            "services": {},
        }
        probes: dict[str, tuple[str, Callable[[], Any]]] = {
            "calendar": (
                "listar calendarios",
                lambda: self.calendar.calendarList().list(maxResults=1),
            ),
            "drive": (
                "listar ficheros",
                lambda: self.drive.files().list(pageSize=1, fields="files(id)"),
            ),
            "sheets": (
                "leer hoja inexistente",
                lambda: self.sheets.spreadsheets().get(spreadsheetId="invalid-probe-id"),
            ),
            "docs": (
                "leer documento inexistente",
                lambda: self.docs.documents().get(documentId="invalid-probe-id"),
            ),
            "tasks": ("listar listas", lambda: self.tasks.tasklists().list()),
            "gmail": ("perfil", lambda: self.gmail.users().getProfile(userId="me")),
        }
        for name, (action, probe) in probes.items():
            if self._auth_method == "service_account" and name in (
                "tasks",
                "gmail",
                "people",
                "fitness",
            ):
                # Con cuenta de servicio estas APIs no aplican al usuario
                # (Tasks no se comparte, Gmail no tiene buzon, etc.).
                result["services"][name] = "requires_oauth"
                continue
            try:
                await self._run(probe, action)
                result["services"][name] = "ok"
            except GoogleServiceError as exc:
                msg = str(exc)
                if "no está habilitada" in msg:
                    result["services"][name] = "api_disabled"
                elif "(404)" in msg:
                    result["services"][name] = "ok"  # la API responde (el id era falso)
                elif "(403)" in msg:
                    result["services"][name] = "forbidden"
                elif "(401)" in msg:
                    result["services"][name] = "unauthorized"
                else:
                    result["services"][name] = "error: %s" % msg[:120]
            except Exception as exc:  # servicio no disponible con estas credenciales
                result["services"][name] = "unavailable: %s" % str(exc)[:120]
        return result

    async def close(self) -> None:
        self._calendar = None
        self._drive = None
        self._sheets = None
        self._docs = None
        self._tasks = None
        self._gmail = None
        self._creds = None
        self._ready = False


google_services = GoogleServicesManager()
