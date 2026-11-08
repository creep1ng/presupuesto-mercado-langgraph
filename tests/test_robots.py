from presupuesto_mercado.robots import interpretar_robots

EXITO = """
User-agent: *
Allow: /
Disallow: /account/
Disallow: /s?
Disallow: /*?*facets=
Disallow: /*?*sort=
Allow: /*?page=
Disallow: /api/
"""

OLIMPICA = """
User-agent: *
Disallow: /secret
User-agent: *
Disallow: /*&

User-agent: Googlebot
Disallow: /busca/*
"""


def test_buscador_y_api_prohibidos():
    r = interpretar_robots(EXITO)
    assert not r.permite("/s?q=arroz")
    assert not r.permite("/api/catalog_system/pub/products/search")
    assert not r.permite("/mercado/despensa?facets=marca")


def test_categorias_y_paginacion_permitidas():
    r = interpretar_robots(EXITO)
    assert r.permite("/mercado/despensa/granos-y-arroz")
    assert r.permite("/mercado/despensa/granos-y-arroz?page=2")


def test_grupos_repetidos_y_comodines():
    r = interpretar_robots(OLIMPICA)
    assert not r.permite("/aceites?a=1&b=2")
    assert r.permite("/supermercado/despensa/aceites?page=2")
    assert r.permite("/busca/arroz")  # la regla es solo para Googlebot
