"""Utilidades de texto: normalización sin tildes, tokens y raíces simples."""

from __future__ import annotations

import re
import unicodedata

_PALABRAS_VACIAS = {
    "de", "del", "la", "el", "los", "las", "y", "x", "en", "con", "sin", "para",
    "por", "a", "un", "una", "e", "o",
}


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con espacios colapsados."""
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower().replace("\xa0", " ")
    return re.sub(r"\s+", " ", texto).strip()


def raiz(palabra: str) -> str:
    """Quita el plural español más común ("aceites" -> "aceite", "panelas" -> "panela")."""
    p = normalizar(palabra)
    # Singulares terminados en consonante forman el plural con "es" (limón/limones,
    # azúcar/azúcares); los terminados en vocal solo con "s" (aceite/aceites).
    if len(p) > 4 and p.endswith("es") and p[-3] in "lnrdj":
        return p[:-2]
    if len(p) > 3 and p.endswith("s"):
        return p[:-1]
    return p


def tokens(texto: str) -> list[str]:
    """Tokens alfanuméricos normalizados, sin palabras vacías."""
    return [t for t in re.split(r"[^a-z0-9]+", normalizar(texto)) if t and t not in _PALABRAS_VACIAS]


def raices(texto: str) -> set[str]:
    return {raiz(t) for t in tokens(texto)}


def contiene_termino(texto: str, termino: str) -> bool:
    """True si todas las raíces del término aparecen en el texto."""
    r_texto = raices(texto)
    r_term = raices(termino)
    return bool(r_term) and r_term <= r_texto


def slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalizar(texto)).strip("-")


def cop(valor: float | None) -> str:
    """Formato de pesos colombianos: 16300 -> '$16.300'."""
    return "—" if valor is None else f"${valor:,.0f}".replace(",", ".")
