import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.logger import logger

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CRED_DIR = Path("/workspace/credentials")
SERVICE_ACCOUNT_FILE = CRED_DIR / "service_account.json"
OAUTH_CREDENTIALS_FILE = CRED_DIR / "credentials.json"
OAUTH_TOKEN_FILE = CRED_DIR / "token.json"


class GoogleCalendarManager:
    def __init__(self):
        self._service = None
        self._ready = False
        self._auth_method = None

    async def initialize(self) -> bool:
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, self._authenticate)
            self._ready = result
            if self._ready:
                logger.info("Google Calendar authenticated via %s", self._auth_method)
            else:
                logger.warning(
                    "Google Calendar not configured. "
                    "Place service_account.json or credentials.json in %s",
                    CRED_DIR,
                )
            return self._ready
        except Exception as e:
            logger.warning("Google Calendar init failed: %s", e)
            self._ready = False
            return False

    def _authenticate(self) -> bool:
        if SERVICE_ACCOUNT_FILE.exists():
            try:
                creds = service_account.Credentials.from_service_account_file(
                    str(SERVICE_ACCOUNT_FILE), scopes=SCOPES
                )
                self._service = build("calendar", "v3", credentials=creds)
                self._auth_method = "service_account"
                return True
            except Exception as e:
                logger.warning("Service account auth failed: %s", e)

        if OAUTH_CREDENTIALS_FILE.exists():
            try:
                creds = None
                if OAUTH_TOKEN_FILE.exists():
                    with open(OAUTH_TOKEN_FILE) as f:
                        creds = Credentials.from_authorized_user_file(str(OAUTH_TOKEN_FILE), SCOPES)
                if not creds or not creds.valid:
                    if creds and creds.expired and creds.refresh_token:
                        creds.refresh(Request())
                    else:
                        flow = InstalledAppFlow.from_client_secrets_file(
                            str(OAUTH_CREDENTIALS_FILE), SCOPES
                        )
                        creds = flow.run_local_server(port=0)
                    with open(OAUTH_TOKEN_FILE, "w") as f:
                        f.write(creds.to_json())
                self._service = build("calendar", "v3", credentials=creds)
                self._auth_method = "oauth"
                return True
            except Exception as e:
                logger.warning("OAuth auth failed: %s", e)

        return False

    async def add_event(
        self,
        title: str,
        start_datetime: str,
        end_datetime: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        if not self._ready or not self._service:
            return {
                "success": False,
                "message": "Google Calendar no configurado. Coloca credentials en /workspace/credentials/",
            }
        if not end_datetime:
            try:
                dt = datetime.fromisoformat(start_datetime)
                end_dt = dt + timedelta(hours=1)
                end_datetime = end_dt.isoformat()
            except ValueError:
                end_datetime = start_datetime
        event_body = {
            "summary": title,
            "description": description or "",
            "start": {
                "dateTime": start_datetime,
                "timeZone": "America/Mexico_City",
            },
            "end": {
                "dateTime": end_datetime,
                "timeZone": "America/Mexico_City",
            },
        }
        loop = asyncio.get_running_loop()

        def _do_insert():
            return self._service.events().insert(calendarId="primary", body=event_body).execute()

        try:
            event = await loop.run_in_executor(None, _do_insert)
            logger.info("Google Calendar event created: %s (%s)", title, event.get("id"))
            return {
                "success": True,
                "message": "Evento creado en Google Calendar: %s" % title,
                "event_id": event.get("id"),
                "html_link": event.get("htmlLink"),
            }
        except HttpError as e:
            logger.error("Google Calendar API error: %s", e)
            return {"success": False, "message": "Error de API de Google: %s" % e}
        except Exception as e:
            logger.exception("Google Calendar add_event error")
            return {"success": False, "message": "Error creando evento: %s" % e}

    async def list_upcoming_events(self, max_results: int = 10) -> list[dict[str, Any]]:
        if not self._ready or not self._service:
            return []
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

        try:
            events_result = await loop.run_in_executor(None, _do_list)
            events = events_result.get("items", [])
            return [
                {
                    "id": e.get("id"),
                    "title": e.get("summary", "Sin título"),
                    "start": e["start"].get("dateTime", e["start"].get("date")),
                    "end": e["end"].get("dateTime", e["end"].get("date")),
                    "description": e.get("description", ""),
                }
                for e in events
            ]
        except Exception as e:
            logger.error("Failed to list Google Calendar events: %s", e)
            return []

    async def delete_event(self, event_id: str) -> dict[str, Any]:
        if not self._ready or not self._service:
            return {"success": False, "message": "Google Calendar no configurado"}
        loop = asyncio.get_running_loop()

        def _do_delete():
            self._service.events().delete(calendarId="primary", eventId=event_id).execute()

        try:
            await loop.run_in_executor(None, _do_delete)
            return {"success": True, "message": "Evento eliminado de Google Calendar"}
        except HttpError as e:
            if e.resp.status == 410:
                return {"success": True, "message": "El evento ya no existe en Google Calendar"}
            return {"success": False, "message": "Error de API: %s" % e}
        except Exception as e:
            return {"success": False, "message": "Error eliminando evento: %s" % e}

    async def sync_from_local_db(self, db_module) -> dict[str, Any]:
        if not self._ready:
            return {"success": False, "message": "Google Calendar no configurado", "synced": 0}
        chat_ids = await db_module.get_all_chat_ids()
        synced = 0
        for chat_id in chat_ids:
            events = await db_module.get_upcoming_events(chat_id, limit=50)
            for ev in events:
                if ev.get("google_event_id"):
                    continue
                result = await self.add_event(
                    title=ev["title"],
                    start_datetime=ev["event_datetime"],
                    description=ev.get("description"),
                )
                if result.get("success") and result.get("event_id"):
                    await db_module._conn.execute(
                        "UPDATE events SET google_event_id = ? WHERE id = ?",
                        (result["event_id"], ev["id"]),
                    )
                    await db_module._conn.commit()
                    synced += 1
        logger.info("Google Calendar sync: %d events synced", synced)
        return {"success": True, "message": "Sincronizados %d eventos" % synced, "synced": synced}

    async def close(self) -> None:
        if self._service:
            self._service = None
            self._ready = False
            logger.info("Google Calendar client closed")


gcal = GoogleCalendarManager()
