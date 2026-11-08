"""Intérprete de robots.txt con comodines (* y $), como lo documenta Google.

`urllib.robotparser` solo compara prefijos y no entiende reglas como
`Disallow: /*?*facets=`, que usan Éxito y Carulla; por eso se implementa aquí.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ReglasRobots:
    reglas: list[tuple[bool, str, re.Pattern[str]]] = field(default_factory=list)  # (permitir, patrón, regex)

    def permite(self, ruta: str) -> bool:
        """Aplica la regla más específica (patrón más largo); en empate gana Allow."""
        mejor: tuple[int, bool] | None = None
        for permitir, patron, regex in self.reglas:
            if regex.match(ruta):
                candidato = (len(patron), permitir)
                if mejor is None or candidato > mejor:
                    mejor = candidato
        return True if mejor is None else mejor[1]


def _a_regex(patron: str) -> re.Pattern[str]:
    ancla = patron.endswith("$")
    if ancla:
        patron = patron[:-1]
    cuerpo = ".*".join(re.escape(p) for p in patron.split("*"))
    return re.compile("^" + cuerpo + ("$" if ancla else ""))


def interpretar_robots(texto: str, agente: str = "*") -> ReglasRobots:
    """Reúne todas las reglas de los grupos que aplican a `agente` (o a `*`)."""
    reglas_especificas: list[tuple[bool, str, re.Pattern[str]]] = []
    reglas_generales: list[tuple[bool, str, re.Pattern[str]]] = []
    agentes_grupo: list[str] = []
    leyendo_agentes = False
    for linea in texto.splitlines():
        linea = linea.split("#", 1)[0].strip()
        if ":" not in linea:
            continue
        clave, valor = (s.strip() for s in linea.split(":", 1))
        clave = clave.lower()
        if clave == "user-agent":
            if not leyendo_agentes:
                agentes_grupo = []
            agentes_grupo.append(valor.lower())
            leyendo_agentes = True
            continue
        leyendo_agentes = False
        if clave not in ("allow", "disallow") or not valor:
            continue
        if not valor.startswith(("/", "*")):
            valor = "/" + valor
        regla = (clave == "allow", valor, _a_regex(valor))
        if agente.lower() != "*" and agente.lower() in agentes_grupo:
            reglas_especificas.append(regla)
        elif "*" in agentes_grupo:
            reglas_generales.append(regla)
    return ReglasRobots(reglas_especificas or reglas_generales)
