"""Éxito y Carulla (Grupo Éxito): listados de categoría renderizados con Playwright.

Las tarjetas de producto se pintan en el navegador; no traen JSON-LD de producto.
En cada tarjeta se distinguen tres precios:
- precio de lista tachado (`priceSection_container-promotion_price`),
- precio de venta general (`ProductPrice_container__price`, etiquetado "Otros"),
- precio con medio de pago aliado (`allieds-display_bestDiscount`), que es condicionado.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..modelos import ItemSolicitado, Oferta
from ..navegador import Navegador
from ..unidades import extraer_presentacion
from .base import ProveedorPorCategorias, precio_cop

EXTRACTOR_JS = r"""() => [...document.querySelectorAll('ul[data-fs-product-grid] article')].map(a => {
  const q = s => a.querySelector(s);
  const txt = e => e ? e.innerText.replace(/\s+/g, ' ').trim() : null;
  const boton = [...a.querySelectorAll('button')].map(b => b.innerText.trim()).filter(Boolean);
  return {
    nombre: txt(q('h3')),
    href: q('a[data-testid=product-link]')?.getAttribute('href'),
    precio_general: txt(q('[data-fs-container-price-otros]')),
    precio_lista: txt(q('[class*="promotion_price"]')),
    precio_aliado: txt(q('[class*="bestDiscount"] [class*="fs-price"]')),
    precio_unidad: txt(q('[data-fs-unit-price]')),
    vendedor: txt(q('[data-fs-product-name-container]')),
    botones: boton,
    texto: txt(a),
  };
})"""


class _GrupoExito(ProveedorPorCategorias):
    mecanismo = (
        "Página de categoría pública (descubierta en el sitemap) renderizada con Playwright; "
        "se leen las tarjetas de producto del DOM. El buscador /s? y /api/ están prohibidos en robots.txt."
    )

    async def leer_listado(self, url: str, nav: Navegador) -> tuple[list[dict[str, Any]], datetime]:
        return await nav.renderizar(
            url, EXTRACTOR_JS, esperar_selector="ul[data-fs-product-grid] article", nombre_extractor="faststore-v1"
        )

    def a_oferta(self, crudo: dict[str, Any], item: ItemSolicitado, url_listado: str, fecha: datetime) -> Oferta | None:
        if not crudo.get("nombre") or not crudo.get("href"):
            return None
        presentacion = extraer_presentacion(crudo["nombre"], permitir_conteo_suelto=True)
        precio = precio_cop(crudo.get("precio_general"))
        lista = precio_cop(crudo.get("precio_lista"))
        aliado = precio_cop(crudo.get("precio_aliado"))
        botones = " ".join(crudo.get("botones") or []).lower()
        if "agregar" in botones:
            disponibilidad = "disponible"
        elif "agotado" in botones or "no disponible" in (crudo.get("texto") or "").lower():
            disponibilidad = "agotado"
        else:
            disponibilidad = "desconocida"
        vendedor = (crudo.get("vendedor") or "").replace("Vendido por:", "").strip() or None
        notas = []
        if vendedor and vendedor.lower() not in (self.nombre.lower(), "exito", "carulla"):
            notas.append(f"Vendido por un tercero del marketplace ({vendedor})")
        return Oferta(
            proveedor=self.id,
            item_id=item.id,
            nombre=crudo["nombre"],
            presentacion_texto=presentacion.texto if presentacion else None,
            contenido=presentacion.contenido if presentacion else None,
            unidad_base=presentacion.unidad_base if presentacion else None,
            precio=precio,
            precio_regular=lista if lista and precio and lista > precio else None,
            precio_condicionado=aliado if aliado and precio and aliado < precio else None,
            condicion_precio="Precio con medio de pago aliado (p. ej. tarjeta de la marca)" if aliado else None,
            url=self.absoluta(crudo["href"]),
            sku=crudo["href"].removesuffix("/p").rsplit("-", 1)[-1],
            fuente_tipo=self.fuente_tipo,
            url_listado=url_listado,
            fecha_consulta=fecha,
            disponibilidad=disponibilidad,
            vendedor=vendedor,
            notas=notas,
        )


class Exito(_GrupoExito):
    id = "exito"
    nombre = "Éxito"
    sitio = "https://www.exito.com"
    sitemaps_categorias = [
        "https://www.exito.com/sitemap/subcategory-0.xml",
        "https://www.exito.com/sitemap/category-0.xml",
    ]
    prefijos_permitidos = ("/mercado/",)
    paginas_max = 4


class Carulla(_GrupoExito):
    id = "carulla"
    nombre = "Carulla"
    sitio = "https://www.carulla.com"
    sitemaps_categorias = [
        "https://www.carulla.com/sitemap/category-0.xml",
        "https://www.carulla.com/sitemap/category-1.xml",
    ]
    prefijos_permitidos = (
        "/despensa", "/lacteos-huevos-y-refrigerados", "/aseo-del-hogar", "/limpieza-del-hogar",
        "/bebidas-snacks-y-dulces", "/frutas-y-verduras", "/pollo-carnes-y-pescado", "/panaderia", "/congelados",
        "/mascotas", "/delicatessen", "/comidas-preparadas", "/salud-y-belleza", "/bebes",
    )
    paginas_max = 3
