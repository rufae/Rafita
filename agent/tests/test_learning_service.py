"""Learning loop semanal: destila Aprendizajes/ en skills (guard KV + LLM)."""

from types import SimpleNamespace

import pytest

from src.services import learning_service as ls


class _LLMFalso:
    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.llamadas = 0

    async def chat(self, **kwargs):
        self.llamadas += 1
        if isinstance(self.respuesta, Exception):
            raise self.respuesta
        return self.respuesta


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "VAULT_PATH", tmp_path)
    kv = {}

    async def kv_get(key):
        return kv.get(key)

    async def kv_set(key, value, expires_at=None):
        kv[key] = value

    monkeypatch.setattr(
        "src.database.db",
        SimpleNamespace(kv_get=kv_get, kv_set=kv_set),
        raising=False,
    )
    avisos = []

    async def fake_notify(text):
        avisos.append(text)

    monkeypatch.setattr(ls, "_notify_admins", fake_notify)
    return tmp_path, kv, avisos


async def test_sin_aprendizajes_cierra_semana(entorno):
    _tmp, kv, avisos = entorno
    res = await ls.weekly_learning_review()
    assert res["success"] is True
    assert res["sin_aprendizajes"] is True
    assert kv.get("learning:last")
    assert avisos == []
    # Segunda llamada en la misma semana: sin tocar el LLM.
    res2 = await ls.weekly_learning_review()
    assert res2.get("skipped")


async def test_guard_kv_evita_repetir(entorno, monkeypatch):
    tmp, kv, _avisos = entorno
    (tmp / "Aprendizajes").mkdir()
    (tmp / "Aprendizajes" / "fix.md").write_text("Reiniciar el servicio X.", encoding="utf-8")
    monkeypatch.setattr("src.ollama_client.llm", _LLMFalso('{"skills": []}'), raising=False)
    res = await ls.weekly_learning_review()
    assert res["success"] is True
    assert res.get("sin_propuestas") is True
    res2 = await ls.weekly_learning_review()
    assert res2.get("skipped")
    res3 = await ls.weekly_learning_review(force=True)
    assert not res3.get("skipped")


async def test_llm_falla_es_honesto_y_reintenta(entorno, monkeypatch):
    tmp, kv, avisos = entorno
    (tmp / "Aprendizajes").mkdir()
    (tmp / "Aprendizajes" / "fix.md").write_text("Algo util.", encoding="utf-8")
    monkeypatch.setattr(
        "src.ollama_client.llm", _LLMFalso(RuntimeError("ollama caido")), raising=False
    )
    res = await ls.weekly_learning_review()
    assert res["success"] is False
    assert "reintentare" in res["message"]
    assert "learning:last" not in kv  # sin marca: se reintenta
    assert avisos == []


async def test_propone_skill_y_guarda(entorno, monkeypatch):
    tmp, kv, avisos = entorno
    from src.utils import skills_manager as sm

    monkeypatch.setattr(sm, "VAULT_PATH", tmp)
    (tmp / "Aprendizajes").mkdir()
    (tmp / "Aprendizajes" / "fix.md").write_text("dnsmasq: reiniciar para DNS.", encoding="utf-8")
    resp = '{"skills": [{"name": "fix dns", "description": "Reparar DNS", "body": "1. Restart."}]}'
    falso = _LLMFalso(resp)
    monkeypatch.setattr("src.ollama_client.llm", falso, raising=False)
    res = await ls.weekly_learning_review()
    assert res["success"] is True
    assert res["guardadas"] == ["fix dns"]
    assert (tmp / "skills" / "fix dns.md").is_file()
    assert falso.llamadas == 1
    assert avisos and "Learning loop" in avisos[0]
    assert kv.get("learning:last")


def test_slug_nombre_invalido():
    assert ls._slug("") == "aprendizaje"
    assert "/" not in ls._slug("a/b")
    assert "\\" not in ls._slug("a\\b")
    assert ls._slug("fix dns!") == "fix dns"
