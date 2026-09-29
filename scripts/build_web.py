#!/usr/bin/env python3
"""Build mínimo de la web (Fase 4, 2026-09-29): minifica y hace cache-busting.

Sin frameworks ni Node: usa `rjsmin` y `csscompressor` (Python puro).
Genera `web/app-dist/` con:
  - `app-<hash8>.js`, `styles-<hash8>.css`, `design-tokens-<hash8>.css` (minificados,
    hash del contenido en el nombre → cache inmutable sin caducar).
  - `index.html` con las referencias reescritas a esos nombres.
  - `sw.js` con el SHELL actualizado (mismos nombres hasheados) y nombre de
    caché por build.
  - copia de `config.js` (editable por instalación, se sirve sin caché),
    `manifest.webmanifest` e `icons/`.

El gateway sirve `web/app-dist/` si existe (producción) y si no `web/app/`
(desarrollo). El source de `web/app/` nunca se toca.

Uso:
    pip install rjsmin csscompressor
    python scripts/build_web.py
"""

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SRC = RAIZ / "web" / "app"
DIST = RAIZ / "web" / "app-dist"


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


def _min_js(texto: str) -> str:
    try:
        from rjsmin import jsmin

        return jsmin(texto)
    except ImportError:
        print("AVISO: rjsmin no instalado (pip install rjsmin); se copia sin minificar")
        return texto


def _min_css(texto: str) -> str:
    try:
        from csscompressor import compress

        return compress(texto)
    except ImportError:
        print("AVISO: csscompressor no instalado (pip install csscompressor)")
        return texto


def build(destino: Path = DIST) -> dict[str, str]:
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)

    originales = {p.name: p for p in SRC.iterdir() if p.is_file()}

    # 1) minificar JS/CSS con hash de contenido
    hasheados: dict[str, str] = {}  # nombre origen -> nombre destino
    for nombre, modo in (("app.js", "js"), ("styles.css", "css"), ("design-tokens.css", "css")):
        fuente = originales[nombre].read_text(encoding="utf-8")
        comprimido = _min_js(fuente) if modo == "js" else _min_css(fuente)
        datos = comprimido.encode("utf-8")
        h = _hash(datos)
        base, ext = nombre.split(".", 1)
        destino_nombre = "%s-%s.%s" % (base, h, ext)
        (destino / destino_nombre).write_bytes(datos)
        hasheados[nombre] = destino_nombre
        print(
            "  %-12s -> %-24s %6d -> %6d bytes (%.0f%%)"
            % (
                nombre,
                destino_nombre,
                len(fuente.encode()),
                len(datos),
                100.0 * len(datos) / max(1, len(fuente.encode())),
            )
        )

    # 2) index.html con referencias reescritas (config.js queda sin hashear:
    #    es editable por instalacion y se sirve con Cache-Control: no-store)
    html = originales["index.html"].read_text(encoding="utf-8")
    for nombre, destino_nombre in hasheados.items():
        html = html.replace('href="%s"' % nombre, 'href="%s"' % destino_nombre)
        html = html.replace('src="%s"' % nombre, 'src="%s"' % destino_nombre)
    (destino / "index.html").write_text(html, encoding="utf-8")

    # 3) service worker: SHELL con nombres hasheados + caché por build
    sw = originales["sw.js"].read_text(encoding="utf-8")
    for nombre, destino_nombre in hasheados.items():
        sw = sw.replace("'./%s'" % nombre, "'./%s'" % destino_nombre)
    h_build = _hash(html.encode())
    sw = re.sub(r"const CACHE = '[^']+';", "const CACHE = 'rafita-shell-%s';" % h_build, sw)
    (destino / "sw.js").write_text(sw, encoding="utf-8")

    # 4) el resto de ficheros, tal cual (config.js, manifest, iconos)
    for nombre, ruta in originales.items():
        if nombre in ("index.html", "sw.js") or nombre in hasheados:
            continue
        shutil.copy2(ruta, destino / nombre)
    (destino / "icons").mkdir(parents=True, exist_ok=True)
    for icono in sorted((SRC / "icons").glob("*")):
        shutil.copy2(icono, destino / "icons" / icono.name)

    print("Build listo en %s (cache: rafita-shell-%s)" % (destino, h_build))
    return hasheados


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DIST), help="carpetade salida")
    args = parser.parse_args()
    build(Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
