"""Creación del modelo de lenguaje.

Por defecto se usa Claude a través de `langchain-anthropic`. Se puede cambiar con
la variable PM_MODELO usando la notación de `init_chat_model` de LangChain
(p. ej. "openai:gpt-4.1-mini"), siempre que el paquete del proveedor esté instalado.
Si no hay credenciales, el sistema funciona con reglas heurísticas y lo advierte.
"""

from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel

MODELO_POR_DEFECTO = "anthropic:claude-sonnet-5-5"


def crear_llm(modelo: str | None = None) -> BaseChatModel | None:
    if os.getenv("PM_SIN_LLM"):
        return None
    modelo = modelo or os.getenv("PM_MODELO") or MODELO_POR_DEFECTO
    proveedor, _, nombre = modelo.partition(":")
    if not nombre:
        proveedor, nombre = "anthropic", proveedor
    if proveedor == "anthropic":
        from langchain_anthropic import ChatAnthropic

        api_key = os.getenv("ANTHROPIC_API_KEY")
        token = os.getenv("ANTHROPIC_AUTH_TOKEN")
        if not api_key and not token:
            return None
        kwargs: dict = {}
        if os.getenv("ANTHROPIC_BASE_URL"):
            kwargs["base_url"] = os.environ["ANTHROPIC_BASE_URL"]
        if token and not api_key:
            # Pasarelas que autentican con token Bearer en lugar de x-api-key
            kwargs["default_headers"] = {"Authorization": f"Bearer {token}"}
            api_key = "no-usado"
        # Los modelos Claude 5 no admiten temperature distinta de la predeterminada
        return ChatAnthropic(model=nombre, api_key=api_key, max_tokens=8000, timeout=120, **kwargs)

    from langchain.chat_models import init_chat_model

    return init_chat_model(nombre, model_provider=proveedor, temperature=0)
