"""Modelos de datos compartidos por los nodos del grafo."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

UnidadBase = Literal["g", "ml", "und", "empaque"]
Disponibilidad = Literal["disponible", "agotado", "desconocida"]
TipoFuente = Literal["pagina_web_renderizada", "datos_estructurados_jsonld", "catalogo_referencia"]
TipoCoincidencia = Literal["equivalente", "sustituto", "no_comparable", "no_relacionado"]


# ---------------------------------------------------------------------------
# Solicitud del usuario
# ---------------------------------------------------------------------------


class ItemSolicitado(BaseModel):
    """Un producto de la lista de compra, ya estructurado."""

    id: str = Field(description="Identificador corto en minúsculas, p. ej. 'arroz'")
    producto: str = Field(description="Nombre genérico del producto, p. ej. 'arroz blanco'")
    texto_original: str = Field(default="", description="Fragmento del mensaje del usuario que lo origina")
    cantidad: float | None = Field(default=None, description="Cantidad pedida; None si el usuario no la dio")
    unidad: str | None = Field(default=None, description="Unidad tal como la expresó el usuario: kg, g, lb, l, ml, und, docena, paquete…")
    marca: str | None = Field(default=None, description="Marca preferida o exigida")
    marca_obligatoria: bool = Field(default=False, description="True si el usuario exige esa marca ('solo Diana')")
    presentacion: str | None = Field(default=None, description="Presentación pedida: '1 kg', 'bolsa', 'garrafa', 'six pack'…")
    atributos: list[str] = Field(default_factory=list, description="Especificaciones exigidas: 'integral', 'girasol', 'deslactosada'…")
    excluir: list[str] = Field(default_factory=list, description="Características que el usuario rechaza: 'light', 'saborizado'…")
    terminos_busqueda: list[str] = Field(default_factory=list, description="1-3 palabras que deben aparecer en el nombre del producto publicado ('arroz', 'aceite', 'azucar'), en singular y sin marca")
    pistas_categoria: list[str] = Field(default_factory=list, description="Nombres de categoría de supermercado donde suele estar el producto ('granos', 'endulzantes', 'lacteos')")


class CostoAdicional(BaseModel):
    concepto: str
    proveedor: str | None = None
    valor: float | None = Field(default=None, description="None cuando el valor no se conoce")
    estado: Literal["confirmado_por_usuario", "publicado_por_proveedor", "no_verificado"] = "no_verificado"
    fuente: str | None = None


class Solicitud(BaseModel):
    texto_original: str
    items: list[ItemSolicitado]
    ciudad: str | None = None
    proveedores_solicitados: list[str] = Field(default_factory=list)
    numero_proveedores: int | None = None
    costos_adicionales: list[CostoAdicional] = Field(default_factory=list)
    suposiciones: list[str] = Field(default_factory=list)
    aclaraciones_pendientes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Ofertas obtenidas de proveedores
# ---------------------------------------------------------------------------


class Oferta(BaseModel):
    """Un producto publicado por un proveedor, con la evidencia de dónde salió."""

    proveedor: str
    item_id: str
    nombre: str
    marca: str | None = None
    presentacion_texto: str | None = None
    contenido: float | None = Field(default=None, description="Contenido total del empaque en unidad_base")
    unidad_base: UnidadBase | None = None
    precio: float | None = Field(default=None, description="Precio de venta al público sin condiciones especiales")
    precio_regular: float | None = Field(default=None, description="Precio antes de descuento, si se publica")
    precio_condicionado: float | None = Field(default=None, description="Precio sujeto a medio de pago o membresía")
    condicion_precio: str | None = None
    moneda: str = "COP"
    url: str
    sku: str | None = None
    fuente_tipo: TipoFuente
    url_listado: str | None = Field(default=None, description="Página de categoría o catálogo consultado")
    fecha_consulta: datetime
    disponibilidad: Disponibilidad = "desconocida"
    vendedor: str | None = None
    notas: list[str] = Field(default_factory=list)

    @property
    def clave(self) -> str:
        return f"{self.proveedor}|{self.sku or self.url}"

    @property
    def precio_por_unidad_base(self) -> float | None:
        if self.precio is None or not self.contenido:
            return None
        return self.precio / self.contenido


class FuenteConsultada(BaseModel):
    url: str
    fecha_consulta: datetime
    productos_leidos: int = 0


class ResultadoConsulta(BaseModel):
    """Lo que devuelve un proveedor para un ítem, incluidos errores y URLs visitadas."""

    proveedor: str
    item_id: str
    ofertas: list[Oferta] = Field(default_factory=list)
    fuentes: list[FuenteConsultada] = Field(default_factory=list)
    errores: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Comparación y presupuesto
# ---------------------------------------------------------------------------


class OfertaEvaluada(BaseModel):
    oferta: Oferta
    tipo: TipoCoincidencia
    motivo: str
    precio_unitario: float | None = Field(default=None, description="COP por kg, L o unidad")
    unidad_comparacion: str | None = None


class Linea(BaseModel):
    """Compra concreta de un ítem en un proveedor: uno o varios empaques."""

    item_id: str
    proveedor: str
    empaques: list[tuple[Oferta, int]]
    cantidad_cubierta: float
    cantidad_solicitada: float
    unidad_base: UnidadBase
    costo: float
    tipo: TipoCoincidencia
    notas: list[str] = Field(default_factory=list)


class Presupuesto(BaseModel):
    nombre: str
    proveedores: list[str]
    lineas: list[Linea] = Field(default_factory=list)
    items_sin_cotizacion: list[str] = Field(default_factory=list)
    subtotal_productos: float = 0
    costos_adicionales: list[CostoAdicional] = Field(default_factory=list)
    total_confirmado: float | None = Field(default=None, description="Solo se calcula si todos los costos adicionales son conocidos")
    cobertura_completa: bool = False
    condiciones_no_verificadas: list[str] = Field(default_factory=list)
