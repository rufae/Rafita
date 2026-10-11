"""Núcleo financiero compartido: gastos e ingresos (DB + Obsidian + banco).

Usado por las tools del LLM (`save_expense`/`save_income`), por los handlers
`/gasto` y `/ingreso`, y por el endpoint `/automation/banco-gasto` (captura
automática desde la notificación del banco en el móvil). Un único sitio
escribe la fila de `Control_Financiero` para que DB y Obsidian no diverjan.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any

from src.config import settings
from src.database import db
from src.logger import logger

_DOMICILIADO_RE = re.compile(
    r"domiciliaci[oó]n|direct debit|mandato|recibo de|cuota de|cuota\b",
    re.I,
)
_NUM_PATRON = r"\d{1,3}(?:[.,]\d{3})+[.,]\d{1,2}|\d{1,6}(?:[.,]\d{1,2})?"
_IMPORTE_EUR_RE = re.compile(r"(%s)\s*(?:€|eur\b)" % _NUM_PATRON, re.I)
_IMPORTE_DEC_RE = re.compile(r"\b(%s)\b" % _NUM_PATRON)
_DEDUPE_TTL_H = 48


def es_domiciliado(texto: str) -> bool:
    """Un cargo domiciliado (gimnasio, seguro...) no es un pago con tarjeta."""
    return bool(_DOMICILIADO_RE.search(texto or ""))


def _parse_importe(raw: str) -> float:
    s = raw.strip()
    try:
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            s = s.replace(",", ".")
        elif "." in s and len(s.split(".")[-1]) == 3:
            s = s.replace(".", "")
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def extraer_importe(texto: str) -> float:
    """Importe de una notificación tipo 'Has pagado 45,20 € en ...'.

    Entiende formato español (1.250,00 €) e inglés (1,250.00); si no lleva
    €/EUR, coge el primer número con decimales del texto.
    """
    for patron in (_IMPORTE_EUR_RE, _IMPORTE_DEC_RE):
        m = patron.search(texto or "")
        if m:
            valor = _parse_importe(m.group(1))
            if valor > 0:
                return valor
    return 0.0


def _concepto_limpio(texto: str, max_len: int = 60) -> str:
    return re.sub(r"\s+", " ", (texto or "")).strip()[:max_len]


async def sync_obsidian_transaccion(
    concepto: str,
    categoria: str,
    importe: float,
    es_ingreso: bool,
) -> None:
    """Añade la fila a Control_Financiero_2026 (mejor esfuerzo, no rompe)."""
    try:
        from src.i18n import currency_symbol
        from src.utils.obsidian_manager import create_or_append_note
        from src.vault_config import get_taxonomy

        fila = "| %s | %s | %s | %s%.2f %s | - |" % (
            datetime.now().strftime("%Y-%m-%d"),
            _concepto_limpio(concepto) or ("Ingreso" if es_ingreso else "Gasto"),
            categoria,
            "+" if es_ingreso else "-",
            importe,
            currency_symbol(),
        )
        await create_or_append_note(
            title="Control_Financiero_2026",
            content=(
                "## Transacciones\n\n"
                "| Fecha | Concepto | Categoria | Ingreso/Gasto (%s) | Saldo |\n"
                "|---|---|---|---|---|\n%s"
            )
            % (settings.default_currency, fila),
            folder=get_taxonomy().path("areas_finanzas"),
        )
    except Exception as sync_err:
        logger.warning("Obsidian finance sync failed: %s", sync_err)


async def registrar_movimiento(
    chat_id: int,
    amount: float,
    category: str,
    subcategory: str | None = None,
    description: str | None = None,
    recorded_at: str | None = None,
) -> int:
    """Escribe en DB y replica en Obsidian (la única puerta de entrada)."""
    datos: dict[str, Any] = {
        "chat_id": chat_id,
        "amount": amount,
        "category": category,
        "subcategory": subcategory,
        "description": description,
        "currency": settings.default_currency,
    }
    if recorded_at:
        datos["recorded_at"] = recorded_at
    record_id = await db.add_finance_record(**datos)
    await sync_obsidian_transaccion(
        concepto=description or subcategory or category,
        categoria=subcategory or category,
        importe=amount,
        es_ingreso=(category == "income"),
    )
    return record_id


async def registrar_gasto_banco(
    chat_id: int,
    amount: float,
    merchant: str = "",
    text: str = "",
    dedupe_key: str = "",
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Gasto detectado en la notificación de la app del banco (Tasker).

    Descarta domiciliaciones (el usuario sólo quiere pagos con tarjeta/
    wallet), valida importe y no duplica reintentos con dedupe por hash.
    """
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = 0.0
    origen = " ".join(x for x in (merchant, text) if x)
    if amount <= 0:
        amount = extraer_importe(origen)
    if amount <= 0:
        return {
            "success": False,
            "message": "Importe no valido y no se pudo extraer del texto: %r"
            % _concepto_limpio(origen),
            "notify": False,
        }

    if es_domiciliado(origen):
        return {
            "success": True,
            "skipped": "domiciliado",
            "message": "Domiciliacion detectada, no registrada: %s" % _concepto_limpio(origen),
            "notify": False,
        }

    huella = hashlib.sha256(
        ("%.2f|%s|%s" % (amount, _concepto_limpio(origen, 120).lower(), recorded_at or "")).encode(
            "utf-8", errors="replace"
        )
    ).hexdigest()[:32]
    clave = dedupe_key.strip()[:100] or huella
    kv_key = "banco:dedupe:%s" % clave
    if await db.kv_get(kv_key) is not None:
        return {
            "success": True,
            "duplicate": True,
            "message": "Gasto ya registrado (duplicado detectado).",
            "notify": False,
        }

    record_id = await registrar_movimiento(
        chat_id=chat_id,
        amount=amount,
        category="expense",
        subcategory="banco-auto",
        description=_concepto_limpio(origen) or "Pago con tarjeta",
        recorded_at=recorded_at,
    )
    await db.kv_set(
        kv_key,
        str(record_id),
        expires_at=(datetime.utcnow() + timedelta(hours=_DEDUPE_TTL_H)).isoformat(),
    )
    logger.info(
        "Gasto bancario auto: user=%d amount=%.2f concepto=%r id=%d",
        chat_id,
        amount,
        origen[:60],
        record_id,
    )
    return {
        "success": True,
        "record_id": record_id,
        "amount": amount,
        "concepto": _concepto_limpio(origen) or "Pago con tarjeta",
        "message": "Gasto registrado: %.2f %s — %s (ID: %d)"
        % (amount, settings.default_currency, _concepto_limpio(origen), record_id),
        "notify": True,
    }
