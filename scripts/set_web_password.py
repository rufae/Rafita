#!/usr/bin/env python3
"""Crea o actualiza un usuario de la web (email + contrasena).

Uso (dentro del contenedor, que tiene acceso a la BD y a /app/src):

    docker exec rafita-agent-core python /workspace/scripts/set_web_password.py \
        email@ejemplo.com 'mi-contrasena' [--admin]

El primer usuario puede crearse tambien desde .env con WEB_ADMIN_EMAIL y
WEB_ADMIN_PASSWORD (se crea solo al arrancar si no hay ninguno).
"""

import argparse
import asyncio
import sys

sys.path.insert(0, "/app/src")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("password")
    parser.add_argument("--admin", action="store_true", help="marcar como administrador")
    args = parser.parse_args()

    from src.database import db
    from src.utils.web_auth import hash_password

    await db.initialize()
    email = args.email.strip().lower()
    if len(args.password) < 8:
        print("ERROR: la contrasena debe tener al menos 8 caracteres")
        return 1
    user = await db.get_web_user_by_email(email)
    if user:
        await db.update_web_user_password(user["id"], hash_password(args.password))
        if args.admin and not user.get("is_admin"):
            await db.execute("UPDATE web_users SET is_admin = 1 WHERE id = ?", (user["id"],))
            await db._conn.commit()
        print(
            "Usuario actualizado: %s (admin=%s)" % (email, bool(args.admin or user.get("is_admin")))
        )
        return 0
    user_id = await db.create_web_user(email, hash_password(args.password), is_admin=args.admin)
    print("Usuario creado: %s (#%d, admin=%s)" % (email, user_id, args.admin))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
