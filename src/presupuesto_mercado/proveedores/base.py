"""Clase base de proveedores web que se consultan por páginas de categoría.

Los buscadores internos de los supermercados (/s?q=, /busca, ?map=ft) están
prohibidos en sus robots.txt. Por eso cada proveedor ubica la categoría del
producto a partir de su sitemap público y recorre las páginas del listado, que sí
están permitidas.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin

from ..modelos import FuenteConsultada, ItemSolicitado, Oferta, ResultadoConsulta, TipoFuente
from ..navegador import BloqueoDetectado, ConsultaNoPermitida, Navegador
from ..texto import contiene_termino, raices


class Proveedor(ABC):
    id: str
    nombre: str
    sitio: str
    mecanismo: str  # descripción para el informe

    @abstractmethod
    async def consultar(self, item: ItemSolicitado, nav: Navegador) -> ResultadoConsulta: ...


# Pistas de categoría conocidas para productos de mercado frecuentes. Se suman a
# las que propone el LLM al interpretar la solicitud.
PISTAS_CATEGORIA: dict[str, list[str]] = {
    "arroz": ["arroces", "granos"],
    "frijol": ["granos"],
    "lenteja": ["granos"],
    "azucar": ["endulzantes", "endulzante"],
    "panela": ["endulzantes", "azucar"],
    "aceite": ["aceites"],
    "leche": ["lacteos", "leches"],
    "huevo": ["huevos"],
    "cafe": ["cafes", "bebidas calientes"],
    "sal": ["condimentos"],
    "pasta": ["pastas"],
    "atun": ["enlatados", "conservas"],
    "detergente": ["detergentes", "cuidado ropa"],
    "jabon": ["detergentes", "jaboneria"],
    "papel higienico": ["papeles desechables"],
    "lavaloza": ["lavalozas", "limpieza cocina"],
    "suavizante": ["suavizantes"],
    "blanqueador": ["blanqueadores", "limpiadores"],
}


def _clave(termino: str) -> str:
    return " ".join(sorted(raices(termino)))


_PISTAS_POR_CLAVE = {_clave(k): v for k, v in PISTAS_CATEGORIA.items()}


# Departamentos demasiado amplios: como pista llevarían a recorrer toda la sección
_PISTAS_GENERICAS = {"despensa", "mercado", "supermercado", "alimento", "abarrote", "vivere", "hogar", "aseo",
                     "aseo hogar", "comida"}


def pistas_item(item: ItemSolicitado) -> list[str]:
    pistas = [p for p in item.pistas_categoria if _clave(p) not in _PISTAS_GENERICAS]
    for termino in terminos_item(item):
        pistas += _PISTAS_POR_CLAVE.get(_clave(termino), [])
    return pistas


def terminos_item(item: ItemSolicitado) -> list[str]:
    terminos = [t for t in item.terminos_busqueda if t.strip()]
    if not terminos:
        terminos = [item.producto.split()[0]]
    return terminos


def es_candidato(nombre: str, item: ItemSolicitado) -> bool:
    """Filtro léxico barato antes de la clasificación con LLM."""
    return any(contiene_termino(nombre, t) for t in terminos_item(item))


def puntuar_categoria(url: str, terminos: list[str], pistas: list[str] = ()) -> int:
    """Puntaje de una URL de categoría: el término del producto pesa más que una pista."""
    ruta = re.sub(r"^https?://[^/]+", "", url).strip("/")
    segmentos = [s for s in ruta.split("/") if s]
    if not segmentos:
        return 0
    ultimo = raices(segmentos[-1].replace("-", " "))
    anteriores = raices(" ".join(segmentos[:-1]).replace("-", " "))
    def puntos(termino: str, peso: int) -> int:
        r = raices(termino)
        if not r:
            return 0
        if r <= ultimo:
            return peso + (peso - 1 if r == ultimo else 0)  # exacto: la categoría es el producto
        return 1 if r <= anteriores else 0

    claves_terminos = {_clave(t) for t in terminos}
    puntaje = sum(puntos(t, 3) for t in terminos)
    # de las pistas cuenta solo la mejor, para que varias variantes no inflen el puntaje
    puntaje += max((puntos(p, 2) for p in pistas if _clave(p) not in claves_terminos), default=0)
    return puntaje


class ProveedorPorCategorias(Proveedor):
    sitemaps_categorias: list[str]
    prefijos_permitidos: tuple[str, ...] = ()  # rutas de mercado; evita "aceites" de vehículos
    prefijos_excluidos: tuple[str, ...] = ()
    fuente_tipo: TipoFuente = "pagina_web_renderizada"
    paginas_max: int = 3
    categorias_max: int = 2
    candidatos_suficientes: int = 14

    # --------------------------------------------------------- categorías

    async def categorias(self, nav: Navegador) -> list[str]:
        urls: list[str] = []
        for sitemap in self.sitemaps_categorias:
            xml, _ = await nav.obtener_texto(sitemap, ttl=timedelta(days=7))
            urls += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
        urls = [u for u in urls if not u.endswith("/p")]
        rutas = [re.sub(r"^https?://[^/]+", "", u) for u in urls]
        if self.prefijos_permitidos:
            urls = [u for u, r in zip(urls, rutas) if r.startswith(self.prefijos_permitidos)]
        if self.prefijos_excluidos:
            urls = [u for u in urls if not re.sub(r"^https?://[^/]+", "", u).startswith(self.prefijos_excluidos)]
        return sorted(set(urls))

    def elegir_categorias(self, item: ItemSolicitado, urls: list[str]) -> list[str]:
        terminos, pistas = terminos_item(item), pistas_item(item)
        puntuadas = sorted(
            ((puntuar_categoria(u, terminos, pistas), -len(raices(u.rsplit("/", 1)[-1].replace("-", " "))), u) for u in urls),
            reverse=True,
        )
        elegidas: list[tuple[int, str]] = []
        for puntaje, _, url in puntuadas:
            if puntaje < 2 or len(elegidas) >= self.categorias_max:
                break
            # padre e hija se solapan: se conserva la hija si es al menos igual de pertinente
            if any(e.startswith(url + "/") for _, e in elegidas):
                continue
            padres = [(p, e) for p, e in elegidas if url.startswith(e + "/")]
            if any(p > puntaje for p, _ in padres):
                continue
            elegidas = [x for x in elegidas if x not in padres] + [(puntaje, url)]
        return [url for _, url in elegidas]

    # ------------------------------------------------------------ listado

    def url_pagina(self, categoria: str, pagina: int) -> str:
        return categoria if pagina == 1 else f"{categoria}?page={pagina}"

    @abstractmethod
    async def leer_listado(self, url: str, nav: Navegador) -> tuple[list[dict[str, Any]], datetime]:
        """Devuelve productos crudos (diccionarios) de una página de listado."""

    @abstractmethod
    def a_oferta(self, crudo: dict[str, Any], item: ItemSolicitado, url_listado: str, fecha: datetime) -> Oferta | None: ...

    def absoluta(self, href: str) -> str:
        return urljoin(self.sitio, href)

    async def consultar(self, item: ItemSolicitado, nav: Navegador) -> ResultadoConsulta:
        resultado = ResultadoConsulta(proveedor=self.id, item_id=item.id)
        try:
            categorias = self.elegir_categorias(item, await self.categorias(nav))
        except (ConsultaNoPermitida, BloqueoDetectado) as e:
            resultado.errores.append(str(e))
            return resultado
        except Exception as e:  # red caída, sitemap cambiado…
            resultado.errores.append(f"No se pudo leer el sitemap de categorías: {e}")
            return resultado
        if not categorias:
            resultado.errores.append(
                f"No se encontró una categoría pública para '{', '.join(terminos_item(item))}' en el sitemap"
            )
            return resultado

        vistos: set[str] = set()
        for categoria in categorias:
            for n in range(1, self.paginas_max + 1):
                url = self.url_pagina(categoria, n)
                try:
                    crudos, fecha = await self.leer_listado(url, nav)
                except (ConsultaNoPermitida, BloqueoDetectado) as e:
                    resultado.errores.append(str(e))
                    break
                except Exception as e:
                    resultado.errores.append(f"Error leyendo {url}: {type(e).__name__}: {e}")
                    break
                resultado.fuentes.append(FuenteConsultada(url=url, fecha_consulta=fecha, productos_leidos=len(crudos)))
                nuevos = 0
                for crudo in crudos:
                    oferta = self.a_oferta(crudo, item, url, fecha)
                    if oferta is None or oferta.clave in vistos or not es_candidato(oferta.nombre, item):
                        continue
                    vistos.add(oferta.clave)
                    resultado.ofertas.append(oferta)
                    nuevos += 1
                if not crudos or len(resultado.ofertas) >= self.candidatos_suficientes:
                    break
                if n >= 2 and nuevos == 0:
                    break  # el listado ya no trae productos de este tipo
            if len(resultado.ofertas) >= self.candidatos_suficientes:
                break
        return resultado


def precio_cop(texto: str | None) -> float | None:
    """'$ 16.300' -> 16300.0 (los precios en COP se publican sin decimales)."""
    if not texto:
        return None
    m = re.search(r"\$\s*([\d.]+(?:,\d{1,2})?)", texto.replace("\xa0", " "))
    if not m:
        return None
    entero = m.group(1).split(",")[0].replace(".", "")
    return float(entero) if entero else None
