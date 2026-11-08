from conftest import oferta

from presupuesto_mercado.emparejamiento import clasificar_con_reglas, normalizar_y_validar
from presupuesto_mercado.modelos import CostoAdicional, ItemSolicitado, OfertaEvaluada, Solicitud
from presupuesto_mercado.presupuesto import construir_presupuestos, elegir_recomendacion, mejor_linea


def ev(o, tipo="equivalente"):
    return OfertaEvaluada(oferta=o, tipo=tipo, motivo="")


def test_elige_combinacion_mas_barata_que_cubre(arroz):
    opciones = [ev(oferta("a", "Arroz X (1000 gr)", 4000)), ev(oferta("a", "Arroz X (5000 gr)", 16300)),
                ev(oferta("a", "Arroz X (3000 gr)", 12900))]
    linea = mejor_linea(arroz, "a", opciones)
    assert linea.costo == 16300 and linea.cantidad_cubierta == 5000


def test_combina_presentaciones_del_mismo_producto(arroz):
    opciones = [ev(oferta("a", "Arroz X (3000 gr)", 9000)), ev(oferta("a", "Arroz X (2000 gr)", 6000)),
                ev(oferta("a", "Arroz X (5000 gr)", 20000))]
    linea = mejor_linea(arroz, "a", opciones)
    assert linea.costo == 15000
    assert sorted(n for _, n in linea.empaques) == [1, 1]


def test_no_usa_agotados(arroz):
    opciones = [ev(oferta("a", "Arroz X (5000 gr)", 10000, disponibilidad="agotado")),
                ev(oferta("a", "Arroz Y (5000 gr)", 20000))]
    assert mejor_linea(arroz, "a", opciones).costo == 20000


def test_sin_unidad_es_no_comparable(arroz):
    e = normalizar_y_validar(arroz, oferta("a", "Arroz especial Natri 210", 5000), "equivalente", "")
    assert e.tipo == "no_comparable"


def test_unidad_incompatible_es_no_comparable():
    leche = ItemSolicitado(id="leche", producto="leche", cantidad=6, unidad="litros", terminos_busqueda=["leche"])
    e = normalizar_y_validar(leche, oferta("a", "Leche en Polvo Entera 1000 Grs", 20900, "leche"), "equivalente", "")
    assert e.tipo == "no_comparable" and "incompatible" in e.motivo


def test_marca_obligatoria_no_puede_ser_equivalente():
    item = ItemSolicitado(id="arroz", producto="arroz", cantidad=2, unidad="kg", marca="Diana",
                          marca_obligatoria=True, terminos_busqueda=["arroz"])
    e = normalizar_y_validar(item, oferta("a", "Arroz ROA blanco (1000 gr)", 4000), "equivalente", "LLM")
    assert e.tipo == "sustituto"


def test_reglas_distinguen_variedad_y_producto_distinto(arroz):
    assert clasificar_con_reglas(arroz, oferta("a", "Arroz DIANA blanco (1000 gr)", 1))[0] == "equivalente"
    assert clasificar_con_reglas(arroz, oferta("a", "Arroz ROA integral (1000 gr)", 1))[0] == "sustituto"
    assert clasificar_con_reglas(arroz, oferta("a", "Harina de arroz (500 gr)", 1))[0] == "no_relacionado"


def _solicitud(costos=()):
    items = [ItemSolicitado(id="arroz", producto="arroz", cantidad=5, unidad="kg", terminos_busqueda=["arroz"]),
             ItemSolicitado(id="aceite", producto="aceite", cantidad=2, unidad="l", terminos_busqueda=["aceite"])]
    return Solicitud(texto_original="t", items=items, ciudad="Medellín", costos_adicionales=list(costos))


EVALUADAS = {
    "arroz": [ev(oferta("a", "Arroz A (5000 gr)", 16000)), ev(oferta("b", "Arroz B (5000 gr)", 15000))],
    "aceite": [ev(oferta("a", "Aceite A (2000 ml)", 14000, "aceite"))],
}


def test_envio_desconocido_no_es_cero():
    por_prov, combinado = construir_presupuestos(_solicitud(), EVALUADAS, ["a", "b"], {"a": "A", "b": "B"})
    a, b = por_prov
    assert a.cobertura_completa and a.subtotal_productos == 30000
    assert a.total_confirmado is None
    assert a.costos_adicionales[0].valor is None and a.costos_adicionales[0].estado == "no_verificado"
    assert not b.cobertura_completa and b.items_sin_cotizacion == ["aceite"]
    assert combinado is not None and combinado.subtotal_productos == 29000
    assert set(combinado.proveedores) == {"a", "b"}


def test_total_confirmado_con_envio_conocido():
    costos = [CostoAdicional(concepto="Domicilio", proveedor="a", valor=5000, estado="confirmado_por_usuario")]
    por_prov, _ = construir_presupuestos(_solicitud(costos), EVALUADAS, ["a", "b"], {"a": "A", "b": "B"})
    assert por_prov[0].total_confirmado == 35000


def test_recomendacion_no_cambia_por_ahorro_pequeno():
    por_prov, combinado = construir_presupuestos(_solicitud(), EVALUADAS, ["a", "b"], {"a": "A", "b": "B"})
    decision = elegir_recomendacion(por_prov, combinado)
    assert decision["opcion"] == "Compra completa en A"  # el combinado ahorra solo $1.000
