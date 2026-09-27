"""Control de acceso y rate limiting (Fase 5, 5.5.6)."""

from src.config import settings
from src.utils.access_control import SlidingWindowLimiter, is_allowed_user


def test_is_allowed_user_open_without_admins(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [])
    assert is_allowed_user(123456)


def test_is_allowed_user_whitelist(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [42])
    assert is_allowed_user(42)
    assert not is_allowed_user(99)


def test_sliding_window_limiter_blocks_burst():
    clock = {"now": 0.0}
    limiter = SlidingWindowLimiter(max_events=3, window_seconds=10.0, time_fn=lambda: clock["now"])
    assert limiter.allow("chat1")
    assert limiter.allow("chat1")
    assert limiter.allow("chat1")
    assert not limiter.allow("chat1")
    assert limiter.allow("chat2")
    clock["now"] = 11.0
    assert limiter.allow("chat1")
