#!/usr/bin/env python3
"""Configura las credenciales de publicación (YouTube + Instagram) para Rafita.

Solo hay que hacerlo UNA vez: los tokens de refresco duran indefinidamente
(YouTube) y el de Instagram se renueva solo (~60 días).

USO
---
1) YOUTUBE (Google Cloud):
   a) Crea un proyecto en https://console.cloud.google.com/
   b) Activa "YouTube Data API v3".
   c) Credenciales → Crear credenciales → ID de cliente OAuth → Aplicación de
      escritorio.
   d) Ejecuta:  python3 setup_social_creds.py youtube
      y sigue las instrucciones (abre una URL, autorizas, pegas el código).

2) INSTAGRAM (Meta):
   a) Necesita una cuenta PROFESIONAL (Creator o Business) vinculada a una
      Página de Facebook, y una app en https://developers.facebook.com/
      con los permisos instagram_basic + instagram_content_publish +
      pages_read_engagement (en modo desarrollo basta para tus propias
      cuentas: añade tu usuario como administrador de la app).
   b) Consigue un token de usuario de larga duración (Graph API Explorer →
      "Generate Access Token" → extiéndelo a 60 días con este script).
   c) Ejecuta:  python3 setup_social_creds.py instagram
      y pega el token cuando te lo pida.

Al final imprime las líneas listas para pegar en:
  - /home/server/proyectos/rafita/.env          (agente Rafita)
  - /home/server/proyectos/rafita/deploy/hp/.env (n8n)
"""

import json
import sys
import urllib.parse
import urllib.request

# ── YOUTUBE ──────────────────────────────────────────────────────────────────

SCOPES = "https://www.googleapis.com/auth/youtube.upload"
REDIRECT = "https://developers.google.com/oauthplayground"


def youtube(client_id: str, client_secret: str) -> None:
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "scope": SCOPES,
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    print("\n1) Abre esta URL en el navegador y autoriza con tu cuenta de YouTube:\n")
    print("   " + url + "\n")
    print("2) Al autorizar, Google redirige a oauthplayground con un código en la URL.")
    print("   Copia el parámetro 'code=...' de esa URL.\n")
    code = input("3) Pega aquí el código: ").strip()
    data = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT,
            "grant_type": "authorization_code",
        }
    ).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req, timeout=30) as resp:
        tok = json.loads(resp.read())
    refresh = tok.get("refresh_token")
    if not refresh:
        print(
            "✗ No llegó refresh_token (revoca el acceso en myaccount.google.com/permissions y repite)"
        )
        return
    print("\n✓ YouTube listo. Pega esto en los .env:\n")
    print(f"YT_CLIENT_ID={client_id}")
    print(f"YT_CLIENT_SECRET={client_secret}")
    print(f"YT_REFRESH_TOKEN={refresh}")


# ── INSTAGRAM ────────────────────────────────────────────────────────────────


def instagram(short_token: str) -> None:
    print("\n1) Cambiando el token a larga duración (60 días)...")
    url = "https://graph.facebook.com/v26.0/oauth/access_token?" + urllib.parse.urlencode(
        {
            "grant_type": "fb_exchange_token",
            "client_id": input("client_id de la app Meta: ").strip(),
            "client_secret": input("client_secret de la app Meta: ").strip(),
            "fb_exchange_token": short_token,
        }
    )
    with urllib.request.urlopen(url, timeout=30) as resp:
        long_token = json.loads(resp.read()).get("access_token")
    if not long_token:
        print("✗ No se pudo extender el token")
        return
    me = json.load(
        urllib.request.urlopen(
            "https://graph.facebook.com/v26.0/me/accounts?access_token=" + long_token, timeout=30
        )
    )
    page_id = (me.get("data") or [{}])[0].get("id", "")
    ig = {}
    if page_id:
        ig = json.load(
            urllib.request.urlopen(
                f"https://graph.facebook.com/v26.0/{page_id}?fields=instagram_business_account"
                f"&access_token={long_token}",
                timeout=30,
            )
        )
    ig_id = ((ig.get("instagram_business_account") or {}) or {}).get("id", "")
    if not ig_id:
        ig_id = input("No encontré la cuenta IG automáticamente. Pega IG_USER_ID: ").strip()
    print("\n✓ Instagram listo. Pega esto en los .env:\n")
    print(f"IG_USER_ID={ig_id}")
    print(f"IG_ACCESS_TOKEN={long_token}")
    print("\n(El token dura 60 días; este script puede renovarlo pasados al menos 24 h)")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("youtube", "instagram"):
        print(__doc__)
        return
    if sys.argv[1] == "youtube":
        youtube(input("client_id de Google: ").strip(), input("client_secret de Google: ").strip())
    else:
        instagram(input("Token de usuario (corta duración) de Instagram: ").strip())


if __name__ == "__main__":
    main()
