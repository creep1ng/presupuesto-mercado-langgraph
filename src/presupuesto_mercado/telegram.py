"""Adaptador de Telegram: recibe solicitudes por un bot y responde con el presupuesto.

Se usa la Bot API por long polling (getUpdates), sin webhook ni puertos abiertos.
Cada chat tiene su propio hilo del grafo: si falta un dato imprescindible, el bot
pregunta y el siguiente mensaje del usuario reanuda el grafo (interrupt).

En consola se registra cada mensaje recibido, cada paso del grafo y cada envío a
Telegram. El token del bot nunca se imprime: los errores de red se reportan sin URL.

Variables de entorno:
    TELEGRAM_BOT_TOKEN          token entregado por @BotFather (obligatorio)
    TELEGRAM_CHATS_PERMITIDOS   ids de chat separados por comas; si se define, solo esos chats se atienden
    TELEGRAM_API_URL            URL base de la Bot API (por defecto https://api.telegram.org)
"""

from __future__ import annotations

import argparse
import asyncio
import html
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from langgraph.types import Command
from rich.console import Console
from rich.markup import escape

from .cli import guardar_resultados
from .grafo import construir_grafo, crear_checkpointer
from .llm import MODELO_POR_DEFECTO, crear_llm
from .navegador import Navegador
from .texto import cop

LIMITE_MENSAJE = 4096

AYUDA = (
    "Envíe su lista de mercado en un mensaje, por ejemplo:\n\n"
    "<i>Necesito 5 kg de arroz, 2 litros de aceite y 3 kg de azúcar. Consulta dos supermercados "
    "y prepara un presupuesto para Medellín.</i>\n\n"
    "Se consultan los precios publicados por Éxito, Carulla, Jumbo, D1 y Olímpica, y se responde con un "
    "resumen y el informe completo con enlaces y fuentes. La consulta tarda de uno a tres minutos.\n\n"
    "/cancelar descarta una pregunta pendiente."
)


class ErrorTelegram(Exception):
    """Falla de la Bot API o de la red. El mensaje nunca incluye el token."""


class ClienteTelegram:
    def __init__(self, token: str, base_url: str = "https://api.telegram.org",
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = f"{base_url.rstrip('/')}/bot{token}"
        # El long polling espera hasta 50 s; el tiempo de lectura debe ser mayor.
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(70, connect=15), transport=transport)

    async def cerrar(self) -> None:
        await self._http.aclose()

    async def llamar(self, metodo: str, datos: dict[str, Any] | None = None,
                     archivos: dict[str, Any] | None = None, intentos: int = 3) -> Any:
        for intento in range(intentos):
            try:
                if archivos:
                    r = await self._http.post(f"{self._url}/{metodo}", data=datos, files=archivos)
                else:
                    r = await self._http.post(f"{self._url}/{metodo}", json=datos or {})
                cuerpo = r.json()
            except (httpx.HTTPError, ValueError) as e:
                # Sin encadenar la excepción original: su texto puede contener la URL con el token.
                raise ErrorTelegram(f"{metodo}: {type(e).__name__}") from None
            if cuerpo.get("ok"):
                return cuerpo["result"]
            espera = (cuerpo.get("parameters") or {}).get("retry_after")
            if r.status_code == 429 and espera and intento < intentos - 1:
                await asyncio.sleep(espera)
                continue
            raise ErrorTelegram(f"{metodo}: {r.status_code} {cuerpo.get('description', '')}".strip())
        raise ErrorTelegram(f"{metodo}: sin respuesta")

    async def obtener_bot(self) -> dict:
        return await self.llamar("getMe")

    async def obtener_actualizaciones(self, offset: int | None, espera: int = 50) -> list[dict]:
        datos: dict[str, Any] = {"timeout": espera, "allowed_updates": ["message"]}
        if offset is not None:
            datos["offset"] = offset
        return await self.llamar("getUpdates", datos)

    async def enviar_mensaje(self, chat_id: int, texto: str, responder_a: int | None = None) -> dict:
        datos: dict[str, Any] = {"chat_id": chat_id, "text": texto, "parse_mode": "HTML",
                                 "link_preview_options": {"is_disabled": True}}
        if responder_a is not None:
            datos["reply_parameters"] = {"message_id": responder_a, "allow_sending_without_reply": True}
        return await self.llamar("sendMessage", datos)

    async def enviar_documento(self, chat_id: int, nombre: str, contenido: bytes, leyenda: str) -> dict:
        return await self.llamar("sendDocument", {"chat_id": str(chat_id), "caption": leyenda},
                                 {"document": (nombre, contenido, "text/markdown")})

    async def indicar_escribiendo(self, chat_id: int) -> None:
        await self.llamar("sendChatAction", {"chat_id": chat_id, "action": "typing"}, intentos=1)


# ------------------------------------------------------------------ formato


def html_desde_markdown(texto: str) -> str:
    """Convierte el Markdown sencillo de la recomendación (negritas y viñetas) al HTML de Telegram."""
    texto = html.escape(texto, quote=False)
    texto = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", texto)
    return re.sub(r"(?m)^[-*] ", "• ", texto)


def recortar(texto: str, limite: int = LIMITE_MENSAJE) -> str:
    if len(texto) <= limite:
        return texto
    corte = texto.rfind("\n", 0, limite - 60)
    return texto[: corte if corte > 0 else limite - 60] + "\n\n<i>(continúa en el informe adjunto)</i>"


def resumen_html(final: dict) -> str:
    """Resumen para el chat: opciones con subtotal y total final, y la recomendación."""
    solicitud = final["solicitud"]
    n = len(solicitud.items)
    partes = [f"<b>Presupuesto de mercado</b> · {html.escape(solicitud.ciudad or 'ciudad no indicada')}", ""]
    partes += [f"• {html.escape(i.texto_original or i.producto)}" for i in solicitud.items]
    partes += ["", "<b>Opciones</b>"]
    opciones = [p for p in final["presupuestos"] if p.lineas] + ([final["combinado"]] if final.get("combinado") else [])
    for p in opciones:
        total = cop(p.total_confirmado) if p.total_confirmado is not None else "no confirmado"
        partes.append(f"• {html.escape(p.nombre)}: {len(p.lineas)}/{n} productos · subtotal "
                      f"<b>{cop(p.subtotal_productos)}</b> · total final {total}")
    if not opciones:
        partes.append("• Ningún supermercado tiene productos cotizados.")
    nombres_items = {i.id: i.producto for i in solicitud.items}
    fallidas: dict[str, list[str]] = {}
    for r in final["resultados"]:
        if r.errores and not r.ofertas:
            fallidas.setdefault(final["nombres"].get(r.proveedor, r.proveedor), []).append(
                nombres_items.get(r.item_id, r.item_id))
    if fallidas:
        partes += ["", "<b>No se pudo consultar</b> (falla del sitio o de la red, no falta de producto)"]
        partes += [f"• {html.escape(p)}: {html.escape(', '.join(dict.fromkeys(i)))}" for p, i in fallidas.items()]
    partes += ["", "<b>Recomendación</b>", html_desde_markdown(final["recomendacion"])]
    partes += ["", "<i>Los precios son los publicados en los sitios web y pueden variar en su ciudad. "
                   "El envío no se toma como cero: si no se conoce, el total final queda sin confirmar.</i>"]
    return recortar("\n".join(partes))


def _item_html(item: Any) -> str:
    cantidad = f"{item.cantidad:g} {item.unidad or ''}".strip() if item.cantidad is not None else "cantidad sin indicar"
    detalles = [d for d in (item.presentacion, *item.atributos) if d]
    if item.marca:
        detalles.append(f"solo {item.marca}" if item.marca_obligatoria else f"marca {item.marca} preferida")
    extra = f" ({', '.join(detalles)})" if detalles else ""
    return f"• {html.escape(cantidad)} de {html.escape(item.producto)}{html.escape(extra)}"


def plan_html(solicitud: Any, nombres: dict[str, str], aclarada: bool, presentar: int | None = None) -> str:
    """Confirma qué se va a buscar, dónde y para qué ciudad, antes de consultar los supermercados."""
    inicio = "Entendido, con su aclaración se buscará:" if aclarada else "Se buscará:"
    lineas = [inicio, ""] + [_item_html(i) for i in solicitud.items]
    lineas += ["", f"<b>Ciudad:</b> {html.escape(solicitud.ciudad or 'sin indicar')}",
               f"<b>Supermercados:</b> {html.escape(', '.join(nombres.values()))}"]
    if presentar:
        lineas.append(f"Se presentarán los {presentar} con más productos y mejor precio.")
    lineas += ["", "Se están consultando los precios; esto tarda de uno a tres minutos."]
    return "\n".join(lineas)


def preguntas_html(preguntas: list[str]) -> str:
    lineas = ["Para preparar el presupuesto faltan datos:", ""]
    lineas += [f"• {html.escape(p)}" for p in preguntas]
    lineas += ["", "Responda en un mensaje, o envíe /cancelar para descartar la solicitud."]
    return "\n".join(lineas)


# ------------------------------------------------------------------ servidor


@dataclass
class Conversacion:
    candado: asyncio.Lock = field(default_factory=asyncio.Lock)
    hilo_pendiente: str | None = None  # hilo del grafo detenido esperando una aclaración


class ServidorTelegram:
    def __init__(self, cliente: ClienteTelegram, navegador: Any, *, llm: Any = None, opciones: dict | None = None,
                 salida: Path = Path("salidas/telegram"), chats_permitidos: set[int] | None = None,
                 simultaneas: int = 2, consola: Console | None = None) -> None:
        self.cliente = cliente
        self.navegador = navegador
        self.llm = llm
        self.opciones = {"interactivo": True, **(opciones or {})}
        self.salida = salida
        self.chats_permitidos = chats_permitidos
        self.consola = consola or Console(log_path=False)
        self.grafo = construir_grafo(crear_checkpointer())
        self._conversaciones: dict[int, Conversacion] = {}
        # Limita cuántas solicitudes consultan los supermercados a la vez.
        self._cupo = asyncio.Semaphore(simultaneas)
        self._tareas: set[asyncio.Task] = set()

    # -- registro en consola

    def _log(self, etiqueta: str, color: str, chat_id: int | None, texto: str) -> None:
        origen = f"chat {chat_id} · " if chat_id is not None else ""
        self.consola.log(f"[bold {color}]{etiqueta:<9}[/] {origen}{texto}")

    async def _enviar(self, chat_id: int, texto: str, descripcion: str, responder_a: int | None = None) -> None:
        try:
            m = await self.cliente.enviar_mensaje(chat_id, texto, responder_a)
        except ErrorTelegram as e:
            self._log("ERROR", "red", chat_id, f"no se pudo enviar {descripcion}: {escape(str(e))}")
            return
        self._log("ENVIADO", "green", chat_id, f"{descripcion} (message_id {m['message_id']}, {len(texto)} caracteres)")

    # -- ciclo principal

    async def ejecutar(self) -> None:
        bot = await self.cliente.obtener_bot()
        motor = "solo reglas (sin LLM)" if self.llm is None else getattr(self.llm, "model", type(self.llm).__name__)
        self._log("INICIO", "cyan", None, f"bot @{bot.get('username')} conectado · modelo: {escape(str(motor))}")
        if self.chats_permitidos:
            self._log("INICIO", "cyan", None, f"chats permitidos: {', '.join(map(str, sorted(self.chats_permitidos)))}")
        self._log("INICIO", "cyan", None, "esperando mensajes (Ctrl+C para detener)")
        offset: int | None = None
        while True:
            try:
                actualizaciones = await self.cliente.obtener_actualizaciones(offset)
            except ErrorTelegram as e:
                self._log("ERROR", "red", None, f"{escape(str(e))}; se reintenta en 5 s")
                await asyncio.sleep(5)
                continue
            for actualizacion in actualizaciones:
                offset = actualizacion["update_id"] + 1
                self.atender(actualizacion)

    def atender(self, actualizacion: dict) -> asyncio.Task | None:
        mensaje = actualizacion.get("message")
        if not mensaje:
            return None
        chat_id = mensaje["chat"]["id"]
        remitente = mensaje.get("from") or {}
        quien = remitente.get("username") and f"@{remitente['username']}" or remitente.get("first_name", "?")
        texto = (mensaje.get("text") or "").strip()
        self._log("RECIBIDO", "magenta", chat_id, f"{escape(quien)}: {escape(texto) if texto else '(sin texto)'}")
        tarea = asyncio.create_task(self._procesar(chat_id, mensaje["message_id"], texto))
        self._tareas.add(tarea)
        tarea.add_done_callback(self._tareas.discard)
        return tarea

    async def esperar_tareas(self) -> None:
        await asyncio.gather(*self._tareas)

    async def _procesar(self, chat_id: int, mensaje_id: int, texto: str) -> None:
        if self.chats_permitidos and chat_id not in self.chats_permitidos:
            self._log("RECHAZADO", "yellow", chat_id, "chat fuera de TELEGRAM_CHATS_PERMITIDOS")
            await self._enviar(chat_id, "Este bot es privado y no atiende este chat.", "aviso de chat no permitido")
            return
        conversacion = self._conversaciones.setdefault(chat_id, Conversacion())
        comando = texto.split()[0].split("@")[0].lower() if texto.startswith("/") else ""
        if comando in ("/start", "/ayuda", "/help") or not texto:
            await self._enviar(chat_id, AYUDA, "ayuda")
            return
        if comando == "/cancelar":
            conversacion.hilo_pendiente = None
            await self._enviar(chat_id, "Solicitud descartada. Puede enviar una nueva lista.", "confirmación de cancelación")
            return
        if conversacion.candado.locked():
            self._log("EN COLA", "yellow", chat_id, "hay una solicitud en proceso para este chat")
            await self._enviar(chat_id, "Su solicitud anterior sigue en proceso; esta se atenderá a continuación.",
                               "aviso de cola", mensaje_id)
        async with conversacion.candado:
            try:
                await self._ejecutar_grafo(chat_id, mensaje_id, texto, conversacion)
            except Exception as e:
                self._log("ERROR", "red", chat_id, f"{type(e).__name__}: {escape(str(e))}")
                self.consola.print_exception(max_frames=3)
                await self._enviar(chat_id, "No fue posible preparar el presupuesto por un error interno. "
                                            "Intente de nuevo en unos minutos.", "aviso de error", mensaje_id)

    async def _ejecutar_grafo(self, chat_id: int, mensaje_id: int, texto: str, conversacion: Conversacion) -> None:
        if conversacion.hilo_pendiente:
            hilo, entrada = conversacion.hilo_pendiente, Command(resume=texto)
            conversacion.hilo_pendiente = None
            aclarada = True
            self._log("PROCESO", "blue", chat_id, f"se reanuda el hilo {hilo[:8]} con la aclaración")
            await self._enviar(chat_id, "Gracias por la aclaración. Se actualiza la solicitud con esos datos.",
                               "acuse de la aclaración", mensaje_id)
        else:
            hilo, entrada = str(uuid.uuid4()), {"texto": texto, "opciones": self.opciones}
            aclarada = False
            self._log("PROCESO", "blue", chat_id, f"nuevo hilo {hilo[:8]}")
            await self._enviar(chat_id, "Recibido. Se está revisando su lista.", "acuse de recibo", mensaje_id)
        config = {"configurable": {"thread_id": hilo, "navegador": self.navegador,
                                   "llm": self.llm, "sin_llm": self.llm is None}}

        if self._cupo.locked():
            self._log("EN COLA", "yellow", chat_id, "se espera a que termine otra consulta")
        async with self._cupo:
            escribiendo = asyncio.create_task(self._mantener_escribiendo(chat_id))
            inicio = time.monotonic()
            try:
                async for evento in self.grafo.astream(entrada, config, stream_mode="updates"):
                    for nodo, cambios in evento.items():
                        if nodo != "__interrupt__":
                            self._log("PROCESO", "blue", chat_id,
                                      f"{nodo} ({time.monotonic() - inicio:.1f} s){self._detalle(nodo, cambios)}")
                        if nodo == "planificar":
                            await self._enviar(chat_id, plan_html(cambios["solicitud"], cambios["nombres"], aclarada,
                                                         cambios.get("presentar")),
                                               "productos que se buscarán", mensaje_id)
            finally:
                escribiendo.cancel()

        estado = await self.grafo.aget_state(config)
        if estado.interrupts:
            conversacion.hilo_pendiente = hilo
            preguntas = estado.interrupts[0].value["preguntas"]
            self._log("PROCESO", "blue", chat_id, f"grafo en pausa: faltan {len(preguntas)} dato(s)")
            await self._enviar(chat_id, preguntas_html(preguntas), "preguntas de aclaración", mensaje_id)
            return

        final = estado.values
        ruta_md, ruta_json = guardar_resultados(final, self.salida, sufijo=f"-{chat_id}")
        self._log("PROCESO", "blue", chat_id, f"informe guardado en {ruta_md} y {ruta_json.name}")
        await self._enviar(chat_id, resumen_html(final), "resumen del presupuesto", mensaje_id)
        try:
            m = await self.cliente.enviar_documento(chat_id, ruta_md.name, ruta_md.read_bytes(),
                                                    "Informe completo con tablas, enlaces y fuentes")
            self._log("ENVIADO", "green", chat_id, f"informe {ruta_md.name} (message_id {m['message_id']})")
        except ErrorTelegram as e:
            self._log("ERROR", "red", chat_id, f"no se pudo enviar el informe: {escape(str(e))}")

    @staticmethod
    def _detalle(nodo: str, cambios: Any) -> str:
        if not isinstance(cambios, dict):
            return ""
        if nodo == "interpretar" and cambios.get("solicitud"):
            s = cambios["solicitud"]
            return f": {len(s.items)} producto(s), ciudad {escape(s.ciudad or 'sin indicar')}"
        if nodo == "planificar" and cambios.get("proveedores"):
            return f": {', '.join(cambios['proveedores'])}"
        if nodo == "consultar_proveedor" and cambios.get("resultados"):
            resultados = cambios["resultados"]
            return f": {resultados[0].proveedor}, {sum(len(r.ofertas) for r in resultados)} ofertas"
        if nodo == "presupuestar" and cambios.get("decision"):
            return f": opción elegida {escape(str(cambios['decision'].get('opcion')))}"
        return ""

    async def _mantener_escribiendo(self, chat_id: int) -> None:
        """Muestra "escribiendo…" en el chat mientras dura la consulta (Telegram lo apaga a los 5 s)."""
        while True:
            try:
                await self.cliente.indicar_escribiendo(chat_id)
            except ErrorTelegram:
                pass
            await asyncio.sleep(4.5)


# ------------------------------------------------------------------ arranque


def _args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="presupuesto-mercado-telegram",
        description="Atiende solicitudes de presupuesto de mercado por un bot de Telegram (long polling).",
    )
    p.add_argument("--aceptar-sustitutos", action="store_true", help="Usar sustitutos cuando no haya equivalentes")
    p.add_argument("--sin-llm", action="store_true", help="Usar solo reglas (sin modelo de lenguaje)")
    p.add_argument("--modelo", help=f"Modelo de LangChain (por defecto {MODELO_POR_DEFECTO})")
    p.add_argument("--sin-cache", action="store_true", help="Ignorar la caché de páginas")
    p.add_argument("--simultaneas", type=int, default=2, help="Consultas a supermercados en paralelo (por defecto 2)")
    p.add_argument("--salida", default="salidas/telegram", help="Carpeta del informe y la evidencia de cada respuesta")
    return p.parse_args(argv)


def _chats_permitidos(valor: str | None) -> set[int] | None:
    ids = {int(c) for c in re.split(r"[,\s]+", valor or "") if c.strip()}
    return ids or None


async def servir(args: argparse.Namespace, consola: Console) -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        consola.print("[red]Falta TELEGRAM_BOT_TOKEN (créelo con @BotFather y agréguelo a .env).[/]")
        return 2
    llm = None if args.sin_llm else crear_llm(args.modelo)
    cliente = ClienteTelegram(token, os.getenv("TELEGRAM_API_URL") or "https://api.telegram.org")
    try:
        async with Navegador(Path(".cache"), usar_cache=not args.sin_cache) as nav:
            servidor = ServidorTelegram(
                cliente, nav, llm=llm, opciones={"aceptar_sustitutos": args.aceptar_sustitutos},
                salida=Path(args.salida), chats_permitidos=_chats_permitidos(os.getenv("TELEGRAM_CHATS_PERMITIDOS")),
                simultaneas=args.simultaneas, consola=consola,
            )
            await servidor.ejecutar()
    except ErrorTelegram as e:
        consola.print(f"[red]Telegram rechazó la conexión: {escape(str(e))}[/]")
        return 1
    finally:
        await cliente.cerrar()
    return 0


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    consola = Console(log_path=False)
    try:
        sys.exit(asyncio.run(servir(_args(argv), consola)))
    except KeyboardInterrupt:
        consola.log("[bold cyan]FIN      [/] servidor detenido")
