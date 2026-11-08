"""Interfaz de línea de comandos."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langgraph.types import Command
from rich.console import Console
from rich.markdown import Markdown

from .grafo import construir_grafo, crear_checkpointer
from .navegador import Navegador
from .proveedores import PROVEEDORES_WEB
from .reporte import cop

consola = Console()


def _args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="presupuesto-mercado",
        description="Cotiza una lista de mercado en supermercados colombianos y elabora un presupuesto con fuentes.",
    )
    p.add_argument("solicitud", nargs="?", help="Solicitud en lenguaje natural (si se omite, se pide por consola)")
    p.add_argument("--proveedores", help=f"Lista separada por comas: {', '.join(PROVEEDORES_WEB)} (anula la de la solicitud)")
    p.add_argument("--catalogo", action="append", default=[], metavar="ID=RUTA",
                   help="Catálogo de referencia CSV/JSON/XLSX para un comercio sin precios en línea (p. ej. ara=datos/ara.csv)")
    p.add_argument("--interactivo", action="store_true", help="Preguntar al usuario cuando falten datos imprescindibles")
    p.add_argument("--aceptar-sustitutos", action="store_true", help="Usar sustitutos cuando no haya productos equivalentes")
    p.add_argument("--sin-llm", action="store_true", help="Usar solo reglas (sin modelo de lenguaje)")
    p.add_argument("--modelo", help="Modelo de LangChain, p. ej. anthropic:claude-sonnet-5-5")
    p.add_argument("--sin-cache", action="store_true", help="Ignorar la caché de páginas (consulta todo de nuevo)")
    p.add_argument("--salida", default="salidas", help="Carpeta donde se guardan el informe y la evidencia")
    return p.parse_args(argv)


def guardar_resultados(final: dict, salida: Path, sufijo: str = "") -> tuple[Path, Path]:
    """Escribe el informe Markdown y la evidencia JSON; devuelve sus rutas."""
    salida.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d-%H%M%S") + sufijo
    ruta_md = salida / f"presupuesto-{marca}.md"
    ruta_json = salida / f"evidencia-{marca}.json"
    ruta_md.write_text(final["informe"], encoding="utf-8")
    evidencia = {
        "solicitud": final["solicitud"].model_dump(mode="json"),
        "proveedores": final["proveedores"],
        "consultas": [r.model_dump(mode="json") for r in final["resultados"]],
        "evaluaciones": {k: [e.model_dump(mode="json") for e in v] for k, v in final["evaluadas"].items()},
        "presupuestos": [p.model_dump(mode="json") for p in final["presupuestos"]],
        "combinado": final["combinado"].model_dump(mode="json") if final.get("combinado") else None,
        "decision": final["decision"],
    }
    ruta_json.write_text(json.dumps(evidencia, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta_md, ruta_json


async def ejecutar(args: argparse.Namespace) -> int:
    texto = args.solicitud or consola.input("[bold]¿Qué necesita comprar?[/] ")
    catalogos = {}
    for c in args.catalogo:
        ident, _, ruta = c.partition("=")
        if not ruta or not Path(ruta).exists():
            consola.print(f"[red]Catálogo inválido: {c}[/]")
            return 2
        catalogos[ident.strip().lower()] = ruta
    opciones = {
        "interactivo": args.interactivo,
        "aceptar_sustitutos": args.aceptar_sustitutos,
        "catalogos": catalogos,
        "proveedores": [p.strip().lower() for p in args.proveedores.split(",")] if args.proveedores else None,
    }
    grafo = construir_grafo(crear_checkpointer())
    async with Navegador(Path(".cache"), usar_cache=not args.sin_cache) as nav:
        config = {"configurable": {"thread_id": str(uuid.uuid4()), "navegador": nav,
                                   "sin_llm": args.sin_llm, "modelo": args.modelo}}
        entrada: object = {"texto": texto, "opciones": opciones}
        with consola.status("Procesando la solicitud…") as estado_ui:
            while True:
                async for evento in grafo.astream(entrada, config, stream_mode="updates"):
                    for nodo in evento:
                        if nodo != "__interrupt__":
                            estado_ui.update(f"Paso completado: [bold]{nodo}[/]")
                pendiente = grafo.get_state(config)
                if not pendiente.interrupts:
                    break
                estado_ui.stop()
                preguntas = pendiente.interrupts[0].value["preguntas"]
                consola.print("[yellow]Faltan datos:[/]")
                for pregunta in preguntas:
                    consola.print(f"  • {pregunta}")
                respuesta = consola.input("[bold]Respuesta:[/] ")
                entrada = Command(resume=respuesta)
                estado_ui.start()
        final = grafo.get_state(config).values

    ruta_md, ruta_json = guardar_resultados(final, Path(args.salida))

    consola.print(Markdown(final["informe"]))
    consola.rule()
    for p in final["presupuestos"] + ([final["combinado"]] if final.get("combinado") else []):
        total = cop(p.total_confirmado) if p.total_confirmado is not None else "no confirmado"
        consola.print(f"{p.nombre}: {len(p.lineas)}/{len(final['solicitud'].items)} productos · "
                      f"subtotal {cop(p.subtotal_productos)} · total final {total}")
    consola.print(f"\nInforme: [bold]{ruta_md}[/]\nEvidencia: [bold]{ruta_json}[/]")
    return 0


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    sys.exit(asyncio.run(ejecutar(_args(argv))))
