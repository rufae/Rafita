"""Tests del onboarding guiado de /start (mejora 3)."""

from src.utils.onboarding import render_onboarding


def _items():
    return [
        {"title": "Ubicación", "status": "ok", "detail": "Sevilla", "next": ""},
        {
            "title": "Google",
            "status": "warn",
            "detail": "no conectado (opcional)",
            "next": "/setup_google",
        },
        {
            "title": "Backups",
            "status": "fail",
            "detail": "sin datos de backup",
            "next": "instala el backup",
        },
    ]


def test_render_muestra_todos_los_items():
    out = render_onboarding(_items())
    assert "✅ *Ubicación*: Sevilla" in out
    assert "⚠️ *Google*" in out
    assert "❌ *Backups*" in out


def test_render_incluye_solo_pasos_pendientes():
    out = render_onboarding(_items())
    assert "*Siguiente paso:*" in out
    assert "`/setup_google`" in out
    assert "instala el backup" in out
    assert "Sevilla" in out


def test_render_todo_listo():
    out = render_onboarding(
        [{"title": "IA local", "status": "ok", "detail": "modelo disponible", "next": ""}]
    )
    assert "Todo listo" in out
    assert "Siguiente paso" not in out


def test_render_sin_next_no_se_propone_paso():
    out = render_onboarding(
        [{"title": "Voz", "status": "warn", "detail": "sin modelo", "next": ""}]
    )
    assert "⚠️ *Voz*" in out
    assert "Siguiente paso" not in out
