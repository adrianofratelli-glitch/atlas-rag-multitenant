"""Destino e credencial do LLM: sempre um gateway explícito, nunca um destino implícito.

Ordem:
  1. `GROVE_BASE_URL` definido e `pov-shared` instalado -> `grove_client.client_settings()`
     (destino validado por domínio + chave `GROVE_API_KEY`, `Authorization: Bearer` e `x-api-key`).
  2. Senão, `ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY`, ambos obrigatórios. Sem base URL
     explícita o processo recusa a chamada em vez de cair em silêncio no endpoint público
     do provedor (o SDK faria isso sozinho com `base_url=None`).
"""
import os


class GatewayNotConfigured(RuntimeError):
    """Nenhum gateway de LLM configurado explicitamente."""


def gateway_settings() -> dict:
    """{"base_url", "api_key", "headers"} para montar o cliente do LLM."""
    if os.getenv("GROVE_BASE_URL"):
        try:
            from grove_client import client_settings
        except ImportError:
            client_settings = None
        if client_settings is not None:
            cfg = client_settings()
            return {"base_url": cfg["base_url"], "api_key": cfg["api_key"], "headers": dict(cfg["headers"])}
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    key = os.getenv("ANTHROPIC_API_KEY")
    if not base_url:
        raise GatewayNotConfigured(
            "Defina GROVE_BASE_URL (com o pov-shared instalado) ou ANTHROPIC_BASE_URL apontando "
            "para o gateway. Não há destino padrão implícito."
        )
    if not key:
        raise GatewayNotConfigured("ANTHROPIC_API_KEY não definida para o gateway configurado.")
    return {"base_url": base_url, "api_key": key, "headers": {"Authorization": f"Bearer {key}"}}
