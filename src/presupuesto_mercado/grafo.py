"""Flujo LangGraph: interpretar → validar → (aclarar) → planificar → consultar (en paralelo)
→ evaluar → presupuestar → recomendar → informe."""

from __future__ import annotations

import asyncio
import json
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
from .reporte import cop, generar_informe


class Estado(TypedDict, total=False):
    texto: str
    opciones: dict[str, Any]
    solicitud: Solicitud
    preguntas: list[str]
    aclarado: bool
    proveedores: list[str]
    presentar: int | None  # "consulte N supermercados": se consultan todos y se presentan los N mejores
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
    presentar = None
    if not pedidos:
        proveedores = list(dict.fromkeys(proveedores + POR_DEFECTO))
        n = solicitud.numero_proveedores
        if n and n < len(proveedores):
            presentar = max(2, n)
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
        "presentar": presentar,
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
    solicitud, nombres = estado["solicitud"], estado["nombres"]
    sustitutos = bool(opciones.get("aceptar_sustitutos"))
    por_proveedor, combinado = construir_presupuestos(
        solicitud, estado["evaluadas"], estado["proveedores"], nombres, aceptar_sustitutos=sustitutos,
    )
    n = estado.get("presentar")
    if n:
        # Se pidieron N supermercados sin nombrarlos: se consultaron todos y se presentan los N
        # con más productos cotizados y, a igual cobertura, menor subtotal.
        mejores = [p.proveedores[0] for p in sorted(
            (p for p in por_proveedor if p.lineas), key=lambda p: (-len(p.lineas), p.subtotal_productos))][:n]
        descartados = [nombres[p] for p in estado["proveedores"] if p not in mejores]
        solicitud = solicitud.model_copy(deep=True)
        solicitud.suposiciones.append(
            f"Se pidió comparar {solicitud.numero_proveedores} supermercado(s) sin nombrarlos: se consultaron "
            f"todos y se presentan los de mayor cobertura y menor subtotal "
            f"({', '.join(nombres[p] for p in mejores) or 'ninguno con ofertas'}). "
            f"No se presentan: {', '.join(descartados) or 'ninguno'}."
        )
        por_proveedor, combinado = construir_presupuestos(
            solicitud, estado["evaluadas"], mejores, nombres, aceptar_sustitutos=sustitutos,
        )
    return {"solicitud": solicitud, "presupuestos": por_proveedor, "combinado": combinado,
            "decision": elegir_recomendacion(por_proveedor, combinado)}


PROMPT_RECOMENDACION = """Redacta en español la explicación de la recomendación de un presupuesto de mercado.
- Trata al usuario de "usted". Sin títulos.
- Usa SOLO los datos del JSON: no inventes precios, tiendas ni costos, y no cambies la opción elegida en "decision".
- Los precios son los publicados en los sitios web sin seleccionar tienda ni dirección: NO afirmes que corresponden \
a la ciudad del usuario; advierte que pueden variar allí.
- En 1 o 2 párrafos breves explica por qué se recomienda la opción, con su subtotal de productos, y deja claro que \
el total final no está confirmado si el envío no está verificado.
- Luego, en una lista de máximo 5 puntos, señala riesgos o limitaciones concretas (productos sin cotizar, \
sustitutos, presentaciones poco prácticas, precios con tarjeta no aplicados)."""


def _resumen_para_llm(estado: Estado) -> dict:
    nombres_items = {i.id: i.producto for i in estado["solicitud"].items}

    def resumen(p: Presupuesto) -> dict:
        return {
            "opcion": p.nombre,
            "productos_cubiertos": f"{len(p.lineas)} de {len(estado['solicitud'].items)}",
            "cobertura_completa": p.cobertura_completa,
            "subtotal_productos": cop(p.subtotal_productos),
            "envio": [f"{c.concepto}: {cop(c.valor) if c.valor is not None else 'no verificado'}" for c in p.costos_adicionales],
            "total_final": cop(p.total_confirmado) if p.total_confirmado is not None else "no confirmado",
            "sin_cotizacion": [nombres_items.get(i, i) for i in p.items_sin_cotizacion],
            "lineas": [
                {"producto": nombres_items.get(l.item_id, l.item_id), "tipo": l.tipo, "costo": cop(l.costo),
                 "articulos": [f"{n} x {o.nombre} ({cop(o.precio)})" for o, n in l.empaques], "notas": l.notas}
                for l in p.lineas
            ],
        }

    return {
        "ciudad": estado["solicitud"].ciudad,
        "decision": estado["decision"],
        "presupuestos": [resumen(p) for p in estado["presupuestos"]],
        "combinado": resumen(estado["combinado"]) if estado.get("combinado") else None,
        "suposiciones": estado["solicitud"].suposiciones,
    }


def _recomendacion_reglas(estado: Estado) -> str:
    decision = estado["decision"]
    if not decision.get("opcion"):
        texto = ["No se puede recomendar un presupuesto: ninguna opción cubre la lista completa."]
        texto += [f"- {m}" for m in decision["motivos"][1:]]
        texto.append("- Revise en la sección de productos sin cotización si faltó la oferta o falló la consulta.")
        return "\n".join(texto)
    texto = [f"Se recomienda la opción **{decision['opcion']}**."]
    texto += [f"- {m}" for m in decision["motivos"]]
    texto.append("- El total final no está confirmado mientras el costo de envío no se verifique en el comercio.")
    return "\n".join(texto)


async def recomendar(estado: Estado, config: RunnableConfig) -> dict:
    llm = _llm(config)
    base = _recomendacion_reglas(estado)
    if llm is None or not estado["decision"].get("opcion"):
        return {"recomendacion": base}
    try:
        respuesta = await llm.ainvoke([
            ("system", PROMPT_RECOMENDACION),
            ("human", json.dumps(_resumen_para_llm(estado), ensure_ascii=False, indent=1)),
        ])
        explicacion = respuesta.content if isinstance(respuesta.content, str) else "".join(
            b.get("text", "") for b in respuesta.content if isinstance(b, dict)
        )
        return {"recomendacion": f"{base}\n\n{explicacion.strip()}"}
    except Exception as e:
        return {"recomendacion": base, "avisos": [f"No se pudo redactar la recomendación con LLM ({type(e).__name__})"]}


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
