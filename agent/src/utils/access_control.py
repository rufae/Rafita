"""Control de acceso y rate limiting (Fase 5, tarea 5.5.6).

El bot es un asistente PRIVADO: si hay ADMIN_IDS configurados, solo esos
usuarios pueden usarlo. Si no hay ADMIN_IDS, el bot queda abierto (mismos
compatibilidad con instalaciones de un solo usuario sin configurar).
"""

import time
from collections import deque


class SlidingWindowLimiter:
    """Ventana deslizante por clave (p. ej. chat_id): max N eventos / ventana."""

    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        time_fn=time.monotonic,
    ):
        self.max_events = max(1, int(max_events))
        self.window_seconds = float(window_seconds)
        self._time_fn = time_fn
        self._events: dict[str, deque] = {}

    def allow(self, key: str) -> bool:
        now = self._time_fn()
        bucket = self._events.setdefault(key, deque())
        while bucket and now - bucket[0] > self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.max_events:
            return False
        bucket.append(now)
        return True


# 20 mensajes por minuto por chat: sobra para uso humano y corta abusos.
chat_limiter = SlidingWindowLimiter(max_events=20, window_seconds=60.0)


def is_allowed_user(user_id: int) -> bool:
    """True si el usuario puede usar el bot (ADMIN_IDS = whitelist)."""
    from src.config import settings

    admins = settings.admin_ids
    return not admins or user_id in admins
