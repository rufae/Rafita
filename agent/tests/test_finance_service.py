"""Núcleo financiero: domiciliados, dedupe y escritura consistente."""

import asyncio

import pytest

import src.services.finance_service as fs
from src.config import settings


def _run(coro):
    return asyncio.run(coro)


def _afn(valor):
    async def _f(*a, **k):
        return valor

    return _f()


@pytest.mark.parametrize(
    "texto",
    [
        "Domiciliación gimnasio Basic-Fit",
        "Cuota de seguro del coche",
        "Recibo de luz Iberdrola",
        "Mandato SEPA ABONO NÓMINA",
    ],
)
def test_detecta_domiciliados(texto):
    assert fs.es_domiciliado(texto) is True


@pytest.mark.parametrize(
    "texto",
    [
        "Pago en Mercadona 45,20",
        "Google Pay TIKI BAR",
        "Compra Amazon",
        "",
    ],
)
def test_no_es_domiciliado(texto):
    assert fs.es_domiciliado(texto) is False


def test_gasto_banco_rechaza_importe(monkeypatch):
    llamadas = []
    monkeypatch.setattr(fs, "registrar_movimiento", lambda **k: llamadas.append(k) or _afn(1))
    res = _run(fs.registrar_gasto_banco(chat_id=1, amount="no-numero"))
    assert res["success"] is False
    assert llamadas == []


def test_gasto_banco_salta_domiciliado(monkeypatch):
    llamadas = []
    monkeypatch.setattr(fs, "registrar_movimiento", lambda **k: llamadas.append(k) or _afn(1))
    res = _run(fs.registrar_gasto_banco(chat_id=1, amount=39.99, text="Domiciliación gimnasio"))
    assert res["success"] is True
    assert res["skipped"] == "domiciliado"
    assert llamadas == []


def test_gasto_banco_registra_y_dedupe(monkeypatch):
    guardados = {}
    monkeypatch.setattr(fs, "registrar_movimiento", lambda **k: _afn(77))
    monkeypatch.setattr(settings, "default_currency", "EUR")
    monkeypatch.setattr(fs.db, "kv_get", lambda k: _afn(guardados.get(k)))
    monkeypatch.setattr(
        fs.db, "kv_set", lambda k, v, expires_at=None: _afn(guardados.__setitem__(k, v))
    )

    res = _run(
        fs.registrar_gasto_banco(chat_id=1, amount=12.5, merchant="Mercadona", dedupe_key="abc123")
    )
    assert res["success"] is True
    assert res["record_id"] == 77
    assert res["notify"] is True
    assert any(k.startswith("banco:dedupe:") for k in guardados)

    res2 = _run(
        fs.registrar_gasto_banco(chat_id=1, amount=12.5, merchant="Mercadona", dedupe_key="abc123")
    )
    assert res2["duplicate"] is True
    assert res2["notify"] is False


def test_concepto_limpio_limita_longitud():
    largo = "x" * 200
    assert len(fs._concepto_limpio(largo)) == 60
    assert fs._concepto_limpio("  hola   mundo ") == "hola mundo"


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Has pagado 45,20 € en Mercadona", 45.20),
        ("Pago con tarjeta 12,99 EUR", 12.99),
        ("Compra por 30,00 euros", 30.00),
        ("Cobro de 1.250,00 € de nomina", 1250.00),
        ("no lleva importe", 0.0),
        ("", 0.0),
    ],
)
def test_extraer_importe(texto, esperado):
    assert fs.extraer_importe(texto) == esperado


def test_gasto_banco_extrae_importe_del_texto(monkeypatch):
    guardados = {}
    monkeypatch.setattr(settings, "default_currency", "EUR")
    monkeypatch.setattr(fs, "registrar_movimiento", lambda **k: _afn(3))
    monkeypatch.setattr(fs.db, "kv_get", lambda k: _afn(guardados.get(k)))
    monkeypatch.setattr(
        fs.db,
        "kv_set",
        lambda k, v, expires_at=None: _afn(guardados.__setitem__(k, v)),
    )
    res = _run(
        fs.registrar_gasto_banco(chat_id=1, amount=0, text="Google Pay ha sido 4,50 € en Metro")
    )
    assert res["success"] is True
    assert res["amount"] == 4.5
    assert res["notify"] is True
