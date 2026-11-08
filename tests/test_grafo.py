"""Ejecución completa del grafo sin red ni LLM, con proveedores simulados y un catálogo CSV."""

from pathlib import Path

from langgraph.types import Command

from presupuesto_mercado import grafo as modulo_grafo

CATALOGO = Path(__file__).parent / "datos" / "catalogo_ejemplo.csv"


async def test_flujo_completo_sin_llm(falsos):
    g = modulo_grafo.construir_grafo(modulo_grafo.crear_checkpointer())
    config = {"configurable": {"thread_id": "t1", "sin_llm": True, "navegador": object()}}
    final = await g.ainvoke({"texto": "Necesito 5 kg de arroz y 2 litros de aceite en Medellín",
                             "opciones": {"catalogos": {"ara": str(CATALOGO)}}}, config)
    assert final["proveedores"] == ["ara", "exito", "d1"]
    nombres = [p.nombre for p in final["presupuestos"]]
    assert "Compra completa en EXITO" in nombres
    assert all(p.total_confirmado is None for p in final["presupuestos"])
    assert "Total final confirmado:** no disponible" in final["informe"]
    assert "Catálogo de referencia" in final["informe"] or "catálogo de referencia" in final["informe"]
    ara = next(r for r in final["resultados"] if r.proveedor == "ara" and r.item_id == "arroz")
    assert ara.ofertas and ara.ofertas[0].fuente_tipo == "catalogo_referencia"


async def test_interrupcion_para_aclarar(falsos):
    g = modulo_grafo.construir_grafo(modulo_grafo.crear_checkpointer())
    config = {"configurable": {"thread_id": "t2", "sin_llm": True, "navegador": object()}}
    await g.ainvoke({"texto": "Necesito 5 kg de arroz", "opciones": {"interactivo": True}}, config)
    estado = g.get_state(config)
    assert estado.interrupts and any("ciudad" in p for p in estado.interrupts[0].value["preguntas"])
    final = await g.ainvoke(Command(resume="En Cali"), config)
    assert final["solicitud"].ciudad == "Cali"
    assert final["informe"]
