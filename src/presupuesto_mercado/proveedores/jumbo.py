"""Jumbo (Cencosud): listado de categoría renderizado con Playwright.

El JSON-LD de las páginas de Jumbo no corresponde a la grilla de productos
(lista artículos de otra vitrina), así que se leen las tarjetas del DOM de la
ruta /co/…, que es la que pinta la grilla completa. El atributo
`data-cnstrc-item-price` no coincide con ningún precio visible y se ignora. Las
tarjetas pueden mostrar "Paga $X" acompañado del logo de la tarjeta CMR
Falabella: ese es un precio condicionado al medio de pago, no el precio general.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..modelos import ItemSolicitado, Oferta
from ..navegador import Navegador
from ..unidades import extraer_presentacion
from .base import ProveedorPorCategorias, precio_cop

EXTRACTOR_JS = r"""() => [...document.querySelectorAll('div[data-product-id][data-cnstrc-item-name]')].map(d => ({
  nombre: d.getAttribute('data-cnstrc-item-name'),
  id: d.getAttribute('data-product-id'),
  href: d.querySelector('a')?.getAttribute('href'),
  marca: d.querySelector('h3')?.previousElementSibling?.innerText?.trim() || null,
  lineas: d.innerText.split('\n').map(s => s.replace(/\s+/g, ' ').trim()).filter(Boolean),
  cmr: !!d.querySelector('[id*="crmFalabella"], [id*="cmr" i], [class*="cmr" i]'),
  botones: [...d.querySelectorAll('button')].map(b => b.innerText.trim()),
}))"""


class Jumbo(ProveedorPorCategorias):
    id = "jumbo"
    nombre = "Jumbo"
    sitio = "https://www.jumbocolombia.com"
    sitemaps_categorias = ["https://www.jumbocolombia.com/sitemap/category-0.xml"]
    prefijos_permitidos = ("/supermercado/",)
    paginas_max = 2
    mecanismo = (
        "Página de categoría pública (descubierta en el sitemap) renderizada con Playwright y desplazada para "
        "cargar la grilla; se leen las tarjetas de producto del DOM."
    )

    def url_pagina(self, categoria: str, pagina: int) -> str:
        # El sitemap publica /supermercado/...; la grilla de búsqueda completa vive en /co/supermercado/...
        url = re.sub(r"^(https://[^/]+)/(?!co/)", r"\1/co/", categoria)
        return super().url_pagina(url, pagina)

    async def leer_listado(self, url: str, nav: Navegador) -> tuple[list[dict[str, Any]], datetime]:
        return await nav.renderizar(
            url, EXTRACTOR_JS, esperar_selector="div[data-product-id][data-cnstrc-item-name]", desplazamientos=6,
            nombre_extractor="jumbo-v2",
        )

    def a_oferta(self, crudo: dict[str, Any], item: ItemSolicitado, url_listado: str, fecha: datetime) -> Oferta | None:
        nombre = (crudo.get("nombre") or "").strip()
        if not nombre or not crudo.get("href"):
            return None
        lineas: list[str] = crudo.get("lineas") or []
        pagas = [precio_cop(l) for l in lineas if l.lower().startswith("paga") and "$" in l]
        otros = [precio_cop(l) for l in lineas if l.startswith("$")]
        pagas = [p for p in pagas if p]
        otros = [p for p in otros if p]
        condicionado = None
        if crudo.get("cmr") and pagas:
            condicionado = pagas[0]
            precio = otros[0] if otros else None
        else:
            precio = pagas[0] if pagas else (otros[0] if otros else None)
        botones = " ".join(crudo.get("botones") or []).lower()
        disponibilidad = "disponible" if "agregar" in botones else ("agotado" if "agotado" in botones else "desconocida")
        presentacion = extraer_presentacion(nombre, permitir_conteo_suelto=True)
        notas = []
        if precio is None and condicionado is not None:
            notas.append("Solo se publicó el precio con tarjeta CMR; no hay precio general en el listado")
        return Oferta(
            proveedor=self.id,
            item_id=item.id,
            nombre=nombre,
            marca=crudo.get("marca"),
            presentacion_texto=presentacion.texto if presentacion else None,
            contenido=presentacion.contenido if presentacion else None,
            unidad_base=presentacion.unidad_base if presentacion else None,
            precio=precio,
            precio_condicionado=condicionado,
            condicion_precio="Precio pagando con tarjeta CMR Falabella" if condicionado else None,
            url=self.absoluta(crudo["href"]),
            sku=crudo.get("id"),
            fuente_tipo=self.fuente_tipo,
            url_listado=url_listado,
            fecha_consulta=fecha,
            disponibilidad=disponibilidad,
            vendedor=next((l.replace("Vendido por", "").strip() for l in lineas if l.startswith("Vendido por")), None),
            notas=notas,
        )
