"""Proveedor a partir de un catálogo importado (CSV, JSON o Excel).

Sirve para comercios que no publican precios en línea, como Tiendas Ara. Los datos se
marcan como "catalogo_referencia": no se consultaron en línea en el momento del
presupuesto, y el informe lo advierte.

Columnas esperadas (las tres primeras son obligatorias):
    nombre, precio, fuente      (fuente: URL, foto de góndola, folleto, etc.)
    marca, presentacion, moneda, fecha_consulta, disponibilidad, sku
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..modelos import FuenteConsultada, ItemSolicitado, Oferta, ResultadoConsulta
from ..navegador import Navegador
from ..unidades import extraer_presentacion
from .base import Proveedor, es_candidato


def _precio(valor: str) -> float | None:
    """Acepta '16300', '16300.5' y el formato colombiano '16.300' o '$ 16.300,50'."""
    t = str(valor).replace("$", "").replace(" ", "").strip()
    if re.fullmatch(r"\d+(\.\d{1,2})?", t):
        return float(t)
    try:
        return float(t.replace(".", "").replace(",", "."))
    except ValueError:
        return None


class ProveedorCatalogo(Proveedor):
    mecanismo = "Catálogo importado desde archivo (datos de referencia, no consultados en línea)"

    def __init__(self, id: str, nombre: str, ruta: Path) -> None:
        self.id = id
        self.nombre = nombre
        self.ruta = ruta
        self.sitio = ruta.as_posix()

    def _leer(self) -> pd.DataFrame:
        sufijo = self.ruta.suffix.lower()
        if sufijo == ".csv":
            df = pd.read_csv(self.ruta, dtype=str)
        elif sufijo == ".json":
            df = pd.read_json(self.ruta, dtype=str)
        elif sufijo in (".xlsx", ".xls"):
            df = pd.read_excel(self.ruta, dtype=str)
        else:
            raise ValueError(f"Formato de catálogo no soportado: {sufijo}")
        df.columns = [c.strip().lower() for c in df.columns]
        faltantes = {"nombre", "precio", "fuente"} - set(df.columns)
        if faltantes:
            raise ValueError(f"Al catálogo {self.ruta.name} le faltan columnas: {', '.join(sorted(faltantes))}")
        return df.fillna("")

    async def consultar(self, item: ItemSolicitado, nav: Navegador) -> ResultadoConsulta:
        resultado = ResultadoConsulta(proveedor=self.id, item_id=item.id)
        try:
            df = self._leer()
        except Exception as e:
            resultado.errores.append(f"No se pudo leer el catálogo: {e}")
            return resultado
        modificado = datetime.fromtimestamp(self.ruta.stat().st_mtime).astimezone()
        resultado.fuentes.append(FuenteConsultada(url=self.ruta.as_posix(), fecha_consulta=modificado, productos_leidos=len(df)))
        for fila in df.to_dict("records"):
            nombre = f"{fila['nombre']} {fila.get('presentacion', '')}".strip()
            if not es_candidato(nombre, item):
                continue
            presentacion = extraer_presentacion(nombre, permitir_conteo_suelto=True)
            precio = _precio(fila["precio"])
            fecha = fila.get("fecha_consulta")
            disponibilidad = (fila.get("disponibilidad") or "desconocida").lower()
            resultado.ofertas.append(
                Oferta(
                    proveedor=self.id,
                    item_id=item.id,
                    nombre=nombre,
                    marca=fila.get("marca") or None,
                    presentacion_texto=presentacion.texto if presentacion else None,
                    contenido=presentacion.contenido if presentacion else None,
                    unidad_base=presentacion.unidad_base if presentacion else None,
                    precio=precio,
                    moneda=fila.get("moneda") or "COP",
                    url=fila["fuente"],
                    sku=fila.get("sku") or None,
                    fuente_tipo="catalogo_referencia",
                    url_listado=self.ruta.as_posix(),
                    fecha_consulta=datetime.fromisoformat(fecha).astimezone() if fecha else modificado,
                    disponibilidad=disponibilidad if disponibilidad in ("disponible", "agotado") else "desconocida",
                    notas=["Dato de referencia importado de un catálogo; no se verificó en línea"],
                )
            )
        return resultado
