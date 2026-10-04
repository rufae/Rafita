#!/usr/bin/env python3
"""Genera las claves VAPID para las notificaciones web push de la PWA.

Escribe la clave privada (PEM) en VAPID_KEY_FILE (por defecto
/data/vapid_private.pem) y muestra la clave publica que usa el navegador
(base64url sin relleno).

Uso:
    python scripts/generate_vapid_keys.py [--force] [ruta/clave.pem]

Con --force sobreescribe una clave existente (las suscripciones existentes
dejarian de funcionar: los navegadores se vuelven a suscribir solos).
"""

import base64
import stat
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

DEFAULT_PATH = "/data/vapid_private.pem"


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    ruta = Path(args[0] if args else DEFAULT_PATH)
    if ruta.exists() and not force:
        print("Ya existe %s (usa --force para regenerar)." % ruta)
        print("Publica actual:", public_key_b64(ruta))
        return 1
    clave = ec.generate_private_key(ec.SECP256R1())
    pem = clave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(pem)
    ruta.chmod(ruta.stat().st_mode | stat.S_IRUSR)  # legible por el contenedor
    print("Clave privada escrita en %s" % ruta)
    print("Publica (applicationServerKey): %s" % public_key_b64(ruta))
    return 0


def public_key_b64(ruta: Path) -> str:
    private_key = serialization.load_pem_private_key(ruta.read_bytes(), password=None)
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(public_bytes).decode("ascii").rstrip("=")


if __name__ == "__main__":
    sys.exit(main())
