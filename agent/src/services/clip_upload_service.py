"""Subida de clips de OpenShorts a redes sociales (desde el Dell).

Los clips se descargan a CLIPS_DIR y se suben desde aqui. Los codigos de
subida (YouTube resumable + Instagram Reels resumable) estan adaptados de
MoneyPrinterTurbo/subir_a_redes.py, probados en esa pipeline.

- YouTube: `videos.insert` resumable (gratis: bucket propio de 100/dia).
- Instagram: contenedor REELS con `upload_type=resumable` (sin URL publica),
  espera a FINISHED y `media_publish` (gratis: Graph API).
- TikTok: Content Posting API (gratis pero exige app auditada); mientras no
  haya credenciales se avisa y no se sube.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from src.config import settings
from src.logger import logger

GRAPH = "https://graph.facebook.com/v26.0"
CHUNK_YT = 8 * 1024 * 1024
TIMEOUT_SUBIDA = 600.0


def _clips_dir() -> Path:
    base = Path(settings.clips_dir or "~/clips").expanduser()
    base.mkdir(parents=True, exist_ok=True)
    return base


def clip_path(job_id: str, filename: str) -> Path:
    return _clips_dir() / job_id / Path(filename).name


async def download_clip(job_id: str, filename: str) -> Path | None:
    """Descarga un clip de OpenShorts al Dell. Devuelve la ruta local."""
    dest = clip_path(job_id, filename)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    base = (settings.openshorts_api_url or "").rstrip("/")
    if not base:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{base}/videos/{job_id}/{Path(filename).name}"
    try:
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        logger.info("Clip descargado: %s (%d bytes)", dest, dest.stat().st_size)
        return dest
    except Exception as e:
        logger.warning("Clip download failed: %s", e)
        return None


def delete_clip(job_id: str, filename: str) -> bool:
    """Borra la copia local de un clip (y su carpeta si queda vacia)."""
    dest = clip_path(job_id, filename)
    try:
        if dest.exists():
            dest.unlink()
        parent = dest.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
        logger.info("Clip borrado: %s", dest)
        return True
    except Exception as e:
        logger.warning("Clip delete failed: %s", e)
        return False


async def yt_access_token(client: httpx.AsyncClient) -> str:
    """Cambia el refresh token de YouTube por un access token fresco."""
    resp = await client.post(
        "https://oauth2.googleapis.com/token",
        json={
            "grant_type": "refresh_token",
            "client_id": settings.yt_client_id,
            "client_secret": settings.yt_client_secret,
            "refresh_token": settings.yt_refresh_token,
        },
    )
    resp.raise_for_status()
    token = str(resp.json().get("access_token") or "")
    if not token:
        raise RuntimeError("YT token: respuesta sin access_token")
    return token


async def upload_youtube(
    video: Path, titulo: str, descripcion: str, tags: list[str], idioma: str = "es"
) -> tuple[str, str]:
    """Sube el clip a YouTube (resumable). Devuelve (url, error)."""
    etiquetas: list[str] = []
    longitud = 0
    for tag in tags:
        tag = str(tag).lstrip("#").strip()
        if tag and longitud + len(tag) + 1 <= 480:
            etiquetas.append(tag)
            longitud += len(tag) + 1
    cuerpo = {
        "snippet": {
            "title": str(titulo or video.stem)[:100],
            "description": str(descripcion or ""),
            "tags": etiquetas,
            "defaultLanguage": idioma,
            "defaultAudioLanguage": idioma,
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }
    tam = video.stat().st_size
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            token = await yt_access_token(client)
            init = await client.post(
                "https://www.googleapis.com/upload/youtube/v3/videos"
                "?uploadType=resumable&part=snippet,status",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-Upload-Content-Length": str(tam),
                    "X-Upload-Content-Type": "video/mp4",
                },
                json=cuerpo,
            )
            if init.status_code >= 400:
                return "", f"YT init: HTTP {init.status_code} {init.text[:180]}"
            url_subida = init.headers.get("Location") or ""
            if not url_subida:
                return "", "YT init: respuesta sin Location"

            offset = 0
            with video.open("rb") as handle:
                while offset < tam:
                    bloque = handle.read(CHUNK_YT)
                    if not bloque:
                        return "", "YT subida: fichero truncado"
                    fin = offset + len(bloque) - 1
                    resp = await client.put(
                        url_subida,
                        content=bloque,
                        headers={
                            "Content-Range": f"bytes {offset}-{fin}/{tam}",
                            "Content-Type": "video/mp4",
                        },
                        timeout=TIMEOUT_SUBIDA,
                    )
                    offset += len(bloque)
                    if resp.status_code in (200, 201):
                        vid = str(resp.json().get("id") or "")
                        if vid:
                            return f"https://youtube.com/shorts/{vid}", ""
                        return "", "YT subida: respuesta sin id"
                    if resp.status_code != 308:
                        return "", f"YT PUT: HTTP {resp.status_code} {resp.text[:180]}"
    except Exception as e:
        return "", f"YouTube: {e}"
    return "", "YT subida: incompleta"


async def upload_instagram(video: Path, caption: str) -> tuple[str, str]:
    """Sube el clip como Reel de Instagram. Devuelve (permalink, error)."""
    token = (settings.ig_access_token or "").strip()
    uid = (settings.ig_user_id or "").strip()
    if not token or not uid:
        return "", "Instagram no configurado (IG_USER_ID / IG_ACCESS_TOKEN)"
    encabezados = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            sesion = await client.post(
                f"{GRAPH}/{uid}/media",
                headers=encabezados,
                json={
                    "media_type": "REELS",
                    "upload_type": "resumable",
                    "caption": str(caption or "")[:2200],
                },
            )
            if sesion.status_code >= 400:
                return "", f"IG sesion: HTTP {sesion.status_code} {sesion.text[:180]}"
            datos = sesion.json() or {}
            cid = str(datos.get("id") or "")
            uri = str(datos.get("uri") or "")
            if not cid or not uri:
                return "", "IG sesion: respuesta sin id/uri"

            tam = video.stat().st_size
            with video.open("rb") as handle:
                subida = await client.post(
                    uri,
                    headers={
                        "Authorization": f"OAuth {token}",
                        "offset": "0",
                        "file_size": str(tam),
                    },
                    content=handle.read(),
                    timeout=TIMEOUT_SUBIDA,
                )
            if subida.status_code >= 400:
                return "", f"IG rupload: HTTP {subida.status_code} (cid {cid})"

            # Procesamiento asincrono de Meta: hasta ~5 min.
            for intento in range(10):
                if intento:
                    await asyncio.sleep(30)
                estado = await client.get(
                    f"{GRAPH}/{cid}", headers=encabezados, params={"fields": "status_code"}
                )
                codigo = str((estado.json() or {}).get("status_code") or "")
                if codigo == "FINISHED":
                    break
                if codigo in ("EXPIRED", "ERROR"):
                    return "", f"IG contenedor {codigo} (cid {cid})"
            else:
                return "", f"IG IN_PROGRESS tras 5 min (cid {cid})"

            publi = await client.post(
                f"{GRAPH}/{uid}/media_publish",
                headers=encabezados,
                params={"creation_id": cid},
            )
            if publi.status_code >= 400:
                return "", f"IG publish: HTTP {publi.status_code} {publi.text[:180]}"
            mid = str((publi.json() or {}).get("id") or "")
            enlace = await client.get(
                f"{GRAPH}/{mid}", headers=encabezados, params={"fields": "permalink"}
            )
            permalink = str((enlace.json() or {}).get("permalink") or "")
            return permalink or f"https://instagram.com/p/{mid}", ""
    except Exception as e:
        return "", f"Instagram: {e}"


async def upload_tiktok(video: Path, titulo: str) -> tuple[str, str]:
    """Sube el clip a TikTok (Direct Post). Requiere app auditada por TikTok."""
    token = (settings.tiktok_access_token or "").strip()
    if not token:
        return "", (
            "TikTok no configurado: pendiente de registrar la app en "
            "developers.tiktok.com y aprobar el scope video.publish"
        )
    tam = video.stat().st_size
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            init = await client.post(
                "https://open.tiktokapis.com/v2/post/publish/video/init/",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "post_info": {
                        "title": str(titulo or video.stem)[:150],
                        "privacy_level": "PUBLIC_TO_EVERYONE",
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": tam,
                        "chunk_size": tam,
                        "total_chunk_count": 1,
                    },
                },
            )
            if init.status_code >= 400:
                return "", f"TT init: HTTP {init.status_code} {init.text[:180]}"
            datos = (init.json() or {}).get("data") or {}
            url_subida = str(datos.get("upload_url") or "")
            if not url_subida:
                return "", "TT init: respuesta sin upload_url"
            with video.open("rb") as handle:
                subida = await client.put(
                    url_subida,
                    content=handle.read(),
                    headers={
                        "Content-Range": f"bytes 0-{tam - 1}/{tam}",
                        "Content-Type": "video/mp4",
                    },
                    timeout=TIMEOUT_SUBIDA,
                )
            if subida.status_code >= 400:
                return "", f"TT PUT: HTTP {subida.status_code}"
            return f"tiktok publish_id {datos.get('publish_id')}", ""
    except Exception as e:
        return "", f"TikTok: {e}"


async def upload_clip(payload: dict[str, Any]) -> dict[str, Any]:
    """Orquesta: descarga el clip al Dell y lo sube a las redes pedidas.

    payload: {job_id, archivo, titulo, descripcion_yt, descripcion_ig,
              descripcion_tt, hashtags, plataformas, chat_id}
    """
    job_id = str(payload.get("job_id") or "")
    archivo = str(payload.get("archivo") or "")
    plataformas = [str(p).lower() for p in (payload.get("plataformas") or ["youtube", "instagram"])]
    titulo = str(payload.get("titulo") or archivo)
    errores: list[str] = []
    links: dict[str, str] = {}

    ruta = await download_clip(job_id, archivo)
    if ruta is None:
        return {"success": False, "links": {}, "errores": [f"no se pudo descargar {archivo}"]}

    if "youtube" in plataformas:
        url, err = await upload_youtube(
            ruta,
            titulo=titulo,
            descripcion=str(payload.get("descripcion_yt") or ""),
            tags=[str(t) for t in (payload.get("hashtags") or [])],
        )
        if err:
            errores.append(err)
        else:
            links["youtube"] = url
    if "instagram" in plataformas:
        url, err = await upload_instagram(ruta, str(payload.get("descripcion_ig") or ""))
        if err:
            errores.append(err)
        else:
            links["instagram"] = url
    if "tiktok" in plataformas:
        url, err = await upload_tiktok(ruta, titulo)
        if err:
            errores.append(err)
        else:
            links["tiktok"] = url

    ok = bool(links) and not errores
    logger.info("Clip %s/%s subido: links=%s errores=%s", job_id, archivo, list(links), errores)
    return {"success": ok, "links": links, "errores": errores}
