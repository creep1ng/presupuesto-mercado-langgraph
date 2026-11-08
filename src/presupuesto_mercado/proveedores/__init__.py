"""Registro de proveedores disponibles."""

from __future__ import annotations

from ..texto import normalizar
from .base import Proveedor
from .catalogo import ProveedorCatalogo
from .grupo_exito import Carulla, Exito
from .jumbo import Jumbo
from .vtex_jsonld import D1, Olimpica

PROVEEDORES_WEB: dict[str, type[Proveedor]] = {
    "exito": Exito,
    "carulla": Carulla,
    "jumbo": Jumbo,
    "d1": D1,
    "olimpica": Olimpica,
}

# Proveedores por defecto cuando el usuario no indica ninguno.
POR_DEFECTO = ["exito", "d1", "jumbo", "olimpica", "carulla"]

# Comercios conocidos sin precios publicados en línea.
SIN_PRECIOS_EN_LINEA = {
    "ara": "Tiendas Ara no tiene tienda en línea con precios publicados. Puede incluirse importando un "
           "catálogo de referencia con --catalogo ara=archivo.csv",
}

_ALIAS = {
    "exito": "exito", "almacenes exito": "exito", "carulla": "carulla", "jumbo": "jumbo",
    "d1": "d1", "tiendas d1": "d1", "olimpica": "olimpica", "ara": "ara",
    "tiendas ara": "ara",
}


def resolver_proveedor(nombre: str) -> str | None:
    return _ALIAS.get(normalizar(nombre).strip())


__all__ = [
    "PROVEEDORES_WEB", "POR_DEFECTO", "SIN_PRECIOS_EN_LINEA", "Proveedor", "ProveedorCatalogo",
    "resolver_proveedor",
]
