"""Flujo LangGraph: interpretar → validar → (aclarar) → planificar → consultar (en paralelo)
→ evaluar → presupuestar → recomendar → informe."""

from __future__ import annotations

import asyncio
import operator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from .emparejamiento import evaluar_todo
from .interpretacion import aplicar_suposiciones, interpretar_con_llm, interpretar_con_reglas, validar
from .llm import crear_llm
from . import modelos
from .modelos import OfertaEvaluada, Presupuesto, ResultadoConsulta, Solicitud
from .navegador import Navegador
from .presupuesto import construir_presupuestos, elegir_recomendacion
from .proveedores import (
    POR_DEFECTO, PROVEEDORES_WEB, SIN_PRECIOS_EN_LINEA, Proveedor, ProveedorCatalogo, resolver_proveedor,
)
from .reporte import generar_informe


class Estado(TypedDict, total=False):
    texto: str
    opciones: dict[str, Any]
    solicitud: Solicitud
    preguntas: list[str]
    aclarado: bool
    proveedores: list[str]
    nombres: dict[str, str]
    mecanismos: dict[str, str]
    resultados: Annotated[list[ResultadoConsulta], operator.add]
    evaluadas: dict[str, list[OfertaEvaluada]]
    presupuestos: list[Presupuesto]
    combinado: Presupuesto | None
    decision: dict[str, Any]
    recomendacion: str
    informe: str
    avisos: Annotated[list[str], operator.add]


class EstadoConsulta(TypedDict):
    proveedor: str
    solicitud: Solicitud
    opciones: dict[str, Any]


# ------------------------------------------------------------------ recursos

_navegador_global: Navegador | None = None


def _navegador(config: RunnableConfig) -> Navegador:
    global _navegador_global
    nav = (config.get("configurable") or {}).get("navegador")
    if nav is None:
        if _navegador_global is None:
            _navegador_global = Navegador(Path(".cache"))
        nav = _navegador_global
    return nav


def _llm(config: RunnableConfig):
    conf = config.get("configurable") or {}
    if conf.get("sin_llm"):
        return None
    if "llm" not in conf:
        conf["llm"] = crear_llm(conf.get("modelo"))
    return conf["llm"]


def instanciar(pid: str, opciones: dict[str, Any]) -> Proveedor:
    catalogos = opciones.get("catalogos") or {}
    if pid in catalogos:
        nombre = {"ara": "Tiendas Ara"}.get(pid, pid.capitalize())
        return ProveedorCatalogo(pid, f"{nombre} (catálogo)", Path(catalogos[pid]))
    return PROVEEDORES_WEB[pid]()


# --------------------------------------------------------------------- nodos


async def interpretar(estado: Estado, config: RunnableConfig) -> dict:
    llm = _llm(config)
    avisos = []
    if llm is not None:
        try:
            solicitud = await interpretar_con_llm(estado["texto"], llm)
        except Exception as e:
            avisos.append(f"El LLM no pudo interpretar la solicitud ({type(e).__name__}); se usaron reglas")
            solicitud = interpretar_con_reglas(estado["texto"])
    else:
        avisos.append("Sin LLM configurado: interpretación y clasificación por reglas heurísticas")
        solicitud = interpretar_con_reglas(estado["texto"])
    return {"solicitud": solicitud, "avisos": avisos}


def validar_solicitud(estado: Estado) -> dict:
    return {"preguntas": validar(estado["solicitud"])}


def ruta_validacion(estado: Estado) -> str:
    if estado.get("preguntas") and estado.get("opciones", {}).get("interactivo") and not estado.get("aclarado"):
        return "aclarar"
    return "planificar"


def aclarar(estado: Estado) -> dict:
    """Pausa el grafo (human-in-the-loop) hasta que el usuario responda."""
    respuesta = interrupt({"preguntas": estado["preguntas"]})
    texto = f"{estado['texto']}\nAclaración del usuario: {respuesta}"
    return {"texto": texto, "aclarado": True}


def planificar(estado: Estado) -> dict:
    opciones = estado.get("opciones") or {}
    solicitud = aplicar_suposiciones(estado["solicitud"])
    avisos: list[str] = []
    disponibles = set(PROVEEDORES_WEB) | set(opciones.get("catalogos") or {})
    pedidos = opciones.get("proveedores") or [
        resolver_proveedor(p) or p for p in solicitud.proveedores_solicitados
    ]
    proveedores: list[str] = []
    for p in pedidos:
        if p in disponibles:
            proveedores.append(p)
        elif p in SIN_PRECIOS_EN_LINEA:
            solicitud.suposiciones.append(SIN_PRECIOS_EN_LINEA[p])
        else:
            solicitud.suposiciones.append(f"No hay un conector para '{p}'; no se consultó")
    for p in opciones.get("catalogos") or {}:
        if p not in proveedores:
            proveedores.append(p)
    if not pedidos:
        n = solicitud.numero_proveedores
        if n:
            elegidos = POR_DEFECTO[: max(2, n)]
            proveedores = list(dict.fromkeys(proveedores + elegidos))
            solicitud.suposiciones.append(
                f"Se pidió consultar {n} supermercado(s) sin nombrarlos: se eligieron "
                f"{', '.join(PROVEEDORES_WEB[p].nombre for p in elegidos)} (cadena tradicional y cadena de descuento "
                "con tienda en línea y cobertura nacional)"
            )
        else:
            proveedores = list(dict.fromkeys(proveedores + POR_DEFECTO))
    elif len(proveedores) < 2:
        extra = [p for p in POR_DEFECTO if p not in proveedores][: 2 - len(proveedores)]
        proveedores += extra
        solicitud.suposiciones.append(
            f"Se requieren al menos dos proveedores: se añadió {', '.join(PROVEEDORES_WEB[p].nombre for p in extra)}"
        )
    proveedores = list(dict.fromkeys(proveedores))
    instancias = {p: instanciar(p, opciones) for p in proveedores}
    return {
        "solicitud": solicitud,
        "proveedores": proveedores,
        "nombres": {p: i.nombre for p, i in instancias.items()},
        "mecanismos": {p: i.mecanismo for p, i in instancias.items()},
        "avisos": avisos,
    }


def repartir_consultas(estado: Estado) -> list[Send]:
    """Fan-out: una rama paralela por proveedor."""
    return [
        Send("consultar_proveedor", {"proveedor": p, "solicitud": estado["solicitud"], "opciones": estado.get("opciones") or {}})
        for p in estado["proveedores"]
    ]


async def consultar_proveedor(estado: EstadoConsulta, config: RunnableConfig) -> dict:
    proveedor = instanciar(estado["proveedor"], estado["opciones"])
    nav = _navegador(config)
    resultados = await asyncio.gather(*(proveedor.consultar(item, nav) for item in estado["solicitud"].items))
    return {"resultados": list(resultados)}


async def evaluar(estado: Estado, config: RunnableConfig) -> dict:
    ofertas = [o for r in estado["resultados"] for o in r.ofertas]
    evaluadas, avisos = await evaluar_todo(estado["solicitud"].items, ofertas, _llm(config))
    return {"evaluadas": evaluadas, "avisos": avisos}


def presupuestar(estado: Estado) -> dict:
    opciones = estado.get("opciones") or {}
    por_proveedor, combinado = construir_presupuestos(
        estado["solicitud"], estado["evaluadas"], estado["proveedores"], estado["nombres"],
        aceptar_sustitutos=bool(opciones.get("aceptar_sustitutos")),
    )
    return {"presupuestos": por_proveedor, "combinado": combinado,
            "decision": elegir_recomendacion(por_proveedor, combinado)}


def _recomendacion_reglas(estado: Estado) -> str:
    decision = estado["decision"]
    if not decision.get("opcion"):
        return "No fue posible recomendar una opción: no se encontraron ofertas comparables suficientes."
    texto = [f"Se recomienda la opción **{decision['opcion']}**."]
    texto += [f"- {m}" for m in decision["motivos"]]
    texto.append("- El total final no está confirmado mientras el costo de envío no se verifique en el comercio.")
    return "\n".join(texto)


def recomendar(estado: Estado) -> dict:
    return {"recomendacion": _recomendacion_reglas(estado)}


def informe(estado: Estado) -> dict:
    md = generar_informe(
        estado["solicitud"], estado["proveedores"], estado["nombres"], estado["mecanismos"], estado["resultados"],
        estado["evaluadas"], estado["presupuestos"], estado.get("combinado"), estado["recomendacion"],
        list(dict.fromkeys(estado.get("avisos") or [])), datetime.now().astimezone(),
    )
    return {"informe": md}


# --------------------------------------------------------------------- grafo


def crear_checkpointer() -> MemorySaver:
    """Checkpointer en memoria que admite de forma explícita los modelos Pydantic del estado.

    LangGraph advierte (y en versiones futuras bloquea) la deserialización de tipos no registrados.
    """
    tipos = [t for t in vars(modelos).values()
             if isinstance(t, type) and issubclass(t, BaseModel) and t.__module__ == modelos.__name__]
    return MemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=tipos))


def construir_grafo(checkpointer: Any | None = None):
    g = StateGraph(Estado)
    g.add_node("interpretar", interpretar)
    g.add_node("validar", validar_solicitud)
    g.add_node("aclarar", aclarar)
    g.add_node("planificar", planificar)
    g.add_node("consultar_proveedor", consultar_proveedor)
    g.add_node("evaluar", evaluar)
    g.add_node("presupuestar", presupuestar)
    g.add_node("recomendar", recomendar)
    g.add_node("informe", informe)

    g.add_edge(START, "interpretar")
    g.add_edge("interpretar", "validar")
    g.add_conditional_edges("validar", ruta_validacion, ["aclarar", "planificar"])
    g.add_edge("aclarar", "interpretar")
    g.add_conditional_edges("planificar", repartir_consultas, ["consultar_proveedor"])
    g.add_edge("consultar_proveedor", "evaluar")
    g.add_edge("evaluar", "presupuestar")
    g.add_edge("presupuestar", "recomendar")
    g.add_edge("recomendar", "informe")
    g.add_edge("informe", END)
    return g.compile(checkpointer=checkpointer)


# Grafo sin checkpointer para LangGraph Studio / `langgraph dev` (el servidor aporta el suyo)
grafo = construir_grafo()
