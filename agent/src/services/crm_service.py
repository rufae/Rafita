"""Mini-CRM de clientes en la boveda (Fase 1, 2026-09-28).

Cada cliente es una nota `CRM/<slug>.md` con frontmatter (nombre, estado,
valor, email, telefono, empresa, ultimo_contacto, proximo_paso,
proximo_seguimiento, etiquetas) y un cuerpo con notas fechadas. No hay base de
datos: la boveda es la fuente de verdad y se puede editar a mano en Obsidian.

Estados: lead -> contactado -> propuesta -> cerrado (o perdido).
"""

import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings
from src.i18n import currency_symbol
from src.logger import logger

CARPETA = "CRM"
ESTADOS = ("lead", "contactado", "propuesta", "cerrado", "perdido")
ESTADOS_ACTIVOS = ("lead", "contactado", "propuesta")
DIAS_SEGUIMIENTO = 7


def _hoy() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _slug(nombre: str) -> str:
    """'María García' -> 'maria-garcia' (nombre de fichero seguro)."""
    base = unicodedata.normalize("NFKD", (nombre or "").strip())
    base = base.encode("ascii", "ignore").decode("ascii").lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base[:80] or "cliente"


def _normalizar_estado(valor: str) -> str:
    base = unicodedata.normalize("NFKD", (valor or "").strip().lower())
    base = base.encode("ascii", "ignore").decode("ascii")
    base = base.replace(" ", "_")
    equivalencias = {
        "nuevo": "lead",
        "contacto": "contactado",
        "presupuesto": "propuesta",
        "ganado": "cerrado",
        "cliente": "cerrado",
        "perdida": "perdido",
    }
    base = equivalencias.get(base, base)
    return base if base in ESTADOS else ""


def _parse_fecha(texto: str) -> date | None:
    texto = (texto or "").strip()
    if not texto:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _parse_frontmatter(texto: str) -> tuple[dict[str, Any], str]:
    """Parsea un frontmatter YAML sencillo (clave: valor, listas [a, b])."""
    datos: dict[str, Any] = {}
    cuerpo = texto
    lineas = texto.splitlines()
    if lineas and lineas[0].strip() == "---":
        for i, linea in enumerate(lineas[1:], start=1):
            if linea.strip() == "---":
                cuerpo = "\n".join(lineas[i + 1 :])
                break
            if ":" not in linea:
                continue
            clave, valor = linea.split(":", 1)
            clave = clave.strip().lower()
            valor = valor.strip()
            if valor.startswith("[") and valor.endswith("]"):
                datos[clave] = [v.strip() for v in valor[1:-1].split(",") if v.strip()]
            elif valor.startswith('"') and valor.endswith('"'):
                datos[clave] = valor[1:-1]
            else:
                datos[clave] = valor
    return datos, cuerpo.strip()


def _render_nota(datos: dict[str, Any], cuerpo: str) -> str:
    orden = (
        "nombre",
        "estado",
        "valor",
        "moneda",
        "email",
        "telefono",
        "empresa",
        "ultimo_contacto",
        "proximo_paso",
        "proximo_seguimiento",
        "etiquetas",
    )
    lineas = ["---"]
    for clave in orden:
        if clave not in datos:
            continue
        valor = datos[clave]
        if isinstance(valor, list):
            lineas.append("%s: [%s]" % (clave, ", ".join(str(v) for v in valor)))
        else:
            lineas.append("%s: %s" % (clave, valor))
    lineas.append("---")
    lineas.append("")
    return "\n".join(lineas) + cuerpo.rstrip() + "\n"


def _ruta_cliente(nombre: str) -> Path:
    return settings.obsidian_vault_path / CARPETA / (_slug(nombre) + ".md")


def _cargar(ruta: Path) -> dict[str, Any] | None:
    try:
        datos, cuerpo = _parse_frontmatter(ruta.read_text(encoding="utf-8"))
    except OSError:
        return None
    datos["cuerpo"] = cuerpo
    datos["_fichero"] = str(ruta)
    return datos


def _valor_numerico(cliente: dict[str, Any]) -> float:
    bruto = str(cliente.get("valor", "") or "").replace(" ", "").replace(",", ".")
    try:
        return float(bruto)
    except ValueError:
        return 0.0


def _formatear_valor(total: float, moneda: str = "") -> str:
    simbolo = currency_symbol(moneda or settings.default_currency)
    if total == int(total):
        return "%d %s" % (int(total), simbolo)
    return "%.2f %s" % (total, simbolo)


async def obtener(nombre: str) -> dict[str, Any] | None:
    """Busca un cliente por nombre (con o sin acentos, o por slug)."""
    ruta = _ruta_cliente(nombre)
    if ruta.exists():
        return _cargar(ruta)
    objetivo = _slug(nombre)
    for cliente in await listar():
        if _slug(cliente.get("nombre", "")) == objetivo:
            return cliente
    return None


async def listar(estado: str | None = None) -> list[dict[str, Any]]:
    carpeta = settings.obsidian_vault_path / CARPETA
    if not carpeta.exists():
        return []
    clientes: list[dict[str, Any]] = []
    for ruta in sorted(carpeta.glob("*.md")):
        datos = _cargar(ruta)
        if not datos:
            continue
        clientes.append(datos)
    if estado:
        normalizado = _normalizar_estado(estado)
        clientes = [c for c in clientes if _normalizar_estado(c.get("estado", "")) == normalizado]
    return clientes


async def crear_o_actualizar(nombre: str, **campos: Any) -> dict[str, Any]:
    """Crea el cliente si no existe y aplica los campos indicados."""
    nombre = (nombre or "").strip()
    if not nombre:
        return {"success": False, "message": "Necesito el nombre del cliente."}
    existente = await obtener(nombre)
    datos: dict[str, Any] = dict(existente or {})
    cuerpo = str(datos.pop("cuerpo", "") or "")
    datos.pop("_fichero", None)
    if not existente:
        datos.update(
            {
                "nombre": nombre,
                "estado": "lead",
                "valor": "",
                "moneda": settings.default_currency,
                "email": "",
                "telefono": "",
                "empresa": "",
                "ultimo_contacto": _hoy().isoformat(),
                "proximo_paso": "",
                "proximo_seguimiento": "",
                "etiquetas": ["cliente"],
            }
        )
        cuerpo = "# %s\n\n## Notas\n" % nombre
    if campos.get("nombre"):
        datos["nombre"] = str(campos["nombre"]).strip()
    if campos.get("estado"):
        estado = _normalizar_estado(str(campos["estado"]))
        if not estado:
            return {
                "success": False,
                "message": "Estado no valido. Usa: %s." % ", ".join(ESTADOS),
            }
        datos["estado"] = estado
    if campos.get("valor") not in (None, ""):
        datos["valor"] = campos["valor"]
    for campo in ("email", "telefono", "empresa", "moneda", "proximo_paso"):
        if campos.get(campo) not in (None, ""):
            datos[campo] = str(campos[campo]).strip()
    if campos.get("proximo_seguimiento"):
        fecha = _parse_fecha(str(campos["proximo_seguimiento"]))
        if fecha:
            datos["proximo_seguimiento"] = fecha.isoformat()
    if campos.get("nota"):
        fecha_txt = _hoy().strftime("%Y-%m-%d")
        cuerpo = cuerpo.rstrip() + "\n- %s: %s\n" % (fecha_txt, str(campos["nota"]).strip())
        datos["ultimo_contacto"] = _hoy().isoformat()
    if not datos.get("ultimo_contacto"):
        datos["ultimo_contacto"] = _hoy().isoformat()

    ruta = _ruta_cliente(datos["nombre"])
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(_render_nota(datos, cuerpo), encoding="utf-8")
    logger.info(
        "CRM: %s cliente %s (%s)",
        "actualizado" if existente else "creado",
        datos["nombre"],
        ruta.name,
    )
    estado = datos.get("estado", "lead")
    return {
        "success": True,
        "cliente": {k: v for k, v in datos.items() if not k.startswith("_")},
        "message": "Cliente *%s* %s (estado: %s)."
        % (datos["nombre"], "actualizado" if existente else "creado", estado),
    }


async def anadir_nota(nombre: str, texto: str) -> dict[str, Any]:
    texto = (texto or "").strip()
    if not texto:
        return {"success": False, "message": "Dime que anoto."}
    return await crear_o_actualizar(nombre, nota=texto)


async def registrar_contacto(nombre: str, fecha: str | None = None) -> dict[str, Any]:
    cliente = await obtener(nombre)
    if not cliente:
        return {"success": False, "message": "No tengo ningun cliente llamado '%s'." % nombre}
    cuando = _parse_fecha(fecha) if fecha else _hoy()
    if not cuando:
        return {"success": False, "message": "Fecha no valida (usa AAAA-MM-DD)."}
    datos = dict(cliente)
    cuerpo = str(datos.pop("cuerpo", "") or "")
    datos.pop("_fichero", None)
    datos["ultimo_contacto"] = cuando.isoformat()
    ruta = _ruta_cliente(datos.get("nombre", nombre))
    ruta.write_text(_render_nota(datos, cuerpo), encoding="utf-8")
    return {
        "success": True,
        "message": "Contacto de *%s* registrado el %s." % (datos.get("nombre"), cuando.isoformat()),
    }


async def seguimientos_pendientes(dias: int = DIAS_SEGUIMIENTO) -> list[dict[str, Any]]:
    """Clientes activos sin contacto reciente o con seguimiento vencido."""
    hoy = _hoy()
    limite = hoy - timedelta(days=max(1, int(dias or DIAS_SEGUIMIENTO)))
    pendientes: list[dict[str, Any]] = []
    for cliente in await listar():
        estado = _normalizar_estado(cliente.get("estado", ""))
        if estado not in ESTADOS_ACTIVOS:
            continue
        motivo = ""
        proximo = _parse_fecha(cliente.get("proximo_seguimiento", ""))
        ultimo = _parse_fecha(cliente.get("ultimo_contacto", ""))
        if proximo and proximo <= hoy:
            motivo = "seguimiento previsto para %s" % proximo.isoformat()
        elif ultimo and ultimo <= limite:
            motivo = "sin contacto desde %s" % ultimo.isoformat()
        if motivo:
            pendientes.append(
                {
                    "nombre": cliente.get("nombre", ""),
                    "estado": estado,
                    "valor": _valor_numerico(cliente),
                    "proximo_paso": cliente.get("proximo_paso", ""),
                    "motivo": motivo,
                }
            )
    pendientes.sort(key=lambda c: c["valor"], reverse=True)
    return pendientes


async def resumen_pipeline() -> dict[str, Any]:
    clientes = await listar()
    por_estado: dict[str, dict[str, float]] = {
        estado: {"clientes": 0, "valor": 0.0} for estado in ESTADOS
    }
    for cliente in clientes:
        estado = _normalizar_estado(cliente.get("estado", "")) or "lead"
        por_estado[estado]["clientes"] += 1
        por_estado[estado]["valor"] += _valor_numerico(cliente)
    activos = sum(por_estado[e]["clientes"] for e in ESTADOS_ACTIVOS)
    valor_activo = sum(por_estado[e]["valor"] for e in ESTADOS_ACTIVOS)
    return {
        "success": True,
        "total_clientes": len(clientes),
        "por_estado": por_estado,
        "activos": activos,
        "valor_activo": valor_activo,
        "ganado": por_estado["cerrado"]["valor"],
    }


def _tabla_pipeline(resumen: dict[str, Any]) -> str:
    lineas = ["📊 *Pipeline de clientes*", "", "| Estado | Clientes | Valor |", "|---|---|---|"]
    for estado in ESTADOS:
        datos = resumen["por_estado"][estado]
        if not datos["clientes"]:
            continue
        lineas.append(
            "| %s | %d | %s |" % (estado, datos["clientes"], _formatear_valor(datos["valor"]))
        )
    lineas.append("")
    lineas.append(
        "*Activos:* %d (%s) · *Ganado:* %s"
        % (
            resumen["activos"],
            _formatear_valor(resumen["valor_activo"]),
            _formatear_valor(resumen["ganado"]),
        )
    )
    return "\n".join(lineas)


async def handle(action: str, args: dict[str, Any]) -> dict[str, Any]:
    """Entrada del tool `manage_crm` del chat."""
    action = (action or "").strip().lower()
    nombre = str(args.get("nombre", "") or "").strip()
    if action in ("create", "update"):
        if not nombre:
            return {"success": False, "message": "Dime el nombre del cliente."}
        return await crear_o_actualizar(
            nombre,
            estado=args.get("estado"),
            valor=args.get("valor"),
            email=args.get("email"),
            telefono=args.get("telefono"),
            empresa=args.get("empresa"),
            proximo_paso=args.get("proximo_paso"),
            proximo_seguimiento=args.get("proximo_seguimiento"),
            nota=args.get("nota"),
        )
    if action == "note":
        return await anadir_nota(nombre, str(args.get("nota", "") or ""))
    if action == "estado":
        return await crear_o_actualizar(nombre, estado=args.get("estado"))
    if action == "contacto":
        return await registrar_contacto(nombre, args.get("fecha"))
    if action == "summary":
        resumen = await resumen_pipeline()
        return {"success": True, "message": _tabla_pipeline(resumen), "resumen": resumen}
    if action == "list":
        clientes = await listar(args.get("estado"))
        if not clientes:
            return {"success": True, "message": "No hay clientes en el CRM todavia."}
        lineas = ["👥 *Clientes (%d)*" % len(clientes)]
        for cliente in clientes:
            estado = _normalizar_estado(cliente.get("estado", "")) or "lead"
            extra = ""
            if cliente.get("proximo_paso"):
                extra = " — %s" % cliente["proximo_paso"]
            valor = _valor_numerico(cliente)
            valor_txt = " (%s)" % _formatear_valor(valor) if valor else ""
            lineas.append(
                "  • *%s* — %s%s%s" % (cliente.get("nombre", ""), estado, valor_txt, extra)
            )
        return {"success": True, "message": "\n".join(lineas)}
    return {
        "success": False,
        "message": "Accion no valida. Usa: create, update, note, estado, contacto, list, summary.",
    }
