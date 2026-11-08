"""Normalización de unidades y extracción de presentaciones desde nombres de producto.

Todas las cantidades se llevan a una unidad base: gramos (g), mililitros (ml),
unidades (und) o empaques (cuando el usuario pide "2 paquetes" sin decir de qué tamaño).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .texto import normalizar

# Unidad del usuario -> (unidad base, factor)
_UNIDADES_USUARIO: dict[str, tuple[str, float]] = {}
for _alias, _base, _factor in [
    (("kg", "kgs", "kilo", "kilos", "kilogramo", "kilogramos"), "g", 1000),
    (("g", "gr", "grs", "gramo", "gramos"), "g", 1),
    # En Colombia la libra de mercado equivale a 500 g y la arroba a 25 libras.
    (("lb", "lbs", "libra", "libras"), "g", 500),
    (("arroba", "arrobas"), "g", 12500),
    (("l", "lt", "lts", "litro", "litros"), "ml", 1000),
    (("ml", "cc", "mililitro", "mililitros"), "ml", 1),
    (("galon", "galones"), "ml", 3785),
    (("und", "unds", "un", "u", "unidad", "unidades", "uds", "rollo", "rollos", "huevo", "huevos"), "und", 1),
    (("docena", "docenas"), "und", 12),
    (("paquete", "paquetes", "bolsa", "bolsas", "caja", "cajas", "botella", "botellas", "lata", "latas",
      "paca", "pacas", "frasco", "frascos", "empaque", "empaques", "sobre", "sobres", "tarro", "tarros"), "empaque", 1),
]:
    for _a in _alias:
        _UNIDADES_USUARIO[_a] = (_base, _factor)

UNIDAD_COMPARACION = {"g": ("kg", 1000), "ml": ("L", 1000), "und": ("und", 1), "empaque": ("empaque", 1)}


@dataclass(frozen=True)
class Cantidad:
    valor: float
    unidad_base: str  # g | ml | und | empaque
    nota: str | None = None


def convertir_cantidad_usuario(cantidad: float | None, unidad: str | None) -> Cantidad | None:
    """Convierte lo que pidió el usuario (5 kg, 2 litros, 1 docena) a unidad base."""
    if cantidad is None:
        return None
    u = normalizar(unidad or "").strip(". ")
    if not u:
        return Cantidad(cantidad, "empaque", "Sin unidad: se interpreta como número de empaques")
    if u not in _UNIDADES_USUARIO:
        return None
    base, factor = _UNIDADES_USUARIO[u]
    nota = None
    if u in ("lb", "lbs", "libra", "libras"):
        nota = "Se asume la libra colombiana de 500 g"
    elif u in ("arroba", "arrobas"):
        nota = "Se asume la arroba colombiana de 12,5 kg"
    return Cantidad(cantidad * factor, base, nota)


# ---------------------------------------------------------------------------
# Presentaciones publicadas por los comercios
# ---------------------------------------------------------------------------

_NUM = r"(\d+(?:[.,]\d+)?)"
_UNI = (r"(kgs?|kilos?|kilogramos?|grs?|g|gramos?|lbs?|libras?|ml|mililitros?|cc|lts?|l|litros?|"
        r"unds?|unidad|unidades|uds|un|u|rollos?|huevos)")
_FIN = r"(?![a-z])"

_RE_MULTI_A = re.compile(rf"(?:^|[^\w])(\d+)\s*(?:unds?|unidades|uds|u|pack|x)?\s*x\s*{_NUM}\s*{_UNI}{_FIN}")
_RE_MULTI_B = re.compile(rf"{_NUM}\s*{_UNI}\s*x\s*(\d+)\s*(?:unds?|unidades|uds|u)?{_FIN}")
_RE_SIMPLE = re.compile(rf"(?:^|[^\w.,])x?\s*{_NUM}\s*{_UNI}{_FIN}")
_RE_CONTEO_SUELTO = re.compile(r"(?:^|\s)x\s*(\d+)(?![\d.,a-z])")


def _numero(texto: str, unidad_base: str) -> float:
    """'3.000' -> 3000 en g/ml/und; '1.5' -> 1.5; '2,5' -> 2.5."""
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", texto) and unidad_base in ("g", "ml", "und"):
        return float(texto.replace(".", ""))
    return float(texto.replace(",", "."))


def _base_de(unidad: str) -> tuple[str, float]:
    u = unidad.rstrip(".")
    if u in ("unds", "uds", "un", "u", "unidad", "unidades", "huevos", "rollo", "rollos"):
        u = "und"
    return _UNIDADES_USUARIO.get(u, ("", 0))


@dataclass(frozen=True)
class Presentacion:
    contenido: float  # total del empaque en unidad base
    unidad_base: str
    texto: str


def extraer_presentacion(nombre: str, permitir_conteo_suelto: bool = False) -> Presentacion | None:
    p = _extraer(nombre, permitir_conteo_suelto)
    return Presentacion(p.contenido, p.unidad_base, p.texto.strip("( )")) if p else None


def _extraer(nombre: str, permitir_conteo_suelto: bool) -> Presentacion | None:
    """Obtiene el contenido total del empaque a partir del nombre publicado.

    Ejemplos: "Arroz DIANA (1000 gr)" -> 1000 g; "Aceite x3L" -> 3000 ml;
    "Leche 6 und x 1100 ml" -> 6600 ml; "Huevo AA x 30 und" -> 30 und.
    Devuelve None si no hay información suficiente (el resultado será "no comparable").
    """
    t = normalizar(nombre)

    m = _RE_MULTI_A.search(t)
    if m:
        n, num, uni = int(m.group(1)), m.group(2), m.group(3)
        base, factor = _base_de(uni)
        if base and n > 1:
            return Presentacion(n * _numero(num, base) * factor, base, m.group(0).strip())
    m = _RE_MULTI_B.search(t)
    if m:
        num, uni, n = m.group(1), m.group(2), int(m.group(3))
        base, factor = _base_de(uni)
        if base and n > 1:
            return Presentacion(n * _numero(num, base) * factor, base, m.group(0).strip())
    # Si el nombre trae peso/volumen y conteo ("x2und (3600 ml)"), el peso/volumen es el contenido total.
    simples = []
    for m in _RE_SIMPLE.finditer(t):
        base, factor = _base_de(m.group(2))
        if base:
            valor = _numero(m.group(1), base) * factor
            if valor > 0:
                simples.append(Presentacion(valor, base, m.group(0).strip()))
    if simples:
        return next((p for p in simples if p.unidad_base != "und"), simples[0])
    if permitir_conteo_suelto:
        m = _RE_CONTEO_SUELTO.search(t)
        if m and int(m.group(1)) > 0:
            return Presentacion(float(m.group(1)), "und", m.group(0).strip())
    return None


def precio_unitario(precio: float, contenido: float, unidad_base: str) -> tuple[float, str]:
    """Precio por kg, L o unidad, para mostrar comparaciones legibles."""
    etiqueta, factor = UNIDAD_COMPARACION[unidad_base]
    return precio / contenido * factor, etiqueta


def formatear_cantidad(valor: float, unidad_base: str) -> str:
    if unidad_base == "g":
        return f"{valor / 1000:g} kg" if valor >= 1000 else f"{valor:g} g"
    if unidad_base == "ml":
        return f"{valor / 1000:g} L" if valor >= 1000 else f"{valor:g} mL"
    if unidad_base == "empaque":
        return f"{valor:g} empaque(s)"
    return f"{valor:g} und"


def cantidad_pedida(cantidad: float | None, unidad: str | None, presentacion: str | None = None) -> Cantidad | None:
    """Cantidad total pedida. "1 paca de 12 rollos" se convierte en 12 und para poder comparar contenidos."""
    base = convertir_cantidad_usuario(cantidad, unidad)
    if base is not None and base.unidad_base == "empaque" and presentacion:
        p = extraer_presentacion(presentacion, permitir_conteo_suelto=True)
        if p is not None:
            return Cantidad(base.valor * p.contenido, p.unidad_base,
                            f"{base.valor:g} empaque(s) de {presentacion} = {formatear_cantidad(base.valor * p.contenido, p.unidad_base)}")
    return base
