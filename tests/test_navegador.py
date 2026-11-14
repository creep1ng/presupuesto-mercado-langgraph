"""Reintentos y descargas compartidas del navegador, sin abrir Chromium."""

import asyncio

from playwright.async_api import Error as ErrorPlaywright
from playwright.async_api import TimeoutError as TiempoAgotado

from presupuesto_mercado.navegador import Navegador, es_falla_transitoria


def test_fallas_transitorias():
    assert es_falla_transitoria(ErrorPlaywright("net::ERR_NETWORK_CHANGED at https://www.d1.com.co/x"))
    assert es_falla_transitoria(TiempoAgotado("Timeout 45000ms exceeded."))
    assert not es_falla_transitoria(ErrorPlaywright("Target page, context or browser has been closed"))
    assert not es_falla_transitoria(ValueError("net::ERR_X"))


async def test_misma_pagina_en_curso_se_descarga_una_vez(tmp_path):
    nav = Navegador(tmp_path)
    descargas = 0

    async def descargar():
        nonlocal descargas
        descargas += 1
        await asyncio.sleep(0.05)
        return "contenido"

    resultados = await asyncio.gather(*(nav._una_vez("clave", descargar) for _ in range(3)))
    assert resultados == ["contenido"] * 3 and descargas == 1
    assert await nav._una_vez("clave", descargar) == "contenido" and descargas == 2  # ya terminó: se repite
    await nav.cerrar()
