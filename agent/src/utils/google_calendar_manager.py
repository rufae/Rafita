import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from googleapiclient.errors import HttpError

from src.config import settings
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
        self._calendar_id = (settings.google_calendar_id or "primary").strip() or "primary"

    def _resolve_calendar_id(self, sa_email: str = "") -> str:
        """Detecta el calendario compartido si no hay uno configurado (3.8)."""
        configured = (settings.google_calendar_id or "").strip()
        if configured and configured != "primary":
            return configured
        try:
            items = self._service.calendarList().list().execute().get("items", [])
        except Exception as e:
            logger.warning("Google Calendar: no se pudo listar calendarios (%s)", e)
            return configured or "primary"
        candidates = [c for c in items if c.get("id") and c.get("id") != sa_email]
        for role in ("owner", "writer", "reader"):
            for cal in candidates:
                if cal.get("accessRole") == role:
                    logger.info(
                        "Google Calendar: calendario detectado '%s' (%s)",
                        cal.get("summary", cal.get("id")),
                        cal.get("accessRole"),
                    )
                    return str(cal.get("id"))
        if sa_email:
            logger.warning(
                "Google Calendar: comparte tu calendario con %s para poder usarlo",
                sa_email,
            )
        return configured or "primary"

    async def initialize(self) -> bool:
        """Autenticacion unificada: delega en GoogleServicesManager.

        Bug 2026-09-27: este modulo autenticaba por su cuenta y **preferia la
        cuenta de servicio** (calendario propio, vacio) mientras las lecturas
        del bot iban por OAuth -> los borrados/altas caian en otro calendario.
        """
        from src.services.google_services_manager import google_services

        try:
            self._ready = await google_services.initialize()
            if self._ready:
                self._service = google_services.calendar
                self._calendar_id = google_services.calendar_id
                self._auth_method = google_services.auth_method
                logger.info(
                    "Google Calendar: usando GoogleServicesManager (auth=%s, calendario=%s)",
                    self._auth_method,
                    self._calendar_id,
                )
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

    async def add_event(
        self,
        title: str,
        start_datetime: str,
        end_datetime: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        from src.services.google_services_manager import event_start_error

        start_error = event_start_error(start_datetime)
        if start_error:
            return {"success": False, "message": start_error}
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
                "timeZone": settings.timezone,
            },
            "end": {
                "dateTime": end_datetime,
                "timeZone": settings.timezone,
            },
        }
        loop = asyncio.get_running_loop()

        def _do_insert():
            return (
                self._service.events()
                .insert(calendarId=self._calendar_id, body=event_body)
                .execute()
            )

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
                    calendarId=self._calendar_id,
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
            self._service.events().delete(calendarId=self._calendar_id, eventId=event_id).execute()

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
