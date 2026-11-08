from datetime import datetime, timezone

import pytest

from presupuesto_mercado.modelos import ItemSolicitado, Oferta
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

