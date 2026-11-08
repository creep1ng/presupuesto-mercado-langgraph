"""D1 y Olímpica: datos estructurados schema.org (JSON-LD) de las páginas de categoría.

Ambos sitios publican en cada listado un bloque `ItemList` con `Product` y
`Offer` (precio, moneda, disponibilidad, vendedor). Es información pública pensada
para buscadores, así que se prefiere sobre el DOM. La página se abre con
Playwright para leer el documento tal como lo ve un navegador.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any

from ..modelos import ItemSolicitado, Oferta, TipoFuente
from ..navegador import Navegador
from ..unidades import extraer_presentacion
from .base import ProveedorPorCategorias

EXTRACTOR_JS = r"""() => [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => s.textContent)"""


def productos_jsonld(bloques: list[str]) -> list[dict[str, Any]]:
    productos: list[dict[str, Any]] = []
    for bloque in bloques:
        try:
            datos = json.loads(bloque)
        except (json.JSONDecodeError, TypeError):
            continue
        for doc in datos if isinstance(datos, list) else [datos]:
            if isinstance(doc, dict) and doc.get("@type") == "ItemList":
                for elemento in doc.get("itemListElement", []):
                    item = elemento.get("item") if isinstance(elemento, dict) else None
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        productos.append(item)
    return productos


def _oferta_principal(producto: dict[str, Any]) -> dict[str, Any]:
    oferta = producto.get("offers") or {}
    if isinstance(oferta, list):
        oferta = oferta[0] if oferta else {}
    internas = oferta.get("offers")
    if isinstance(internas, list) and internas:
        # AggregateOffer: se toma la oferta del vendedor principal (la primera)
        return {**internas[0], "lowPrice": oferta.get("lowPrice")}
    return oferta


class ProveedorJsonLd(ProveedorPorCategorias):
    fuente_tipo: TipoFuente = "datos_estructurados_jsonld"
    mecanismo = (
        "Página de categoría pública (descubierta en el sitemap) abierta con Playwright; se leen los datos "
        "estructurados schema.org ItemList/Product/Offer (JSON-LD) que el sitio publica para buscadores."
    )

    async def leer_listado(self, url: str, nav: Navegador) -> tuple[list[dict[str, Any]], datetime]:
        bloques, fecha = await nav.renderizar(
            url, EXTRACTOR_JS, esperar_selector='script[type="application/ld+json"]', nombre_extractor="jsonld-v1"
        )
        return productos_jsonld(bloques), fecha

    def a_oferta(self, crudo: dict[str, Any], item: ItemSolicitado, url_listado: str, fecha: datetime) -> Oferta | None:
        nombre = html.unescape(crudo.get("name") or "").strip()
        url = crudo.get("@id") or crudo.get("url")
        if not nombre or not url:
            return None
        oferta = _oferta_principal(crudo)
        precio = oferta.get("price") or oferta.get("lowPrice")
        disponibilidad_sch = (oferta.get("availability") or "").rsplit("/", 1)[-1].lower()
        disponibilidad = {"instock": "disponible", "outofstock": "agotado", "soldout": "agotado"}.get(
            disponibilidad_sch, "desconocida"
        )
        presentacion = extraer_presentacion(nombre, permitir_conteo_suelto=True)
        marca = crudo.get("brand")
        vendedor = (oferta.get("seller") or {}).get("name") if isinstance(oferta.get("seller"), dict) else None
        return Oferta(
            proveedor=self.id,
            item_id=item.id,
            nombre=nombre,
            marca=(marca.get("name") if isinstance(marca, dict) else marca) or None,
            presentacion_texto=presentacion.texto if presentacion else None,
            contenido=presentacion.contenido if presentacion else None,
            unidad_base=presentacion.unidad_base if presentacion else None,
            precio=float(precio) if precio not in (None, "") else None,
            moneda=oferta.get("priceCurrency") or "COP",
            url=self.absoluta(url),
            sku=str(oferta.get("sku") or crudo.get("sku") or "") or None,
            fuente_tipo=self.fuente_tipo,
            url_listado=url_listado,
            fecha_consulta=fecha,
            disponibilidad=disponibilidad,
            vendedor=vendedor,
        )


class D1(ProveedorJsonLd):
    id = "d1"
    nombre = "Tiendas D1"
    sitio = "https://www.d1.com.co"
    sitemaps_categorias = ["https://www.d1.com.co/sitemap/category-0.xml"]
    prefijos_excluidos = ("/moda", "/hogar/decohogar", "/hogar/cocina-y-mesa", "/extraordinario", "/donaciones")


class Olimpica(ProveedorJsonLd):
    id = "olimpica"
    nombre = "Olímpica"
    sitio = "https://www.olimpica.com"
    sitemaps_categorias = ["https://www.olimpica.com/sitemap/category-0.xml"]
    prefijos_permitidos = ("/supermercado/",)
    prefijos_excluidos = ("/supermercado/saludable", "/supermercado/marcas-propias")
