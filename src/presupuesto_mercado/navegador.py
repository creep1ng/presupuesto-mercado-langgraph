"""Acceso a la web: robots.txt, caché, cortesía con los servidores y Playwright.

Reglas que se aplican a todas las consultas:
- Antes de abrir una URL se verifica robots.txt del sitio; si la ruta está
  prohibida, la consulta no se hace y se registra el motivo.
- Si el sitio responde con un bloqueo (403, 429, CAPTCHA o reto anti-bots), se
  detiene la consulta a ese sitio durante un tiempo. No se intenta eludir ninguna
  protección: no se resuelven retos ni se ocultan las huellas del navegador.
- Entre dos solicitudes al mismo dominio se espera un intervalo mínimo.
- Las fallas transitorias de red (tiempo agotado, conexión reiniciada) se
  reintentan pocas veces con espera creciente. Un bloqueo nunca se reintenta.
- Si varias consultas piden la misma página a la vez, se descarga una sola vez.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx
from playwright.async_api import Browser, Page, Playwright, Response, async_playwright
from playwright.async_api import Error as ErrorPlaywright
from playwright.async_api import TimeoutError as TiempoAgotado

from .robots import ReglasRobots, interpretar_robots

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36 PresupuestoMercado/0.1 (proyecto academico)"
)
_MARCAS_BLOQUEO = ("just a moment", "captcha", "access denied", "attention required", "acceso denegado")


_INTENTOS = 3
_BLOQUEO_VIGENTE = timedelta(minutes=30)


def es_falla_transitoria(error: BaseException) -> bool:
    """Tiempo agotado o error de red de Chromium (net::ERR_…): vale la pena reintentar."""
    return isinstance(error, TiempoAgotado) or (isinstance(error, ErrorPlaywright) and "net::ERR_" in str(error))


class ConsultaNoPermitida(Exception):
    """robots.txt prohíbe la ruta solicitada."""


class BloqueoDetectado(Exception):
    """El sitio respondió con un mecanismo de protección; no se insiste."""


class Navegador:
    def __init__(
        self,
        cache_dir: Path,
        ttl: timedelta = timedelta(hours=6),
        concurrencia: int = 3,
        intervalo_dominio: float = 1.5,
        usar_cache: bool = True,
    ) -> None:
        self.cache_dir = cache_dir
        self.ttl = ttl
        self.usar_cache = usar_cache
        self.intervalo_dominio = intervalo_dominio
        self._sem = asyncio.Semaphore(concurrencia)
        self._robots: dict[str, ReglasRobots] = {}
        self._bloqueados: dict[str, float] = {}  # dominio -> instante (monotonic) del bloqueo
        self._en_curso: dict[str, asyncio.Future] = {}
        self._ultimo_acceso: dict[str, float] = {}
        self._candados: dict[str, asyncio.Lock] = {}
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lanzando = asyncio.Lock()
        self._http = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "es-CO,es;q=0.9"},
            follow_redirects=True,
            timeout=30,
        )

    async def __aenter__(self) -> "Navegador":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.cerrar()

    async def cerrar(self) -> None:
        await self._http.aclose()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    # ------------------------------------------------------------------ caché

    def _ruta_cache(self, tipo: str, clave: str) -> Path:
        h = hashlib.sha1(clave.encode()).hexdigest()[:20]
        return self.cache_dir / tipo / f"{h}.json"

    def _leer_cache(self, tipo: str, clave: str, ttl: timedelta | None = None) -> dict[str, Any] | None:
        if not self.usar_cache:
            return None
        ruta = self._ruta_cache(tipo, clave)
        if not ruta.exists():
            return None
        datos = json.loads(ruta.read_text())
        if datetime.fromisoformat(datos["fecha"]) < datetime.now().astimezone() - (ttl or self.ttl):
            return None
        return datos

    def _escribir_cache(self, tipo: str, clave: str, datos: Any) -> dict[str, Any]:
        registro = {"clave": clave, "fecha": datetime.now().astimezone().isoformat(), "datos": datos}
        ruta = self._ruta_cache(tipo, clave)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(registro, ensure_ascii=False))
        return registro

    # ---------------------------------------------------------------- robots

    async def _reglas(self, origen: str) -> ReglasRobots:
        if origen not in self._robots:
            cache = self._leer_cache("robots", origen, timedelta(days=1))
            if cache is None:
                try:
                    r = await self._http.get(f"{origen}/robots.txt")
                    texto = r.text if r.status_code == 200 else ""
                except httpx.HTTPError:
                    texto = ""
                cache = self._escribir_cache("robots", origen, texto)
            self._robots[origen] = interpretar_robots(cache["datos"])
        return self._robots[origen]

    async def verificar_permitido(self, url: str) -> None:
        partes = urlsplit(url)
        origen = f"{partes.scheme}://{partes.netloc}"
        ruta = partes.path + (f"?{partes.query}" if partes.query else "")
        if not (await self._reglas(origen)).permite(ruta or "/"):
            raise ConsultaNoPermitida(f"robots.txt de {partes.netloc} no permite {ruta}")
        bloqueo = self._bloqueados.get(partes.netloc)
        if bloqueo is not None and time.monotonic() - bloqueo < _BLOQUEO_VIGENTE.total_seconds():
            raise BloqueoDetectado(f"{partes.netloc} bloqueó una consulta hace menos de "
                                   f"{_BLOQUEO_VIGENTE.seconds // 60} minutos; no se insiste")

    async def _cortesia(self, dominio: str) -> None:
        candado = self._candados.setdefault(dominio, asyncio.Lock())
        async with candado:
            espera = self._ultimo_acceso.get(dominio, 0) + self.intervalo_dominio - time.monotonic()
            if espera > 0:
                await asyncio.sleep(espera)
            self._ultimo_acceso[dominio] = time.monotonic()

    async def _una_vez(self, clave: str, descarga: Callable[[], Awaitable[Any]]) -> Any:
        """Si la misma descarga ya está en curso, espera su resultado en lugar de repetirla."""
        futuro = self._en_curso.get(clave)
        if futuro is None:
            futuro = asyncio.ensure_future(descarga())
            self._en_curso[clave] = futuro
            futuro.add_done_callback(lambda _: self._en_curso.pop(clave, None))
        return await asyncio.shield(futuro)

    # ------------------------------------------------------------ descargas

    async def obtener_texto(self, url: str, ttl: timedelta | None = None) -> tuple[str, datetime]:
        """Descarga un recurso estático (sitemaps XML) con el navegador, respetando robots.txt.

        Se usa Chromium y no un cliente HTTP porque algunos sitios (Éxito) rechazan con 403 a
        los clientes que no son navegadores, aunque la ruta esté permitida en robots.txt.
        """
        async def leer(_: Page, respuesta: Response | None) -> str:
            if respuesta is None or not respuesta.ok:
                raise RuntimeError(f"{url} respondió {respuesta.status if respuesta else 'sin respuesta'}")
            return await respuesta.text()

        async def descargar() -> dict[str, Any]:
            cache = self._leer_cache("http", url, ttl)
            if cache is None:
                cache = self._escribir_cache("http", url, await self._en_pagina(url, leer))
            return cache

        cache = await self._una_vez(f"http|{url}", descargar)
        return cache["datos"], datetime.fromisoformat(cache["fecha"])

    async def _browser_listo(self) -> Browser:
        async with self._lanzando:
            if self._browser is None:
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(headless=True)
        return self._browser

    def _bloquear(self, dominio: str, motivo: str) -> BloqueoDetectado:
        self._bloqueados[dominio] = time.monotonic()
        return BloqueoDetectado(motivo)

    async def _en_pagina(self, url: str, accion: Callable[[Page, Response | None], Awaitable[Any]]) -> Any:
        """Abre la URL en una pestaña nueva, detecta bloqueos y ejecuta `accion`.

        Reintenta las fallas transitorias de red; los bloqueos y las prohibiciones no.
        """
        dominio = urlsplit(url).netloc
        for intento in range(1, _INTENTOS + 1):
            await self.verificar_permitido(url)
            async with self._sem:
                await self._cortesia(dominio)
                browser = await self._browser_listo()
                contexto = await browser.new_context(
                    user_agent=USER_AGENT, locale="es-CO", viewport={"width": 1366, "height": 900}
                )
                pagina = await contexto.new_page()
                try:
                    respuesta = await pagina.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    if respuesta is not None and respuesta.status in (403, 429):
                        raise self._bloquear(dominio, f"{url} respondió {respuesta.status}")
                    titulo = (await pagina.title()).lower()
                    if any(m in titulo for m in _MARCAS_BLOQUEO):
                        raise self._bloquear(dominio, f"{url} mostró una página de verificación ('{titulo}')")
                    return await accion(pagina, respuesta)
                except ErrorPlaywright as e:
                    if intento == _INTENTOS or not es_falla_transitoria(e):
                        raise
                finally:
                    await contexto.close()
            await asyncio.sleep(3 * intento)  # fuera del semáforo, para no retener el cupo

    async def renderizar(
        self,
        url: str,
        extractor_js: str,
        esperar_selector: str | None = None,
        desplazamientos: int = 0,
        nombre_extractor: str = "",
    ) -> tuple[Any, datetime]:
        """Abre la URL en Chromium (Playwright), espera el render y ejecuta `extractor_js`.

        Devuelve el resultado del extractor y la fecha real de la consulta (si viene
        de caché, la fecha original, no la de lectura).
        """
        clave = f"{nombre_extractor}|{url}"

        async def extraer(pagina: Page, respuesta: Response | None) -> Any:
            if respuesta is not None and respuesta.status == 404:
                return []
            if esperar_selector:
                try:
                    await pagina.wait_for_selector(esperar_selector, timeout=25_000)
                except TiempoAgotado:
                    pass  # página sin resultados; el extractor devolverá lista vacía
            for _ in range(desplazamientos):
                await pagina.mouse.wheel(0, 2500)
                await pagina.wait_for_timeout(900)
            await pagina.wait_for_timeout(1200)
            return await pagina.evaluate(extractor_js)

        async def descargar() -> dict[str, Any]:
            cache = self._leer_cache("paginas", clave)
            if cache is None:
                cache = self._escribir_cache("paginas", clave, await self._en_pagina(url, extraer))
            return cache

        cache = await self._una_vez(f"paginas|{clave}", descargar)
        return cache["datos"], datetime.fromisoformat(cache["fecha"])
