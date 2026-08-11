import json
from datetime import datetime
from typing import Any

import httpx

from src.database import db
from src.logger import logger
from src.utils.security_manager import decrypt_value, encrypt_value


class AppConnector:
    def __init__(self):
        self._http_client: httpx.AsyncClient | None = None
        self._connectors: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        self._http_client = httpx.AsyncClient(timeout=30.0)
        await self._load_connectors()
        logger.info("AppConnector initialized with %d connectors", len(self._connectors))

    async def close(self) -> None:
        if self._http_client:
            await self._http_client.aclose()

    async def _load_connectors(self) -> None:
        rows = await db.execute_fetchall(
            "SELECT name, connector_type, credentials_enc, config_json, is_active FROM app_connectors"
        )
        for row in rows:
            self._connectors[row["name"]] = {
                "type": row["connector_type"],
                "credentials": json.loads(decrypt_value(row["credentials_enc"])) if row["credentials_enc"] else {},
                "config": json.loads(row["config_json"]) if row["config_json"] else {},
                "is_active": bool(row["is_active"]),
            }

    async def register_connector(
        self,
        name: str,
        connector_type: str,
        credentials: dict[str, Any],
        config: dict[str, Any],
    ) -> int:
        cred_enc = encrypt_value(json.dumps(credentials, ensure_ascii=False))
        config_json = json.dumps(config, ensure_ascii=False)
        row = await db.execute_fetchone(
            "SELECT id FROM app_connectors WHERE name = ?", (name,)
        )
        if row:
            await db.execute(
                "UPDATE app_connectors SET connector_type=?, credentials_enc=?, config_json=?, is_active=1, updated_at=? WHERE name=?",
                (connector_type, cred_enc, config_json, datetime.utcnow().isoformat(), name),
            )
            logger.info("Connector '%s' updated", name)
            return row["id"]
        record_id = await db.execute_insert(
            "INSERT INTO app_connectors (name, connector_type, credentials_enc, config_json, is_active, created_at, updated_at) VALUES (?,?,?,?,1,?,?)",
            (name, connector_type, cred_enc, config_json, datetime.utcnow().isoformat(), datetime.utcnow().isoformat()),
        )
        self._connectors[name] = {
            "type": connector_type,
            "credentials": credentials,
            "config": config,
            "is_active": True,
        }
        logger.info("Connector '%s' registered (id=%s)", name, record_id)
        return record_id

    async def remove_connector(self, name: str) -> bool:
        await db.execute("DELETE FROM app_connectors WHERE name=?", (name,))
        if name in self._connectors:
            del self._connectors[name]
            logger.info("Connector '%s' removed", name)
            return True
        return False

    def list_connectors(self) -> list[dict[str, Any]]:
        result = []
        for name, info in self._connectors.items():
            result.append({
                "name": name,
                "type": info["type"],
                "is_active": info["is_active"],
                "config": info["config"],
            })
        return result

    def get_connector(self, name: str) -> dict[str, Any] | None:
        return self._connectors.get(name)

    async def test_gmail(self, credentials: dict[str, Any]) -> dict[str, Any]:
        try:
            headers = {"Authorization": "Bearer %s" % credentials.get("access_token", "")}
            resp = await self._http_client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                return {"success": True, "email": data.get("emailAddress", "unknown")}
            return {"success": False, "error": "Gmail API returned %d" % resp.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def fetch_urgent_emails(self, connector_name: str = "gmail") -> list[dict[str, Any]]:
        conn = self._connectors.get(connector_name)
        if not conn or conn["type"] != "gmail" or not conn["is_active"]:
            return []
        creds = conn["credentials"]
        headers = {"Authorization": "Bearer %s" % creds.get("access_token", "")}
        try:
            resp = await self._http_client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers=headers,
                params={"q": "is:important OR is:starred", "maxResults": 5},
            )
            if resp.status_code != 200:
                logger.warning("Gmail fetch failed: %d", resp.status_code)
                return []
            messages = resp.json().get("messages", [])
            results = []
            for msg_ref in messages[:5]:
                msg_id = msg_ref["id"]
                detail = await self._http_client.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages/%s" % msg_id,
                    headers=headers,
                    params={"format": "metadata", "metadataHeaders": ["Subject", "From"]},
                )
                if detail.status_code == 200:
                    msg_data = detail.json()
                    headers_list = msg_data.get("payload", {}).get("headers", [])
                    subject = next((h["value"] for h in headers_list if h["name"] == "Subject"), "")
                    sender = next((h["value"] for h in headers_list if h["name"] == "From"), "")
                    results.append({
                        "id": msg_id,
                        "subject": subject,
                        "from": sender,
                        "snippet": msg_data.get("snippet", ""),
                    })
            logger.info("Gmail: fetched %d urgent emails", len(results))
            return results
        except Exception as e:
            logger.warning("Gmail fetch error: %s", e)
            return []

    async def call_home_assistant(
        self, entity_id: str, action: str = "toggle", connector_name: str = "homeassistant"
    ) -> dict[str, Any]:
        conn = self._connectors.get(connector_name)
        if not conn or conn["type"] != "homeassistant" or not conn["is_active"]:
            return {"success": False, "error": "Home Assistant connector not configured"}
        creds = conn["credentials"]
        config = conn["config"]
        ha_url = config.get("url", "").rstrip("/")
        token = creds.get("token", "")
        headers = {
            "Authorization": "Bearer %s" % token,
            "Content-Type": "application/json",
        }
        domain = entity_id.split(".")[0] if "." in entity_id else "light"
        service = action if action in ("toggle", "turn_on", "turn_off") else "toggle"
        try:
            resp = await self._http_client.post(
                "%s/api/services/%s/%s" % (ha_url, domain, service),
                headers=headers,
                json={"entity_id": entity_id},
            )
            if resp.status_code in (200, 201):
                return {"success": True, "entity": entity_id, "action": service}
            return {"success": False, "error": "HA returned %d" % resp.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_home_assistant_state(
        self, entity_id: str = "", connector_name: str = "homeassistant"
    ) -> dict[str, Any]:
        conn = self._connectors.get(connector_name)
        if not conn or conn["type"] != "homeassistant" or not conn["is_active"]:
            return {"success": False, "error": "Home Assistant connector not configured"}
        creds = conn["credentials"]
        config = conn["config"]
        ha_url = config.get("url", "").rstrip("/")
        token = creds.get("token", "")
        headers = {"Authorization": "Bearer %s" % token}
        try:
            if entity_id:
                resp = await self._http_client.get(
                    "%s/api/states/%s" % (ha_url, entity_id),
                    headers=headers,
                )
            else:
                resp = await self._http_client.get(
                    "%s/api/states" % ha_url,
                    headers=headers,
                )
            if resp.status_code == 200:
                return {"success": True, "data": resp.json()}
            return {"success": False, "error": "HA returned %d" % resp.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}


connector = AppConnector()
