#!/usr/bin/env python3
"""Test E2E de las integraciones de Google (auditoría 2026-09-26).

Uso (dentro del contenedor del agente):
    python /workspace/agent/scripts/test_google_services.py [--calendar-id X] [--keep]

Pruebas (crean y borran recursos de prueba salvo --keep):
  1. Diagnóstico de los 6 servicios (Calendar, Drive, Sheets, Docs, Tasks, Gmail)
  2. Calendar: crear "Test Audit Google Services" (mañana 09:00), listar, borrar
  3. Drive: listar 5 ficheros accesibles
  4. Sheets: crear hoja, escribir fila, leerla, borrar
  5. Docs: crear documento, escribir frase, leerlo, borrar
  6. Tasks/Gmail: solo con OAuth (las cuentas de servicio no pueden compartir
     tareas ni tienen buzón de Gmail)
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.config import settings
from src.database import db
from src.services.google_services_manager import google_services

OK = "[OK]  "
FAIL = "[FAIL]"
SKIP = "[SKIP]"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Test E2E de servicios de Google")
    parser.add_argument("--calendar-id", default="", help="fija el calendario antes de probar")
    parser.add_argument("--keep", action="store_true", help="no borrar los recursos de prueba")
    args = parser.parse_args()

    await db.initialize()
    if not await google_services.initialize():
        print(FAIL, "autenticación: no hay credenciales válidas en /workspace/credentials/")
        return 1
    if args.calendar_id:
        result = await google_services.set_calendar_id(args.calendar_id)
        print(OK if result.get("success") else FAIL, "calendario:", result.get("message"))

    failures = 0

    print("\n=== 1. Diagnóstico de servicios ===")
    status = await google_services.status()
    print(
        "auth=%s | cuenta=%s | calendario=%s | carpeta_drive=%s"
        % (
            status["auth_method"],
            status["service_account"] or "oauth",
            status["calendar_id"],
            status["drive_folder_id"],
        )
    )
    for name, state in status["services"].items():
        mark = OK if state in ("ok",) else SKIP
        print(mark, "%-8s %s" % (name, state))

    print("\n=== 2. Calendar ===")
    event_id = None
    try:
        tz = ZoneInfo(settings.timezone)
        start = (datetime.now(tz) + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        end = start + timedelta(hours=1)
        event = await google_services.create_event(
            "Test Audit Google Services", start.isoformat(), end.isoformat()
        )
        event_id = event["event_id"]
        print(OK, "crear evento:", event_id)
        events = await google_services.list_events(
            max_results=25, time_min=(start - timedelta(days=1)).isoformat()
        )
        found = any(e.get("id") == event_id for e in events)
        print(
            OK if found else FAIL, "listar eventos: %d, creado presente=%s" % (len(events), found)
        )
        if not found:
            failures += 1
    except Exception as exc:
        failures += 1
        print(FAIL, "calendar:", exc)
    finally:
        if event_id and not args.keep:
            try:
                await google_services.delete_event(event_id)
                print(OK, "borrar evento")
            except Exception as exc:
                failures += 1
                print(FAIL, "borrar evento:", exc)

    print("\n=== 3. Drive ===")
    try:
        data = await google_services._run(
            lambda: google_services.drive.files().list(
                pageSize=5, fields="files(id,name,mimeType)"
            ),
            "listar ficheros",
        )
        files = data.get("files", [])
        print(OK, "ficheros accesibles:", len(files))
        for f in files:
            print("       - %s (%s)" % (f.get("name"), f.get("mimeType")))
    except Exception as exc:
        failures += 1
        print(FAIL, "drive:", exc)

    print("\n=== 4. Sheets ===")
    sheet_id = None
    try:
        sheet = await google_services.create_spreadsheet("Test Audit Google Services (borrar)")
        sheet_id = sheet["id"]
        print(OK, "crear hoja:", sheet_id)
        await google_services.append_row(sheet_id, ["prueba", 42, "auditoría"])
        print(OK, "escribir fila")
        read = await google_services.read_range(sheet_id, "A1:Z10")
        rows = read.get("values", [])
        print(OK if rows else FAIL, "leer rango: %d filas" % len(rows))
        if not rows:
            failures += 1
    except Exception as exc:
        failures += 1
        print(FAIL, "sheets:", exc)
    finally:
        if sheet_id and not args.keep:
            try:
                await google_services.delete_file(sheet_id)
                print(OK, "borrar hoja")
            except Exception as exc:
                failures += 1
                print(FAIL, "borrar hoja:", exc)

    print("\n=== 5. Docs ===")
    doc_id = None
    try:
        doc = await google_services.create_document("Test Audit Google Services (borrar)")
        doc_id = doc["id"]
        print(OK, "crear documento:", doc_id)
        await google_services.append_document_text(doc_id, "Frase de prueba de la auditoría.\n")
        print(OK, "escribir frase")
        content = await google_services.read_document(doc_id)
        has_text = "auditoría" in content.get("text", "")
        print(
            OK if has_text else FAIL, "leer documento: %d caracteres" % len(content.get("text", ""))
        )
        if not has_text:
            failures += 1
    except Exception as exc:
        failures += 1
        print(FAIL, "docs:", exc)
    finally:
        if doc_id and not args.keep:
            try:
                await google_services.delete_file(doc_id)
                print(OK, "borrar documento")
            except Exception as exc:
                failures += 1
                print(FAIL, "borrar documento:", exc)

    print("\n=== 6. Tasks / Gmail ===")
    if google_services.auth_method != "oauth":
        print(
            SKIP,
            "Tasks/Gmail requieren OAuth: una cuenta de servicio no puede acceder "
            "a las tareas ni al buzón del usuario.",
        )
    else:
        try:
            task = await google_services.create_task("Test Audit Google Services")
            print(OK, "crear tarea:", task["id"])
            await google_services.delete_task(task["id"])
            print(OK, "borrar tarea")
        except Exception as exc:
            failures += 1
            print(FAIL, "tasks:", exc)
        try:
            profile = await google_services.gmail_profile()
            print(OK, "gmail:", profile.get("email"), "| mensajes:", profile.get("messages"))
        except Exception as exc:
            failures += 1
            print(FAIL, "gmail:", exc)

    print("\n=== RESUMEN ===")
    print("fallos:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
