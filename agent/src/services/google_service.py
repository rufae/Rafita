import asyncio
import json
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.database import db
from src.logger import logger
from src.utils.security_manager import decrypt_value, encrypt_value

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]

CRED_DIR = Path("/workspace/credentials")
OAUTH_CREDENTIALS_FILE = CRED_DIR / "credentials.json"


class GoogleService:
    def __init__(self):
        self._service = None
        self._creds: Credentials | None = None
        self._ready = False
        self._flow = None

    async def initialize(self) -> bool:
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
        logger.info("Google Service: token cifrado y guardado en SQLite (AES-256)")

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
            self._ready = True
            logger.info("Google Service: autenticacion completada y token almacenado")
            return {
                "success": True,
                "message": "Google conectado exitosamente. Ya puedo acceder a tu Calendar, Tasks y Drive.",
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
                        calendarId="primary",
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
            "start": {"dateTime": start_datetime, "timeZone": "America/Mexico_City"},
            "end": {"dateTime": end_datetime, "timeZone": "America/Mexico_City"},
        }
        try:
            loop = asyncio.get_running_loop()

            def _do_insert():
                return (
                    self._service.events().insert(calendarId="primary", body=event_body).execute()
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

    async def close(self) -> None:
        self._service = None
        self._creds = None
        self._ready = False


google_service = GoogleService()
