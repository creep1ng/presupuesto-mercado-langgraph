"""Conversión de datos crudos (tal como los extrae Playwright) a ofertas."""

from conftest import FECHA

from presupuesto_mercado.modelos import ItemSolicitado
from presupuesto_mercado.proveedores.base import precio_cop, puntuar_categoria
from presupuesto_mercado.proveedores.grupo_exito import Exito
from presupuesto_mercado.proveedores.jumbo import Jumbo
from presupuesto_mercado.proveedores.vtex_jsonld import D1, productos_jsonld

ITEM = ItemSolicitado(id="aceite", producto="aceite", cantidad=2, unidad="l", terminos_busqueda=["aceite"])


def test_precio_cop():
    assert precio_cop("$ 16.300") == 16300
    assert precio_cop("Paga $\xa049.650") == 49650
    assert precio_cop("sin precio") is None


def test_exito_distingue_precio_general_de_lista_y_aliado():
    crudo = {
        "nombre": "Aceite de girasol PREMIER multiusos (1800 ml)",
        "href": "/aceite-comestible-girasol-premier-1800-ml-3139678/p",
        "precio_general": "$ 29.288", "precio_lista": "$ 41.990", "precio_aliado": "$ 27.294",
        "vendedor": "Vendido por: Exito", "botones": ["Agregar"], "texto": "",
    }
    o = Exito().a_oferta(crudo, ITEM, "https://www.exito.com/mercado/despensa/aceites-y-vinagres", FECHA)
    assert o.precio == 29288 and o.precio_regular == 41990 and o.precio_condicionado == 27294
    assert o.contenido == 1800 and o.unidad_base == "ml"
    assert o.url == "https://www.exito.com/aceite-comestible-girasol-premier-1800-ml-3139678/p"
    assert o.sku == "3139678" and o.disponibilidad == "disponible"


def test_jumbo_precio_cmr_es_condicionado():
    crudo = {
        "nombre": "Aceite Premier girasol x2700ml", "id": "288561", "href": "/aceite-premier-girasol-x2700ml/p",
        "marca": "Premier", "lineas": ["Premier", "Aceite Premier girasol x2700ml", "Paga $ 40.650", "-5%",
                                        "$ 54.200", "20.07xml", "Vendido por Cencosud - Colombia", "Agregar"],
        "cmr": True, "botones": ["Agregar"],
    }
    o = Jumbo().a_oferta(crudo, ITEM, "u", FECHA)
    assert o.precio == 54200
    assert o.precio_condicionado == 40650 and "CMR" in o.condicion_precio
    assert o.contenido == 2700 and o.vendedor == "Cencosud - Colombia"


def test_jumbo_url_de_listado_usa_ruta_co():
    assert Jumbo().url_pagina("https://www.jumbocolombia.com/supermercado/despensa/aceite", 2) == \
        "https://www.jumbocolombia.com/co/supermercado/despensa/aceite?page=2"


def test_jsonld_itemlist_d1():
    bloque = """{"@context":"https://schema.org","@type":"ItemList","itemListElement":[{"@type":"ListItem","position":1,
    "item":{"@type":"Product","@id":"https://www.d1.com.co/aceite-vegetal-imata-12004359/p","name":"Aceite Vegetal Imatá 3.000 Ml",
    "brand":{"@type":"Brand","name":"IMATA"},"offers":{"@type":"AggregateOffer","lowPrice":19950,"priceCurrency":"COP",
    "offers":[{"@type":"Offer","price":19950,"priceCurrency":"COP","availability":"http://schema.org/InStock","sku":"709",
    "seller":{"@type":"Organization","name":"Tiendas D1"}}]}}}]}"""
    productos = productos_jsonld(['{"@type":"BreadcrumbList"}', bloque, "no es json"])
    assert len(productos) == 1
    o = D1().a_oferta(productos[0], ITEM, "u", FECHA)
    assert (o.precio, o.moneda, o.disponibilidad, o.marca, o.sku) == (19950, "COP", "disponible", "IMATA", "709")
    assert o.contenido == 3000 and o.fuente_tipo == "datos_estructurados_jsonld"


def test_categoria_exacta_puntua_mas_que_parcial():
    exacta = puntuar_categoria("https://www.carulla.com/despensa/granos/arroz", ["arroz"])
    parcial = puntuar_categoria("https://www.carulla.com/despensa/harinas/harina-de-almendras-arroz-y-avena", ["arroz"])
    ninguna = puntuar_categoria("https://www.carulla.com/despensa/pastas", ["arroz"])
    assert exacta > parcial > ninguna == 0


def test_pista_generica_no_desplaza_a_la_subcategoria():
    urls = ["https://www.d1.com.co/despensa", "https://www.d1.com.co/despensa/granos-azucar-y-panela",
            "https://www.d1.com.co/despensa/margarinas-y-aceites"]
    item = ItemSolicitado(id="arroz", producto="arroz", terminos_busqueda=["arroz"], pistas_categoria=["granos", "despensa"])
    assert D1().elegir_categorias(item, urls) == ["https://www.d1.com.co/despensa/granos-azucar-y-panela"]
