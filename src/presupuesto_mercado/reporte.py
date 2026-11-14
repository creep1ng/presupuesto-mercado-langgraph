"""Informe en Markdown con tablas, presupuestos, limitaciones y fuentes."""

from __future__ import annotations

from datetime import datetime

from .modelos import OfertaEvaluada, Presupuesto, ResultadoConsulta, Solicitud
from .texto import cop
from .unidades import cantidad_pedida, formatear_cantidad

_ETIQUETA_TIPO = {
    "equivalente": "Equivalente",
    "sustituto": "Sustituto aproximado",
    "no_comparable": "No comparable",
    "no_relacionado": "No relacionado",
}
_ETIQUETA_FUENTE = {
    "pagina_web_renderizada": "Página web (Playwright, DOM)",
    "datos_estructurados_jsonld": "Datos estructurados JSON-LD",
    "catalogo_referencia": "Catálogo de referencia",
}


def _celda(texto: str) -> str:
    return texto.replace("|", "/").replace("\n", " ")


def _tabla(encabezados: list[str], filas: list[list[str]]) -> str:
    lineas = ["| " + " | ".join(encabezados) + " |", "|" + "|".join("---" for _ in encabezados) + "|"]
    lineas += ["| " + " | ".join(_celda(c) for c in fila) + " |" for fila in filas]
    return "\n".join(lineas)


def _presupuesto_md(p: Presupuesto, nombres_items: dict[str, str], nombres_prov: dict[str, str]) -> str:
    partes = [f"### {p.nombre}", ""]
    if p.lineas:
        filas = []
        for l in p.lineas:
            for oferta, n in l.empaques:
                filas.append([
                    nombres_items.get(l.item_id, l.item_id),
                    nombres_prov.get(oferta.proveedor, oferta.proveedor),
                    f"[{oferta.nombre}]({oferta.url})",
                    str(n),
                    cop(oferta.precio),
                    cop(oferta.precio * n if oferta.precio else None),
                    f"{formatear_cantidad(l.cantidad_cubierta, l.unidad_base)} de {formatear_cantidad(l.cantidad_solicitada, l.unidad_base)}",
                    _ETIQUETA_TIPO[l.tipo],
                ])
        partes.append(_tabla(
            ["Producto", "Proveedor", "Artículo", "Unid.", "Precio unitario", "Costo", "Cubre", "Tipo"], filas
        ))
        notas = [n for l in p.lineas for n in l.notas]
        if notas:
            partes += ["", *[f"- {n}" for n in notas]]
    partes += ["", f"- **Subtotal de productos:** {cop(p.subtotal_productos)}"]
    for c in p.costos_adicionales:
        estado = {"confirmado_por_usuario": "confirmado por el usuario", "publicado_por_proveedor": "publicado por el proveedor",
                  "no_verificado": "**no verificado**"}[c.estado]
        partes.append(f"- {c.concepto}: {cop(c.valor) if c.valor is not None else 'desconocido'} ({estado})")
    if p.total_confirmado is not None:
        partes.append(f"- **Total final confirmado:** {cop(p.total_confirmado)}")
    else:
        motivo = "faltan productos por cotizar" if p.items_sin_cotizacion else "hay costos adicionales sin verificar"
        partes.append(f"- **Total final confirmado:** no disponible ({motivo}). El subtotal no incluye envío.")
    if p.items_sin_cotizacion:
        partes.append(f"- **Sin cotización en esta opción:** {', '.join(nombres_items.get(i, i) for i in p.items_sin_cotizacion)}")
    return "\n".join(partes)


def consulta_fallida(resultados: list[ResultadoConsulta], proveedor: str, item_id: str) -> bool:
    """El proveedor no devolvió ofertas para el ítem porque la consulta falló, no porque no las tenga."""
    propios = [r for r in resultados if r.proveedor == proveedor and r.item_id == item_id]
    return bool(propios) and all(r.errores and not r.ofertas for r in propios)


def generar_informe(
    solicitud: Solicitud,
    proveedores: list[str],
    nombres_prov: dict[str, str],
    mecanismos: dict[str, str],
    resultados: list[ResultadoConsulta],
    evaluadas: dict[str, list[OfertaEvaluada]],
    por_proveedor: list[Presupuesto],
    combinado: Presupuesto | None,
    recomendacion: str,
    avisos: list[str],
    fecha: datetime,
) -> str:
    nombres_items = {i.id: i.producto for i in solicitud.items}
    md = [
        "# Presupuesto de compra de mercado",
        "",
        f"- **Solicitud:** {solicitud.texto_original}",
        f"- **Ciudad:** {solicitud.ciudad or 'no indicada'}",
        f"- **Fecha de elaboración:** {fecha:%Y-%m-%d %H:%M %Z}",
        f"- **Proveedores consultados:** {', '.join(nombres_prov.get(p, p) for p in proveedores)}",
        "",
        "## 1. Interpretación de la solicitud",
        "",
    ]
    filas = []
    for i in solicitud.items:
        cantidad = cantidad_pedida(i.cantidad, i.unidad, i.presentacion)
        restricciones = []
        if i.marca:
            restricciones.append(f"marca {i.marca} ({'obligatoria' if i.marca_obligatoria else 'preferida'})")
        if i.presentacion:
            restricciones.append(f"presentación {i.presentacion}")
        restricciones += [f"debe ser {a}" for a in i.atributos] + [f"sin {e}" for e in i.excluir]
        filas.append([
            i.producto,
            f"{i.cantidad:g} {i.unidad or ''}".strip() if i.cantidad is not None else "—",
            formatear_cantidad(cantidad.valor, cantidad.unidad_base) if cantidad else "—",
            "; ".join(restricciones) or "ninguna",
        ])
    md.append(_tabla(["Producto", "Cantidad pedida", "Cantidad normalizada", "Restricciones"], filas))
    if solicitud.suposiciones or solicitud.aclaraciones_pendientes:
        md += ["", "**Suposiciones explícitas:**", ""]
        md += [f"- {s}" for s in solicitud.suposiciones + solicitud.aclaraciones_pendientes]

    md += ["", "## 2. Ofertas encontradas y comparación", ""]
    md.append(
        "Clasificación: *equivalente* (cumple lo pedido), *sustituto aproximado* (otra variedad o marca), "
        "*no comparable* (sin precio o sin presentación interpretable) y *no relacionado*. Los precios por unidad "
        "se calculan con la presentación normalizada (g→kg, mL→L)."
    )
    for item in solicitud.items:
        evs = [e for e in evaluadas.get(item.id, []) if e.tipo != "no_relacionado"]
        md += ["", f"### {item.producto}", ""]
        if not evs:
            md.append("No se encontraron ofertas relacionadas.")
            continue
        orden = {"equivalente": 0, "sustituto": 1, "no_comparable": 2}
        evs.sort(key=lambda e: (orden[e.tipo], e.precio_unitario or float("inf")))
        filas = []
        for e in evs:
            o = e.oferta
            precio = cop(o.precio)
            if o.precio_regular:
                precio += f" (antes {cop(o.precio_regular)})"
            if o.precio_condicionado:
                precio += f"; {cop(o.precio_condicionado)} con condición"
            filas.append([
                nombres_prov.get(o.proveedor, o.proveedor),
                f"[{o.nombre}]({o.url})",
                o.presentacion_texto or "—",
                precio,
                f"{cop(e.precio_unitario)}/{e.unidad_comparacion}" if e.precio_unitario else "—",
                o.disponibilidad,
                _ETIQUETA_TIPO[e.tipo],
                e.motivo,
            ])
        md.append(_tabla(
            ["Proveedor", "Producto", "Presentación", "Precio", "Precio por unidad", "Disponibilidad", "Clasificación", "Motivo"],
            filas,
        ))
        descartados = len(evaluadas.get(item.id, [])) - len(evs)
        if descartados:
            md.append(f"\n_{descartados} resultado(s) del listado se descartaron por no estar relacionados._")

    md += ["", "## 3. Presupuestos", ""]
    cotizados = [p for p in por_proveedor if p.lineas]
    for p in cotizados:
        md += [_presupuesto_md(p, nombres_items, nombres_prov), ""]
    vacios = [nombres_prov.get(p.proveedores[0], p.proveedores[0]) for p in por_proveedor if not p.lineas]
    if vacios:
        md += [f"Sin presupuesto (ningún producto cotizado): {', '.join(vacios)}. El motivo está en la sección 5.", ""]
    if combinado:
        md += [_presupuesto_md(combinado, nombres_items, nombres_prov), ""]
    else:
        md += ["### Compra combinada", "", "No aplica: todos los mejores precios están en un mismo proveedor o no hubo ofertas suficientes.", ""]

    md += ["## 4. Resumen comparativo", ""]
    filas = [
        [p.nombre, f"{len(p.lineas)}/{len(solicitud.items)}", cop(p.subtotal_productos),
         "no verificado" if any(c.valor is None for c in p.costos_adicionales) else cop(sum(c.valor or 0 for c in p.costos_adicionales)),
         cop(p.total_confirmado) if p.total_confirmado is not None else "no confirmado"]
        for p in cotizados + ([combinado] if combinado else [])
    ]
    md.append(_tabla(["Opción", "Productos cubiertos", "Subtotal productos", "Envío", "Total final"], filas))

    md += ["", "## 5. Productos sin cotización", ""]
    sin = []
    for item in solicitud.items:
        faltan = [p.proveedores[0] for p in por_proveedor if item.id in p.items_sin_cotizacion]
        fallidas = [nombres_prov.get(p, p) for p in faltan if consulta_fallida(resultados, p, item.id)]
        sin_oferta = [nombres_prov.get(p, p) for p in faltan if not consulta_fallida(resultados, p, item.id)]
        if fallidas:
            sin.append(f"- **{item.producto}:** no se pudo consultar en {', '.join(fallidas)} (ver incidencias)")
        if sin_oferta:
            sin.append(f"- **{item.producto}:** sin oferta equivalente disponible en {', '.join(sin_oferta)}")
    md += sin or ["- Todos los productos tienen al menos una cotización por proveedor."]
    errores = [(r.proveedor, r.item_id, e) for r in resultados for e in r.errores]
    if errores:
        md += ["", "Incidencias de consulta:", ""]
        md += [f"- {nombres_prov.get(p, p)} / {nombres_items.get(i, i)}: {e}" for p, i, e in errores]

    md += ["", "## 6. Condiciones no verificadas", ""]
    condiciones = list(dict.fromkeys(c for p in por_proveedor + ([combinado] if combinado else []) for c in p.condiciones_no_verificadas))
    condiciones.append("Los costos de envío desconocidos no se tratan como cero: por eso el total final queda sin confirmar.")
    condiciones.append("Los precios con condiciones (tarjetas, membresías) se muestran aparte y no se usan en los totales.")
    md += [f"- {c}" for c in condiciones + avisos]

    md += ["", "## 7. Recomendación", "", recomendacion.strip(), ""]

    md += ["## 8. Fuentes consultadas", ""]
    filas = []
    vistos = set()
    for r in resultados:
        for f in r.fuentes:
            if f.url in vistos:
                continue
            vistos.add(f.url)
            filas.append([nombres_prov.get(r.proveedor, r.proveedor), f.url, f"{f.fecha_consulta:%Y-%m-%d %H:%M}",
                          str(f.productos_leidos)])
    md.append(_tabla(["Proveedor", "URL consultada", "Fecha de consulta", "Productos leídos"], filas))
    md += ["", "Mecanismo de consulta por proveedor:", ""]
    md += [f"- **{nombres_prov.get(p, p)}:** {mecanismos.get(p, '')}" for p in proveedores]
    return "\n".join(md) + "\n"
