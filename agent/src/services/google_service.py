import asyncio
import json
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.config import settings
from src.database import db
from src.logger import logger
from src.utils.security_manager import decrypt_value, encrypt_value

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.readonly",
]

CRED_DIR = Path("/workspace/credentials")
OAUTH_CREDENTIALS_FILE = CRED_DIR / "credentials.json"
SERVICE_ACCOUNT_FILE = CRED_DIR / "service_account.json"


class GoogleService:
    def __init__(self):
        self._service = None
        self._drive = None
        self._creds: Credentials | None = None
        self._ready = False
        self._flow = None
        self._calendar_id = (settings.google_calendar_id or "primary").strip() or "primary"

    @property
    def calendar_id(self) -> str:
        return self._calendar_id

    def _resolve_calendar_id(self, sa_email: str = "") -> str:
        """Si no hay calendario configurado, detecta el compartido (task 3.8).

        Con cuenta de servicio, el calendario del usuario aparece en su
        calendarList tras compartirlo; se elige el primero que no sea el
        propio de la cuenta de servicio. Sin compartir nada, se mantiene
        'primary' y se registra un aviso claro.
        """
        configured = (settings.google_calendar_id or "").strip()
        if configured and configured != "primary":
            return configured
        try:
            items = self._service.calendarList().list().execute().get("items", [])
        except Exception as e:
            logger.warning("Google: no se pudo listar calendarios (%s)", e)
            return configured or "primary"
        candidates = [c for c in items if c.get("id") and c.get("id") != sa_email]
        for role in ("owner", "writer", "reader"):
            for cal in candidates:
                if cal.get("accessRole") == role:
                    logger.info(
                        "Google: calendario detectado '%s' (%s)",
                        cal.get("summary", cal.get("id")),
                        cal.get("accessRole"),
                    )
                    return str(cal.get("id"))
        if sa_email:
            logger.warning(
                "Google: no hay calendario compartido con %s; comparte tu calendario "
                "con ese email para que pueda leerlo/crearlo",
                sa_email,
            )
        return configured or "primary"

    async def initialize(self) -> bool:
        if SERVICE_ACCOUNT_FILE.exists():
            # Cuenta de servicio: no requiere autorizacion interactiva. El
            # calendario a usar debe estar compartido con el client_email.
            try:
                loop = asyncio.get_running_loop()

                def _load_service_account():
                    creds = service_account.Credentials.from_service_account_file(
                        str(SERVICE_ACCOUNT_FILE), scopes=SCOPES
                    )
                    return (
                        build("calendar", "v3", credentials=creds),
                        build("drive", "v3", credentials=creds),
                        creds.service_account_email,
                    )

                self._service, self._drive, sa_email = await loop.run_in_executor(
                    None, _load_service_account
                )
                stored_cal = await db.kv_get("google_calendar_id")
                if stored_cal:
                    self._calendar_id = stored_cal
                    logger.info("Google Service: calendario fijado: %s", stored_cal)
                else:
                    self._calendar_id = self._resolve_calendar_id(sa_email=sa_email)
                self._ready = True
                logger.info(
                    "Google Service: autenticado con cuenta de servicio (calendario=%s)",
                    self._calendar_id,
                )
                return True
            except Exception as e:
                logger.warning("Google Service: error con cuenta de servicio: %s", e)
                return False

        if not OAUTH_CREDENTIALS_FILE.exists():
            logger.info("Google Service: credentials.json no encontrado en %s", CRED_DIR)
            return False
        try:
            token_enc = await db.kv_get("google_token")
            if token_enc:
                token_json = decrypt_value(token_enc)
                token_data = json.loads(token_json)
                self._creds = Credentials.from_authorized_user_info(token_data, SCOPES)
                if self._creds.expired and self._creds.refresh_token:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self._creds.refresh, Request())
                    await self._save_token(self._creds)
                    logger.info("Google Service: token refrescado")
                elif not self._creds.valid:
                    self._creds = None
                    return False
                self._service = build("calendar", "v3", credentials=self._creds)
                self._drive = build("drive", "v3", credentials=self._creds)
                stored_cal = await db.kv_get("google_calendar_id")
                if stored_cal:
                    self._calendar_id = stored_cal
                self._ready = True
                logger.info("Google Service: autenticado desde token almacenado")
                return True
        except Exception as e:
            logger.warning("Google Service: error cargando token: %s", e)
            self._creds = None
        return False

    async def _save_token(self, creds: Credentials) -> None:
        token_json = creds.to_json()
        token_enc = encrypt_value(token_json)
        await db.kv_set("google_token", token_enc)
        logger.info("Google Service: token cifrado (Fernet) y guardado en SQLite")

    async def generate_auth_url(self) -> dict[str, Any]:
        if not OAUTH_CREDENTIALS_FILE.exists():
            return {
                "success": False,
                "message": "No se encontro credentials.json en /workspace/credentials/. "
                "Descarga el archivo JSON desde Google Cloud Console.",
            }
        try:
            loop = asyncio.get_running_loop()

            def _create_flow():
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(OAUTH_CREDENTIALS_FILE), SCOPES
                )
                flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
                auth_url, state = flow.authorization_url(
                    access_type="offline",
                    prompt="consent",
                )
                return flow, auth_url, state

            self._flow, auth_url, state = await loop.run_in_executor(None, _create_flow)
            await db.kv_set("google_flow_state", state)
            logger.info("Google Service: URL de autorizacion generada")
            return {
                "success": True,
                "auth_url": auth_url,
                "message": "Abre este enlace en tu navegador, autoriza la app y copia el codigo que te da Google.",
            }
        except Exception as e:
            logger.exception("Google Service: error generando auth URL")
            return {"success": False, "message": "Error generando URL: %s" % e}

    async def exchange_code(self, auth_code: str) -> dict[str, Any]:
        if not self._flow:
            stored_state = await db.kv_get("google_flow_state")
            if not stored_state:
                return {
                    "success": False,
                    "message": "No hay flujo OAuth activo. Genera el enlace de autorizacion primero.",
                }
            try:
                loop = asyncio.get_running_loop()

                def _recreate_flow():
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(OAUTH_CREDENTIALS_FILE), SCOPES
                    )
                    flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
                    return flow

                self._flow = await loop.run_in_executor(None, _recreate_flow)
            except Exception as e:
                return {"success": False, "message": "Error recreando flujo OAuth: %s" % e}

        try:
            loop = asyncio.get_running_loop()

            def _fetch_token():
                self._flow.fetch_token(code=auth_code)
                return self._flow.credentials

            creds = await loop.run_in_executor(None, _fetch_token)
            self._creds = creds
            await self._save_token(creds)
            self._service = build("calendar", "v3", credentials=creds)
            self._drive = build("drive", "v3", credentials=creds)
            stored_cal = await db.kv_get("google_calendar_id")
            if stored_cal:
                self._calendar_id = stored_cal
            self._ready = True
            logger.info("Google Service: autenticacion completada y token almacenado")
            return {
                "success": True,
                "message": "Google conectado exitosamente. Ya puedo acceder a tu Calendar.",
            }
        except Exception as e:
            logger.exception("Google Service: error intercambiando codigo")
            return {"success": False, "message": "Error de autenticacion: %s" % e}

    async def get_calendar_events(self, max_results: int = 10) -> dict[str, Any]:
        if not self._ready or not self._service:
            return {
                "success": False,
                "message": "No autenticado. Usa generate_google_auth_link para conectar tu cuenta de Google.",
                "needs_auth": True,
            }
        try:
            from datetime import datetime

            now = datetime.utcnow().isoformat() + "Z"
            loop = asyncio.get_running_loop()

            def _do_list():
                return (
                    self._service.events()
                    .list(
                        calendarId=self._calendar_id,
                        timeMin=now,
                        maxResults=max_results,
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )

            result = await loop.run_in_executor(None, _do_list)
            events = result.get("items", [])
            formatted = []
            for e in events:
                start = e["start"].get("dateTime", e["start"].get("date"))
                end = e["end"].get("dateTime", e["end"].get("date"))
                formatted.append(
                    {
                        "id": e.get("id"),
                        "title": e.get("summary", "Sin titulo"),
                        "start": start,
                        "end": end,
                        "description": e.get("description", ""),
                        "html_link": e.get("htmlLink", ""),
                    }
                )
            logger.info("Google Service: %d eventos obtenidos", len(formatted))
            return {"success": True, "events": formatted, "count": len(formatted)}
        except HttpError as e:
            logger.error("Google Service: HttpError %s", e)
            return {"success": False, "message": "Error de API de Google: %s" % e}
        except Exception as e:
            logger.exception("Google Service: error listando eventos")
            return {"success": False, "message": "Error: %s" % e}

    async def create_calendar_event(
        self,
        title: str,
        start_datetime: str,
        end_datetime: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        if not self._ready or not self._service:
            return {
                "success": False,
                "message": "No autenticado. Usa generate_google_auth_link para conectar.",
                "needs_auth": True,
            }
        if not end_datetime:
            try:
                from datetime import datetime, timedelta

                dt = datetime.fromisoformat(start_datetime)
                end_datetime = (dt + timedelta(hours=1)).isoformat()
            except ValueError:
                end_datetime = start_datetime

        event_body = {
            "summary": title,
            "description": description or "",
            "start": {"dateTime": start_datetime, "timeZone": settings.timezone},
            "end": {"dateTime": end_datetime, "timeZone": settings.timezone},
        }
        try:
            loop = asyncio.get_running_loop()

            def _do_insert():
                return (
                    self._service.events()
                    .insert(calendarId=self._calendar_id, body=event_body)
                    .execute()
                )

            event = await loop.run_in_executor(None, _do_insert)
            logger.info("Google Service: evento creado '%s' (%s)", title, event.get("id"))
            return {
                "success": True,
                "message": "Evento creado en Google Calendar: '%s' para %s"
                % (title, start_datetime),
                "event_id": event.get("id"),
                "html_link": event.get("htmlLink"),
            }
        except HttpError as e:
            return {"success": False, "message": "Error de API: %s" % e}
        except Exception as e:
            logger.exception("Google Service: error creando evento")
            return {"success": False, "message": "Error: %s" % e}

    async def set_calendar_id(self, calendar_id: str) -> dict[str, Any]:
        """Fija el calendario a usar (compartido con la cuenta de servicio).

        Se guarda en la base de datos (no en el .env) y se valida el acceso
        antes de aceptarlo.
        """
        cid = (calendar_id or "").strip()
        if not cid:
            return {
                "success": False,
                "message": "Indica el calendario: /calendario tu-correo@gmail.com",
            }
        if not self._ready or not self._service:
            return {"success": False, "message": "Google no está autenticado todavía."}
        try:
            loop = asyncio.get_running_loop()

            def _probe():
                info = self._service.calendars().get(calendarId=cid).execute()
                self._service.events().list(calendarId=cid, maxResults=1).execute()
                return info

            info = await loop.run_in_executor(None, _probe)
        except Exception as e:
            return {
                "success": False,
                "message": "No puedo acceder a '%s': %s\n\n¿Compartiste ese calendario "
                "con rafita@rafita-500317.iam.gserviceaccount.com con permiso "
                "«Hacer cambios en los eventos»?" % (cid, str(e)[:160]),
            }
        await db.kv_set("google_calendar_id", cid)
        self._calendar_id = cid
        logger.info("Google Service: calendario fijado a '%s'", cid)
        return {
            "success": True,
            "message": "Calendario configurado: '%s'. Ya puedo leer y crear eventos ahí."
            % info.get("summary", cid),
        }

    async def search_drive(self, query: str, max_results: int = 10) -> dict[str, Any]:
        """Busca ficheros en el Drive compartido con la cuenta (solo lectura)."""
        if not self._ready or not self._drive:
            return {"success": False, "message": "Google Drive no está disponible."}
        q = (query or "").strip()
        if not q:
            return {"success": False, "message": "Indica qué buscar (nombre o texto)."}
        escaped = q.replace("'", "\\'")
        drive_query = "(name contains '%s') or (fullText contains '%s')" % (escaped, escaped)
        try:
            loop = asyncio.get_running_loop()

            def _search():
                return (
                    self._drive.files()
                    .list(
                        q=drive_query,
                        pageSize=max(1, min(int(max_results), 25)),
                        fields="files(id,name,mimeType,modifiedTime,webViewLink,size)",
                        orderBy="modifiedTime desc",
                    )
                    .execute()
                )

            data = await loop.run_in_executor(None, _search)
        except Exception as e:
            return {"success": False, "message": "Error de Drive: %s" % str(e)[:200]}
        files = data.get("files", [])
        if not files:
            return {
                "success": True,
                "files": [],
                "message": "No encontré ficheros para '%s'." % q,
            }
        lines = ["🔎 Resultados en tu Drive:"]
        for f in files:
            lines.append("  • %s — id: %s" % (f.get("name", "?"), f.get("id", "?")))
        return {"success": True, "files": files, "message": "\n".join(lines)}

    async def read_drive_file(self, file_id: str, max_chars: int = 8000) -> dict[str, Any]:
        """Lee el texto de un fichero del Drive compartido (Docs, Sheets, PDF, texto)."""
        if not self._ready or not self._drive:
            return {"success": False, "message": "Google Drive no está disponible."}
        fid = (file_id or "").strip()
        if not fid:
            return {"success": False, "message": "Falta el id del fichero (usa antes la búsqueda)."}
        try:
            loop = asyncio.get_running_loop()

            def _fetch():
                meta = (
                    self._drive.files()
                    .get(fileId=fid, fields="id,name,mimeType,webViewLink,size")
                    .execute()
                )
                mime = meta.get("mimeType", "")
                if mime == "application/vnd.google-apps.document":
                    data = self._drive.files().export(fileId=fid, mimeType="text/plain").execute()
                elif mime == "application/vnd.google-apps.spreadsheet":
                    data = self._drive.files().export(fileId=fid, mimeType="text/csv").execute()
                elif mime == "application/vnd.google-apps.presentation":
                    data = self._drive.files().export(fileId=fid, mimeType="text/plain").execute()
                else:
                    data = self._drive.files().get_media(fileId=fid).execute()
                return meta, data

            meta, data = await loop.run_in_executor(None, _fetch)
        except Exception as e:
            return {"success": False, "message": "Error leyendo el fichero: %s" % str(e)[:200]}
        mime = meta.get("mimeType", "")
        if isinstance(data, bytes):
            if mime == "application/pdf":
                try:
                    import io as _io

                    from pypdf import PdfReader

                    reader = PdfReader(_io.BytesIO(data))
                    text = "\n".join((page.extract_text() or "") for page in reader.pages)
                except Exception as e:
                    return {"success": False, "message": "PDF no legible: %s" % str(e)[:150]}
            else:
                text = data.decode("utf-8", errors="replace")
        else:
            text = str(data)
        truncated = len(text) > max_chars
        return {
            "success": True,
            "name": meta.get("name", fid),
            "mime_type": mime,
            "link": meta.get("webViewLink", ""),
            "truncated": truncated,
            "text": text[:max_chars],
        }

    async def close(self) -> None:
        self._service = None
        self._drive = None
        self._creds = None
        self._ready = False


google_service = GoogleService()
