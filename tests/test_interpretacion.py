from presupuesto_mercado.interpretacion import aplicar_suposiciones, interpretar_con_reglas, validar


def test_reglas_extraen_items_ciudad_y_numero_de_proveedores():
    s = interpretar_con_reglas(
        "Necesito 5 kg de arroz, 2 litros de aceite y 3 kg de azúcar. Consulta dos supermercados y prepara un "
        "presupuesto para Medellín."
    )
    assert [(i.id, i.cantidad, i.unidad) for i in s.items] == [("arroz", 5, "kg"), ("aceite", 2, "litros"), ("azucar", 3, "kg")]
    assert s.ciudad == "Medellín"
    assert s.numero_proveedores == 2


def test_reglas_detectan_proveedores_nombrados():
    s = interpretar_con_reglas("Cotiza 1 kg de sal en Éxito y en D1")
    assert s.proveedores_solicitados == ["exito", "d1"]


def test_datos_faltantes_generan_preguntas_y_suposiciones():
    s = interpretar_con_reglas("Quiero arroz")
    s.items = s.items or []
    from presupuesto_mercado.modelos import ItemSolicitado

    s.items.append(ItemSolicitado(id="cafe", producto="café", terminos_busqueda=["cafe"]))
    preguntas = validar(s)
    assert any("cantidad" in p for p in preguntas)
    assert any("ciudad" in p for p in preguntas)
    completada = aplicar_suposiciones(s)
    assert completada.items[-1].unidad == "empaque"
    assert any("ciudad" in x for x in completada.suposiciones)
