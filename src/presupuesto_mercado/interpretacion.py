"""Interpretación de la solicitud en lenguaje natural y validación de datos faltantes."""

from __future__ import annotations

import re

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from .modelos import CostoAdicional, ItemSolicitado, Solicitud
from .proveedores import resolver_proveedor
from .texto import normalizar, slug
from .unidades import cantidad_pedida

PROMPT_INTERPRETACION = """Eres el módulo de interpretación de un sistema que cotiza compras de mercado \
(alimentos, aseo y artículos del hogar) en supermercados de Colombia.

Convierte la solicitud del usuario en una lista estructurada. Reglas:
- Un ítem por producto. `producto` es el nombre genérico en español ("arroz blanco", "aceite vegetal").
- `cantidad` y `unidad` exactamente como las pidió el usuario (5 y "kg"; 2 y "litros"; 1 y "docena").
  Si dice "2 bolsas de 1 kg", usa cantidad 2, unidad "kg" y presentacion "1 kg". Si dice "1 paca de papel de 12 \
rollos", usa cantidad 12, unidad "rollos" y presentacion "12 rollos" (la cantidad total en la unidad contable). \
Si no da cantidad, déjala vacía.
- `marca`: solo si la menciona. `marca_obligatoria` = true únicamente si la exige ("solo", "tiene que ser", "únicamente").
- `atributos`: especificaciones que el producto debe cumplir (integral, girasol, deslactosada, AA, sin sal…).
- `excluir`: características que rechaza ("que no sea light").
- `terminos_busqueda`: 1 a 3 palabras que aparecerían en el nombre comercial del producto, en singular, sin tildes \
y sin marca (para "aceite de cocina": ["aceite"]; para "jabón para ropa": ["detergente", "jabon"]).
- `pistas_categoria`: subsecciones específicas de supermercado donde suele estar ("granos", "endulzantes", \
"lacteos", "detergentes"); no uses departamentos amplios como "despensa" o "mercado".
- `ciudad`: ciudad o municipio de entrega/compra si la menciona.
- `proveedores_solicitados`: solo los supermercados que el usuario pide expresamente consultar o usar, tal como \
los escribe (Éxito, D1, Jumbo, Olímpica…). Mencionar el costo de domicilio de una tienda NO es pedir consultarla.
- `numero_proveedores`: si pide consultar un número de supermercados sin nombrarlos ("consulta dos supermercados").
- `costos_adicionales`: solo costos que el usuario afirma conocer (p. ej. "el domicilio de D1 me cuesta 5.000").
- `suposiciones`: interpretaciones no literales que hiciste (p. ej. "'una libra' se tomó como 500 g").
No inventes datos que el usuario no dio."""


class ItemLLM(BaseModel):
    producto: str
    texto_original: str = ""
    cantidad: float | None = None
    unidad: str | None = None
    marca: str | None = None
    marca_obligatoria: bool = False
    presentacion: str | None = None
    atributos: list[str] = Field(default_factory=list)
    excluir: list[str] = Field(default_factory=list)
    terminos_busqueda: list[str] = Field(default_factory=list)
    pistas_categoria: list[str] = Field(default_factory=list)


class CostoLLM(BaseModel):
    concepto: str
    proveedor: str | None = None
    valor: float


class InterpretacionLLM(BaseModel):
    """Solicitud de compra estructurada."""

    items: list[ItemLLM]
    ciudad: str | None = None
    proveedores_solicitados: list[str] = Field(default_factory=list)
    numero_proveedores: int | None = None
    costos_adicionales: list[CostoLLM] = Field(default_factory=list)
    suposiciones: list[str] = Field(default_factory=list)


def _a_solicitud(texto: str, datos: InterpretacionLLM) -> Solicitud:
    items: list[ItemSolicitado] = []
    usados: set[str] = set()
    for it in datos.items:
        base = slug(it.terminos_busqueda[0] if it.terminos_busqueda else it.producto) or "item"
        ident, n = base, 2
        while ident in usados:
            ident, n = f"{base}-{n}", n + 1
        usados.add(ident)
        items.append(ItemSolicitado(id=ident, **it.model_dump()))
    costos = []
    for c in datos.costos_adicionales:
        prov = resolver_proveedor(c.proveedor) if c.proveedor else None
        costos.append(
            CostoAdicional(concepto=c.concepto, proveedor=prov or c.proveedor, valor=c.valor,
                           estado="confirmado_por_usuario", fuente="Indicado por el usuario en la solicitud")
        )
    return Solicitud(
        texto_original=texto,
        items=items,
        ciudad=datos.ciudad,
        proveedores_solicitados=datos.proveedores_solicitados,
        numero_proveedores=datos.numero_proveedores,
        costos_adicionales=costos,
        suposiciones=list(datos.suposiciones),
    )


async def interpretar_con_llm(texto: str, llm: BaseChatModel) -> Solicitud:
    estructurado = llm.with_structured_output(InterpretacionLLM, method="json_schema")
    datos = await estructurado.ainvoke([("system", PROMPT_INTERPRETACION), ("human", texto)])
    return _a_solicitud(texto, datos)


# ---------------------------------------------------------------------------
# Intérprete de respaldo sin LLM (reglas)
# ---------------------------------------------------------------------------

CIUDADES = [
    "Bogotá", "Medellín", "Cali", "Barranquilla", "Cartagena", "Bucaramanga", "Pereira", "Manizales",
    "Santa Marta", "Cúcuta", "Ibagué", "Villavicencio", "Pasto", "Montería", "Neiva", "Armenia", "Popayán",
    "Valledupar", "Sincelejo", "Tunja", "Envigado", "Itagüí", "Bello", "Rionegro", "Soacha", "Chía", "Sabaneta",
]
_PROVEEDORES_TEXTO = ["exito", "carulla", "jumbo", "d1", "olimpica", "ara"]
_RE_ITEM = re.compile(
    r"(\d+(?:[.,]\d+)?|una?|dos|tres|cuatro|cinco|seis|media)\s*"
    r"(kg|kilos?|kilogramos?|g|gr|gramos?|lb|libras?|l|lt|litros?|ml|mililitros?|und|unidades?|docenas?|"
    r"paquetes?|bolsas?|botellas?|cajas?|latas?|rollos?|frascos?)?\s+(?:de\s+)?"
    r"([a-zñ][a-zñ ]+?)(?=,|\.|;| y \d| y una?\b| y (?:dos|tres|cuatro|cinco|seis)\b|$| consult| prepar| para | en )"
)
_NO_PRODUCTOS = {"supermercado", "supermercados", "proveedor", "proveedores", "tienda", "tiendas", "presupuesto",
                 "opcion", "opciones", "almacen", "almacenes", "cadena", "cadenas"}
_RE_NUM_PROVEEDORES = re.compile(r"\b(\d+|dos|tres|cuatro|cinco)\s+(?:supermercados|tiendas|proveedores|almacenes|cadenas)")
_PALABRAS_NUM = {"un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "media": 0.5}


def interpretar_con_reglas(texto: str) -> Solicitud:
    t = normalizar(texto)
    items: list[ItemSolicitado] = []
    for m in _RE_ITEM.finditer(t):
        cant_txt, unidad, producto = m.group(1), m.group(2), m.group(3).strip()
        producto = re.sub(r"^(necesito|quiero|comprar)\s+", "", producto)
        producto = re.sub(r"\s+(y|e|o)$", "", producto)
        if not producto or producto.split()[0] in _NO_PRODUCTOS:
            continue
        cantidad = _PALABRAS_NUM.get(cant_txt) or float(cant_txt.replace(",", "."))
        termino = producto.split()[0]
        ident = slug(termino)
        if any(i.id == ident for i in items):
            continue
        items.append(
            ItemSolicitado(
                id=ident, producto=producto, texto_original=m.group(0).strip(), cantidad=cantidad,
                unidad=unidad, terminos_busqueda=[termino],
            )
        )
    ciudad = next((c for c in CIUDADES if normalizar(c) in t), None)
    proveedores = [p for p in _PROVEEDORES_TEXTO if re.search(rf"\b{p}\b", t)]
    m = _RE_NUM_PROVEEDORES.search(t)
    return Solicitud(
        texto_original=texto,
        items=items,
        ciudad=ciudad,
        proveedores_solicitados=proveedores,
        numero_proveedores=(_PALABRAS_NUM.get(m.group(1)) or int(m.group(1))) if m else None,
        suposiciones=["La solicitud se interpretó con reglas (sin LLM); revise la lista de productos"],
    )


# ---------------------------------------------------------------------------
# Validación: datos imprescindibles
# ---------------------------------------------------------------------------


def validar(solicitud: Solicitud) -> list[str]:
    """Devuelve preguntas de aclaración para datos imprescindibles que faltan."""
    preguntas = []
    if not solicitud.items:
        preguntas.append("No identifiqué productos en la solicitud. ¿Qué productos y cantidades necesita?")
    for item in solicitud.items:
        if item.cantidad is None:
            preguntas.append(f"¿Qué cantidad de {item.producto} necesita (por ejemplo, en kg, litros o unidades)?")
        elif cantidad_pedida(item.cantidad, item.unidad, item.presentacion) is None:
            preguntas.append(f"No reconozco la unidad '{item.unidad}' para {item.producto}. ¿Puede expresarla en kg, g, L, mL o unidades?")
    if not solicitud.ciudad:
        preguntas.append("¿En qué ciudad se hará la compra o la entrega?")
    return preguntas


def aplicar_suposiciones(solicitud: Solicitud) -> Solicitud:
    """Completa los datos faltantes con suposiciones explícitas (modo no interactivo)."""
    s = solicitud.model_copy(deep=True)
    for item in s.items:
        if item.cantidad is None:
            item.cantidad, item.unidad = 1, "empaque"
            s.suposiciones.append(f"No se indicó cantidad de {item.producto}: se cotiza 1 empaque (el de menor precio disponible)")
        elif cantidad_pedida(item.cantidad, item.unidad, item.presentacion) is None:
            s.suposiciones.append(f"Unidad '{item.unidad}' de {item.producto} no reconocida: se cotiza por empaque")
            item.unidad = "empaque"
        pedido = cantidad_pedida(item.cantidad, item.unidad, item.presentacion)
        if pedido and pedido.nota and pedido.nota not in " ".join(s.suposiciones):
            s.suposiciones.append(f"{item.producto}: {pedido.nota}")
    if not s.ciudad:
        s.suposiciones.append(
            "No se indicó ciudad: se usan los precios que cada sitio web muestra sin seleccionar tienda ni dirección"
        )
    return s
