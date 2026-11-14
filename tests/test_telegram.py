"""Adaptador de Telegram con la Bot API simulada (httpx.MockTransport), sin red ni LLM."""

import json

import httpx
import pytest
from rich.console import Console

from presupuesto_mercado.telegram import (
    ClienteTelegram, ErrorTelegram, ServidorTelegram, html_desde_markdown, recortar,
)

TOKEN = "123456:SECRETO-DE-PRUEBA"


class BotApiFalsa:
    """Registra las llamadas a la Bot API y responde como Telegram."""

    def __init__(self):
        self.llamadas: list[tuple[str, dict]] = []
        self._siguiente = 100

    def __call__(self, request: httpx.Request) -> httpx.Response:
        metodo = request.url.path.rsplit("/", 1)[-1]
        if request.headers.get("content-type", "").startswith("application/json"):
            datos = json.loads(request.content or b"{}")
        else:
            datos = {"multipart": request.content}
        self.llamadas.append((metodo, datos))
        if metodo == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "username": "bot_prueba"}})
        if metodo in ("sendMessage", "sendDocument"):
            self._siguiente += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self._siguiente}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def enviados(self, metodo: str = "sendMessage") -> list[dict]:
        return [d for m, d in self.llamadas if m == metodo]


def mensaje(texto: str, chat_id: int = 42, update_id: int = 1) -> dict:
    return {"update_id": update_id, "message": {
        "message_id": update_id, "chat": {"id": chat_id, "type": "private"},
        "from": {"id": chat_id, "first_name": "Ana", "username": "ana"}, "text": texto,
    }}


@pytest.fixture
def api():
    return BotApiFalsa()


@pytest.fixture
def servidor(api, tmp_path, falsos):
    cliente = ClienteTelegram(TOKEN, "https://bot.test", transport=httpx.MockTransport(api))
    consola = Console(record=True, width=200, log_path=False)
    return ServidorTelegram(cliente, object(), salida=tmp_path, consola=consola)


async def test_solicitud_completa_responde_resumen_e_informe(servidor, api, tmp_path):
    await servidor.atender(mensaje("Necesito 5 kg de arroz y 2 litros de aceite en Medellín"))

    textos = [d["text"] for d in api.enviados()]
    assert textos[0] == "Recibido. Se está revisando su lista."
    plan = textos[1]
    assert plan.startswith("Se buscará:") and "• 5 kg de arroz" in plan
    assert "<b>Ciudad:</b> Medellín" in plan and "<b>Supermercados:</b> EXITO, D1" in plan
    resumen = textos[2]
    assert "<b>Presupuesto de mercado</b> · Medellín" in resumen
    assert "Compra completa en EXITO" in resumen and "total final no confirmado" in resumen
    assert all(d["reply_parameters"]["message_id"] == 1 for d in api.enviados())
    documentos = api.enviados("sendDocument")
    assert len(documentos) == 1 and b"# Presupuesto" in documentos[0]["multipart"]
    assert len(list(tmp_path.glob("presupuesto-*-42.md"))) == 1
    assert len(list(tmp_path.glob("evidencia-*-42.json"))) == 1

    consola = servidor.consola.export_text()
    assert "RECIBIDO  chat 42 · @ana: Necesito 5 kg" in consola
    assert "PROCESO   chat 42 · consultar_proveedor" in consola
    assert "ENVIADO   chat 42 · resumen del presupuesto" in consola
    assert "ENVIADO   chat 42 · informe presupuesto-" in consola


async def test_falta_un_dato_pregunta_y_reanuda_con_la_respuesta(servidor, api):
    await servidor.atender(mensaje("Necesito 5 kg de arroz", update_id=1))
    textos = [d["text"] for d in api.enviados()]
    assert textos[0] == "Recibido. Se está revisando su lista."
    assert textos[1].startswith("Para preparar el presupuesto faltan datos")
    assert len(textos) == 2 and not api.enviados("sendDocument")

    await servidor.atender(mensaje("En Cali", update_id=2))
    acuse, plan, resumen = [d["text"] for d in api.enviados()][2:]
    assert acuse.startswith("Gracias por la aclaración")
    assert plan.startswith("Entendido, con su aclaración se buscará:") and "<b>Ciudad:</b> Cali" in plan
    assert "<b>Presupuesto de mercado</b> · Cali" in resumen
    assert all(d["reply_parameters"]["message_id"] == 2 for d in api.enviados()[2:])
    assert len(api.enviados("sendDocument")) == 1
    consola = servidor.consola.export_text()
    assert "se reanuda el hilo" in consola and "ENVIADO   chat 42 · acuse de la aclaración" in consola
    assert "ENVIADO   chat 42 · productos que se buscarán" in consola


async def test_cancelar_descarta_la_pregunta_pendiente(servidor, api):
    await servidor.atender(mensaje("Necesito 5 kg de arroz", update_id=1))
    await servidor.atender(mensaje("/cancelar", update_id=2))
    assert api.enviados()[-1]["text"].startswith("Solicitud descartada")
    await servidor.atender(mensaje("Necesito 2 litros de aceite en Pasto", update_id=3))
    assert "· Pasto" in api.enviados()[-1]["text"]


async def test_chat_no_permitido_no_ejecuta_el_grafo(servidor, api):
    servidor.chats_permitidos = {7}
    await servidor.atender(mensaje("Necesito 5 kg de arroz en Medellín", chat_id=42))
    assert [d["text"] for d in api.enviados()] == ["Este bot es privado y no atiende este chat."]
    assert "RECHAZADO" in servidor.consola.export_text()


async def test_ayuda_con_start(servidor, api):
    await servidor.atender(mensaje("/start"))
    assert "Envíe su lista de mercado" in api.enviados()[0]["text"]


async def test_errores_de_la_api_no_exponen_el_token():
    def falla(request):
        raise httpx.ConnectError(f"no se pudo conectar a {request.url}")

    cliente = ClienteTelegram(TOKEN, "https://bot.test", transport=httpx.MockTransport(falla))
    with pytest.raises(ErrorTelegram) as error:
        await cliente.obtener_bot()
    assert "SECRETO" not in str(error.value) and error.value.__cause__ is None

    rechazo = ClienteTelegram(TOKEN, "https://bot.test", transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"ok": False, "description": "Unauthorized"})))
    with pytest.raises(ErrorTelegram, match="getMe: 401 Unauthorized"):
        await rechazo.obtener_bot()


async def test_resumen_separa_consultas_fallidas(api, tmp_path, tres_tiendas):
    cliente = ClienteTelegram(TOKEN, "https://bot.test", transport=httpx.MockTransport(api))
    servidor = ServidorTelegram(cliente, object(), salida=tmp_path, consola=Console(record=True, width=200))
    await servidor.atender(mensaje("Necesito 5 kg de arroz y 2 litros de aceite en Medellín"))
    plan, resumen = [d["text"] for d in api.enviados()][1:3]
    assert "EXITO, D1, JUMBO" in plan
    assert "EXITO" not in resumen.split("<b>No se pudo consultar</b>")[0]
    assert "<b>No se pudo consultar</b> (falla del sitio o de la red, no falta de producto)\n• EXITO: arroz, aceite" in resumen
    assert "Se recomienda la opción <b>Compra completa en JUMBO</b>" in resumen


def test_formato_html():
    assert html_desde_markdown("Se recomienda **D1 & Co** <x>\n- motivo") == \
        "Se recomienda <b>D1 &amp; Co</b> &lt;x&gt;\n• motivo"
    largo = "\n".join(["linea"] * 2000)
    assert len(recortar(largo)) <= 4096
