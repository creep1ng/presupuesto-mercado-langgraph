"""Selección de empaques y construcción de presupuestos.

Toda la aritmética es determinista; el LLM no calcula totales.
"""

from __future__ import annotations

import math
from itertools import combinations

from .modelos import CostoAdicional, ItemSolicitado, Linea, OfertaEvaluada, Presupuesto, Solicitud
from .texto import cop, normalizar
from .unidades import cantidad_pedida, formatear_cantidad


def _familia(ev: OfertaEvaluada) -> str:
    """Mismo producto en distintas presentaciones (para no mezclar marcas en un ítem)."""
    o = ev.oferta
    nombre = normalizar(o.nombre)
    if o.presentacion_texto:
        nombre = nombre.replace(normalizar(o.presentacion_texto), "")
    return " ".join(nombre.replace("(", " ").replace(")", " ").split())


def mejor_linea(item: ItemSolicitado, proveedor: str, opciones: list[OfertaEvaluada]) -> Linea | None:
    """Combinación de empaques más barata que cubre la cantidad pedida.

    Se evalúan un solo producto en una o varias unidades, y combinaciones de dos
    presentaciones del mismo producto (p. ej. 3 kg + 2 kg para pedir 5 kg).
    """
    pedido = cantidad_pedida(item.cantidad, item.unidad, item.presentacion)
    if pedido is None:
        return None
    validas = [
        ev for ev in opciones
        if ev.oferta.proveedor == proveedor and ev.oferta.precio and ev.oferta.disponibilidad != "agotado"
        and (pedido.unidad_base == "empaque" or (ev.oferta.contenido and ev.oferta.unidad_base == pedido.unidad_base))
    ]
    if not validas:
        return None

    def contenido(ev: OfertaEvaluada) -> float:
        return 1.0 if pedido.unidad_base == "empaque" else ev.oferta.contenido  # type: ignore[return-value]

    q = pedido.valor
    soluciones: list[tuple[float, int, float, list[tuple[OfertaEvaluada, int]]]] = []

    def considerar(combo: list[tuple[OfertaEvaluada, int]]) -> None:
        combo = [(ev, n) for ev, n in combo if n > 0]
        cubierto = sum(contenido(ev) * n for ev, n in combo)
        if cubierto + 1e-9 < q:
            return
        costo = sum(ev.oferta.precio * n for ev, n in combo)  # type: ignore[operator]
        soluciones.append((costo, sum(n for _, n in combo), cubierto - q, combo))

    for ev in validas:
        considerar([(ev, max(1, math.ceil(q / contenido(ev) - 1e-9)))])
    baratas = sorted(validas, key=lambda ev: ev.oferta.precio / contenido(ev))[:10]  # type: ignore[operator]
    for a, b in combinations(baratas, 2):
        if _familia(a) != _familia(b):
            continue
        for n_a in range(1, math.ceil(q / contenido(a)) + 1):
            resto = q - n_a * contenido(a)
            n_b = max(0, math.ceil(resto / contenido(b) - 1e-9))
            considerar([(a, n_a), (b, n_b)])
    if not soluciones:
        return None
    # La más barata; si otra cuesta como máximo 2 % más y usa menos empaques, se prefiere esa.
    minimo = min(s[0] for s in soluciones)
    cercanas = [s for s in soluciones if s[0] <= minimo * 1.02]
    empaques = min(cercanas, key=lambda s: (s[1], s[0], s[2]))[3]
    cubierto = sum(contenido(ev) * n for ev, n in empaques)
    tipos = {ev.tipo for ev, _ in empaques}
    notas = []
    if cubierto > q * 1.15:
        notas.append(
            f"Se compra {formatear_cantidad(cubierto, pedido.unidad_base)} para cubrir "
            f"{formatear_cantidad(q, pedido.unidad_base)} por las presentaciones disponibles"
        )
    for ev, _ in empaques:
        if ev.oferta.precio_condicionado:
            notas.append(
                f"{ev.oferta.nombre}: {ev.oferta.condicion_precio or 'precio condicionado'} "
                f"{cop(ev.oferta.precio_condicionado)} (no aplicado)"
            )
    return Linea(
        item_id=item.id,
        proveedor=proveedor,
        empaques=[(ev.oferta, n) for ev, n in empaques],
        cantidad_cubierta=cubierto,
        cantidad_solicitada=q,
        unidad_base=pedido.unidad_base,
        costo=sum(ev.oferta.precio * n for ev, n in empaques),  # type: ignore[operator]
        tipo="sustituto" if "sustituto" in tipos else "equivalente",
        notas=notas,
    )


def _linea_item(
    item: ItemSolicitado, proveedor: str, evaluadas: list[OfertaEvaluada], aceptar_sustitutos: bool
) -> Linea | None:
    linea = mejor_linea(item, proveedor, [e for e in evaluadas if e.tipo == "equivalente"])
    if linea is None and aceptar_sustitutos:
        linea = mejor_linea(item, proveedor, [e for e in evaluadas if e.tipo == "sustituto"])
    return linea


def _costos_envio(solicitud: Solicitud, proveedores: list[str], nombres: dict[str, str]) -> list[CostoAdicional]:
    costos = []
    for p in proveedores:
        conocidos = [c for c in solicitud.costos_adicionales if c.proveedor == p]
        if conocidos:
            costos += conocidos
        else:
            costos.append(
                CostoAdicional(
                    concepto=f"Domicilio / envío {nombres.get(p, p)}",
                    proveedor=p,
                    valor=None,
                    estado="no_verificado",
                    fuente="Depende de la dirección, la franja de entrega y el monto mínimo; solo se confirma en el "
                           "checkout del comercio, que no se consulta",
                )
            )
    costos += [c for c in solicitud.costos_adicionales if c.proveedor is None]
    return costos


def _condiciones(solicitud: Solicitud, proveedores: list[str], nombres: dict[str, str], lineas: list[Linea]) -> list[str]:
    condiciones = []
    ciudad = solicitud.ciudad or "la ciudad de entrega"
    for p in proveedores:
        condiciones.append(
            f"{nombres.get(p, p)}: precio publicado en el sitio sin seleccionar tienda ni dirección; puede variar en "
            f"{ciudad}. La cobertura de entrega en {ciudad} no se verificó."
        )
    for linea in lineas:
        for oferta, _ in linea.empaques:
            if oferta.disponibilidad == "desconocida":
                condiciones.append(f"Disponibilidad de '{oferta.nombre}' ({nombres.get(oferta.proveedor, oferta.proveedor)}) no publicada")
            if oferta.fuente_tipo == "catalogo_referencia":
                condiciones.append(f"'{oferta.nombre}' proviene de un catálogo de referencia, no de una consulta en línea")
            condiciones += [f"{oferta.nombre}: {n}" for n in oferta.notas if "catálogo" not in n]
    return list(dict.fromkeys(condiciones))


def _armar(nombre: str, solicitud: Solicitud, lineas: list[Linea], proveedores: list[str], nombres: dict[str, str]) -> Presupuesto:
    cubiertos = {l.item_id for l in lineas}
    faltantes = [i.id for i in solicitud.items if i.id not in cubiertos]
    usados = [p for p in proveedores if any(l.proveedor == p for l in lineas)]
    costos = _costos_envio(solicitud, usados, nombres)
    subtotal = sum(l.costo for l in lineas)
    total = subtotal + sum(c.valor for c in costos) if costos and all(c.valor is not None for c in costos) else None
    return Presupuesto(
        nombre=nombre,
        proveedores=usados or proveedores,
        lineas=lineas,
        items_sin_cotizacion=faltantes,
        subtotal_productos=subtotal,
        costos_adicionales=costos,
        total_confirmado=total if not faltantes else None,
        cobertura_completa=not faltantes,
        condiciones_no_verificadas=_condiciones(solicitud, usados, nombres, lineas),
    )


def construir_presupuestos(
    solicitud: Solicitud,
    evaluadas: dict[str, list[OfertaEvaluada]],
    proveedores: list[str],
    nombres: dict[str, str],
    aceptar_sustitutos: bool = False,
) -> tuple[list[Presupuesto], Presupuesto | None]:
    """Un presupuesto por proveedor y, si es viable, uno combinado."""
    por_proveedor: list[Presupuesto] = []
    candidatas: dict[str, list[Linea]] = {i.id: [] for i in solicitud.items}
    for p in proveedores:
        lineas = []
        for item in solicitud.items:
            linea = _linea_item(item, p, evaluadas.get(item.id, []), aceptar_sustitutos)
            if linea:
                lineas.append(linea)
                candidatas[item.id].append(linea)
        por_proveedor.append(_armar(f"Compra completa en {nombres.get(p, p)}", solicitud, lineas, [p], nombres))

    # Combinada: por ítem, la línea más barata entre proveedores (equivalentes antes que sustitutos)
    elegidas = []
    for item in solicitud.items:
        opciones = sorted(candidatas[item.id], key=lambda l: (l.tipo != "equivalente", l.costo))
        if opciones:
            elegidas.append(opciones[0])
    usados = list(dict.fromkeys(l.proveedor for l in elegidas))
    combinado = None
    if len(usados) >= 2:
        combinado = _armar("Compra combinada (mejor precio por producto)", solicitud, elegidas, usados, nombres)
    return por_proveedor, combinado


def elegir_recomendacion(por_proveedor: list[Presupuesto], combinado: Presupuesto | None) -> dict:
    """Regla de decisión explícita; el LLM solo redacta la explicación."""
    completos = sorted((p for p in por_proveedor if p.cobertura_completa), key=lambda p: p.subtotal_productos)
    resultado: dict = {"opcion": None, "motivos": []}
    if completos:
        mejor = completos[0]
        resultado["opcion"] = mejor.nombre
        resultado["motivos"].append(
            f"{mejor.nombre} tiene el menor subtotal de productos entre los proveedores con cobertura completa "
            f"({len(completos)} de {len(por_proveedor)})"
        )
        if len(completos) > 1:
            resultado["motivos"].append(
                f"Diferencia con la siguiente opción ({completos[1].nombre}): "
                f"{cop(completos[1].subtotal_productos - mejor.subtotal_productos)}"
            )
        if combinado and combinado.cobertura_completa:
            ahorro = mejor.subtotal_productos - combinado.subtotal_productos
            extra = len(combinado.proveedores) - 1
            # cada domicilio adicional debe compensarse con al menos max($5.000, 3 % del subtotal)
            umbral = extra * max(5000.0, 0.03 * mejor.subtotal_productos)
            if ahorro > umbral:
                resultado["opcion"] = combinado.nombre
                resultado["motivos"].append(
                    f"La compra combinada ahorra {cop(ahorro)} en productos, más que el umbral de {cop(umbral)} "
                    f"fijado para compensar {extra} domicilio(s) adicional(es) de costo no verificado"
                )
            elif ahorro > 0:
                resultado["motivos"].append(
                    f"La compra combinada solo ahorraría {cop(ahorro)}, que no compensa {extra} domicilio(s) "
                    f"adicional(es) de costo desconocido (umbral: {cop(umbral)})"
                )
    elif combinado:
        resultado["opcion"] = combinado.nombre
        resultado["motivos"].append("Ningún proveedor cubre todos los productos; la compra combinada es la de mayor cobertura")
    else:
        mejor = max(por_proveedor, key=lambda p: (len(p.lineas), -p.subtotal_productos), default=None)
        if mejor and mejor.lineas:
            resultado["opcion"] = mejor.nombre
            resultado["motivos"].append("Ningún proveedor cubre todos los productos; es el de mayor cobertura")
    return resultado
