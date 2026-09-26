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
from datetime import UTC, datetime
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
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/gmail.readonly",
]

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
        return True

    async def initialize(self, force: bool = False) -> bool:
        if self._ready and not force:
            return True
        loop = asyncio.get_running_loop()
        loaded = await loop.run_in_executor(None, self._load_service_account_sync)
        if not loaded:
            loaded = await self._load_oauth_sync()
        if not loaded:
            logger.info("GoogleServices: sin credenciales válidas en %s", CRED_DIR)
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
        if not end_datetime:
            from datetime import datetime, timedelta

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
        await self._run(
            lambda: self.tasks.tasks().delete(tasklist=tasklist, taskId=task_id), "borrar tarea"
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
