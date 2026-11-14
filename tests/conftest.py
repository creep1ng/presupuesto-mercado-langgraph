from datetime import datetime, timezone

import pytest

from presupuesto_mercado import grafo as modulo_grafo
from presupuesto_mercado.modelos import FuenteConsultada, ItemSolicitado, Oferta, ResultadoConsulta
from presupuesto_mercado.proveedores.base import Proveedor, es_candidato
from presupuesto_mercado.unidades import extraer_presentacion

FECHA = datetime(2026, 11, 4, 16, 0, tzinfo=timezone.utc)


def oferta(proveedor: str, nombre: str, precio: float | None, item_id: str = "arroz", **extra) -> Oferta:
    p = extraer_presentacion(nombre, permitir_conteo_suelto=True)
    return Oferta(
        proveedor=proveedor, item_id=item_id, nombre=nombre, precio=precio,
        contenido=p.contenido if p else None, unidad_base=p.unidad_base if p else None,
        presentacion_texto=p.texto if p else None, url=f"https://{proveedor}.test/{abs(hash(nombre))}/p",
        fuente_tipo="pagina_web_renderizada", fecha_consulta=FECHA, disponibilidad=extra.pop("disponibilidad", "disponible"),
        **extra,
    )


@pytest.fixture
def arroz() -> ItemSolicitado:
    return ItemSolicitado(id="arroz", producto="arroz blanco", cantidad=5, unidad="kg", terminos_busqueda=["arroz"])


class Falso(Proveedor):
    mecanismo = "simulado"

    def __init__(self, id, precios):
        self.id, self.nombre, self.sitio, self.precios = id, id.upper(), f"https://{id}.test", precios

    async def consultar(self, item, nav):
        ofertas = [oferta(self.id, n, p, item.id) for n, p in self.precios if es_candidato(n, item)]
        return ResultadoConsulta(proveedor=self.id, item_id=item.id, ofertas=ofertas,
                                 fuentes=[FuenteConsultada(url=f"{self.sitio}/cat", fecha_consulta=FECHA)])


class Caido(Proveedor):
    """Proveedor cuyo sitio rechaza la consulta (p. ej. 403)."""
    mecanismo = "simulado"

    def __init__(self, id):
        self.id, self.nombre, self.sitio = id, id.upper(), f"https://{id}.test"

    async def consultar(self, item, nav):
        return ResultadoConsulta(proveedor=self.id, item_id=item.id, errores=[f"{self.sitio}/sitemap.xml respondió 403"])


@pytest.fixture
def falsos(monkeypatch):
    tiendas = {
        "exito": [("Arroz Blanco X (5000 gr)", 16300), ("Aceite Vegetal X (2000 ml)", 14490)],
        "d1": [("Arroz Blanco Y 1000 Grs", 3000), ("Aceite Vegetal Y 900 Ml", 6950)],
    }
    clases = {k: (lambda k=k: Falso(k, tiendas[k])) for k in tiendas}
    monkeypatch.setattr(modulo_grafo, "PROVEEDORES_WEB", clases)
    monkeypatch.setattr(modulo_grafo, "POR_DEFECTO", list(tiendas))


@pytest.fixture
def tres_tiendas(monkeypatch):
    """Éxito caído, D1 con un solo producto y Jumbo con la lista completa."""
    tiendas = {
        "d1": [("Arroz Blanco Y 1000 Grs", 3000)],
        "jumbo": [("Arroz Blanco Z 1000 Grs", 3500), ("Aceite Vegetal Z 1000 Ml", 7000)],
    }
    clases = {"exito": lambda: Caido("exito"), **{k: (lambda k=k: Falso(k, tiendas[k])) for k in tiendas}}
    monkeypatch.setattr(modulo_grafo, "PROVEEDORES_WEB", clases)
    monkeypatch.setattr(modulo_grafo, "POR_DEFECTO", ["exito", "d1", "jumbo"])
