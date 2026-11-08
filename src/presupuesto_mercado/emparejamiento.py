"""Normalización y clasificación de ofertas frente a cada ítem solicitado.

Se combinan dos pasos:
1. El LLM decide si cada producto encontrado es equivalente, sustituto
   aproximado o no relacionado con lo pedido (entiende variedades, marcas y usos).
2. Reglas deterministas que el LLM no puede saltarse: sin precio o sin
   presentación interpretable, o con unidad incompatible, la oferta es "no
   comparable"; si la marca es obligatoria y no coincide, no puede ser
   "equivalente".
"""

from __future__ import annotations

import asyncio
from typing import Literal

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from .modelos import ItemSolicitado, Oferta, OfertaEvaluada
from .texto import contiene_termino, normalizar, raices, tokens
from .unidades import cantidad_pedida, precio_unitario

PROMPT_CLASIFICACION = """Eres un comprador experto de supermercado en Colombia. Para el producto solicitado, \
clasifica cada producto publicado por los comercios:

- "equivalente": es el mismo tipo de producto y cumple todas las especificaciones exigidas (marca obligatoria, \
atributos, exclusiones). El tamaño del empaque NO importa (se normaliza después). Si el usuario no especificó \
variedad, la variedad estándar o más común es equivalente (arroz blanco, aceite vegetal/de cocina, azúcar blanca, \
leche entera, huevos de gallina), incluidas sus versiones de marca propia, "premium" o fortificadas.
- "sustituto": satisface la misma necesidad pero difiere en algo relevante: otra variedad (integral, morena, oliva, \
deslactosada cuando no se pidió), otra marca cuando el usuario expresó preferencia de marca, o un formato distinto \
al pedido.
- "no_relacionado": es otro producto aunque comparta palabras (harina de arroz, arroz con pollo, aceite corporal, \
galletas con azúcar), un combo con otros productos o un accesorio.

Responde una clasificación por cada índice, con un motivo breve en español (máximo 15 palabras)."""


class ClasificacionLLM(BaseModel):
    indice: int
    tipo: Literal["equivalente", "sustituto", "no_relacionado"]
    motivo: str


class ClasificacionesLLM(BaseModel):
    """Clasificación de los productos publicados frente al producto solicitado."""

    clasificaciones: list[ClasificacionLLM] = Field(default_factory=list)


def _describir_item(item: ItemSolicitado) -> str:
    partes = [f"Producto solicitado: {item.producto}"]
    if item.cantidad is not None:
        partes.append(f"Cantidad: {item.cantidad:g} {item.unidad or ''}".strip())
    if item.marca:
        partes.append(f"Marca: {item.marca} ({'obligatoria' if item.marca_obligatoria else 'preferida, no obligatoria'})")
    if item.presentacion:
        partes.append(f"Presentación pedida: {item.presentacion}")
    if item.atributos:
        partes.append(f"Debe cumplir: {', '.join(item.atributos)}")
    if item.excluir:
        partes.append(f"Excluir: {', '.join(item.excluir)}")
    return "\n".join(partes)


async def clasificar_con_llm(
    item: ItemSolicitado, ofertas: list[Oferta], llm: BaseChatModel
) -> dict[int, tuple[str, str]]:
    if not ofertas:
        return {}
    lista = "\n".join(
        f"{i}. {o.nombre}" + (f" (marca: {o.marca})" if o.marca else "") for i, o in enumerate(ofertas)
    )
    estructurado = llm.with_structured_output(ClasificacionesLLM, method="json_schema")
    respuesta = await estructurado.ainvoke(
        [("system", PROMPT_CLASIFICACION), ("human", f"{_describir_item(item)}\n\nProductos publicados:\n{lista}")]
    )
    return {c.indice: (c.tipo, c.motivo) for c in respuesta.clasificaciones}


# Palabras que suelen indicar una variedad distinta de la estándar
_VARIEDADES = {
    "integral", "oliva", "coco", "aguacate", "morena", "morenas", "light", "deslactosada", "descremada",
    "semidescremada", "saborizada", "saborizado", "sabor", "chocolate", "fresa", "vainilla", "organico", "organica",
    "parbolizado", "basmati", "jazmin", "arborio", "sushi", "salvaje", "pulverizada", "pulverizado", "glass",
    "codorniz", "sesamo", "canola", "girasol", "soya", "mezcla", "esparcible", "vegetal", "dietetica", "stevia",
    "endulzante", "sucralosa", "panela", "liquida", "liquido", "polvo",
}
_EMPAQUES = {"bolsa", "cubeta", "paquete", "pack", "caja", "botella", "garrafa", "paca", "tarro", "lata", "frasco",
             "doypack", "sixpack"}
_VARIEDADES_ESTANDAR = {"vegetal", "blanco", "blanca", "entera", "premium"}


def clasificar_con_reglas(item: ItemSolicitado, oferta: Oferta) -> tuple[str, str]:
    nombre = normalizar(oferta.nombre)
    toks = tokens(nombre)
    terminos = item.terminos_busqueda or [item.producto]
    # En los nombres comerciales el tipo de producto va primero ("Arroz DIANA…"), salvo el empaque ("Bolsa ARROZ…")
    principales = [t for t in toks if t not in _EMPAQUES]
    if not any(contiene_termino(" ".join(principales[:len(tokens(t))]), t) for t in terminos):
        return "no_relacionado", "El producto buscado no es el tipo principal del artículo"
    pedidos = raices(" ".join([item.producto, *item.atributos, item.presentacion or ""]))
    for excl in item.excluir:
        if contiene_termino(nombre, excl):
            return "sustituto", f"Tiene una característica excluida: {excl}"
    for attr in item.atributos:
        if not contiene_termino(nombre, attr):
            return "sustituto", f"No indica el atributo exigido: {attr}"
    variedades = {t for t in toks if t in _VARIEDADES and t not in _VARIEDADES_ESTANDAR and t not in pedidos}
    if variedades:
        return "sustituto", f"Variedad distinta de la estándar: {', '.join(sorted(variedades))}"
    if item.marca and not contiene_termino(f"{nombre} {oferta.marca or ''}", item.marca):
        return "sustituto", f"Marca distinta de la preferida ({item.marca})"
    return "equivalente", "Mismo tipo de producto (clasificación por reglas)"


def _cumple_marca(item: ItemSolicitado, oferta: Oferta) -> bool:
    return not item.marca or contiene_termino(f"{oferta.nombre} {oferta.marca or ''}", item.marca)


def normalizar_y_validar(item: ItemSolicitado, oferta: Oferta, tipo: str, motivo: str) -> OfertaEvaluada:
    """Aplica las reglas deterministas de comparabilidad y calcula el precio unitario."""
    pedido = cantidad_pedida(item.cantidad, item.unidad, item.presentacion)
    if tipo != "no_relacionado":
        if oferta.precio is None:
            tipo, motivo = "no_comparable", "El listado no publica un precio general (sin condiciones)"
        elif pedido is None:
            tipo, motivo = "no_comparable", "La cantidad solicitada no tiene una unidad reconocible"
        elif pedido.unidad_base != "empaque" and (oferta.contenido is None or oferta.unidad_base is None):
            tipo, motivo = "no_comparable", "El nombre publicado no indica contenido ni unidad de medida"
        elif pedido.unidad_base != "empaque" and oferta.unidad_base != pedido.unidad_base:
            tipo, motivo = "no_comparable", (
                f"Unidad incompatible: se pidió en {pedido.unidad_base} y se publica en {oferta.unidad_base}"
            )
        elif tipo == "equivalente" and item.marca_obligatoria and not _cumple_marca(item, oferta):
            tipo, motivo = "sustituto", f"No es de la marca exigida ({item.marca})"
    pu, etiqueta = None, None
    if tipo in ("equivalente", "sustituto") and oferta.precio is not None:
        if pedido is not None and pedido.unidad_base == "empaque":
            pu, etiqueta = oferta.precio, "empaque"
        elif oferta.contenido and oferta.unidad_base:
            pu, etiqueta = precio_unitario(oferta.precio, oferta.contenido, oferta.unidad_base)
    return OfertaEvaluada(oferta=oferta, tipo=tipo, motivo=motivo, precio_unitario=pu, unidad_comparacion=etiqueta)


async def evaluar_item(
    item: ItemSolicitado, ofertas: list[Oferta], llm: BaseChatModel | None
) -> tuple[list[OfertaEvaluada], list[str]]:
    avisos: list[str] = []
    decisiones: dict[int, tuple[str, str]] = {}
    if llm is not None and ofertas:
        try:
            decisiones = await clasificar_con_llm(item, ofertas, llm)
        except Exception as e:
            avisos.append(f"Clasificación con LLM falló para {item.producto} ({type(e).__name__}); se usaron reglas")
    evaluadas = []
    for i, oferta in enumerate(ofertas):
        tipo, motivo = decisiones.get(i) or clasificar_con_reglas(item, oferta)
        evaluadas.append(normalizar_y_validar(item, oferta, tipo, motivo))
    return evaluadas, avisos


async def evaluar_todo(
    items: list[ItemSolicitado], ofertas: list[Oferta], llm: BaseChatModel | None
) -> tuple[dict[str, list[OfertaEvaluada]], list[str]]:
    por_item = {item.id: [o for o in ofertas if o.item_id == item.id] for item in items}
    resultados = await asyncio.gather(*(evaluar_item(item, por_item[item.id], llm) for item in items))
    evaluadas = {item.id: r[0] for item, r in zip(items, resultados)}
    avisos = [a for r in resultados for a in r[1]]
    return evaluadas, avisos
