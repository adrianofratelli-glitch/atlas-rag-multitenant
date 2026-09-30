"""Retry/backoff para erros transitórios (429, 5xx, timeout, conexão).

Tudo aqui é opt-in: com attempts <= 1 (default dos flags) o comportamento é o de antes.
"""
import logging
import random
import time
from typing import Callable, Iterable, Iterator, TypeVar

logger = logging.getLogger("rag_poc.resilience")
T = TypeVar("T")

_TRANSIENT_NAMES = ("timeout", "connection", "ratelimit", "serviceunavailable", "internalserver",
                    "overloaded", "apierror", "servererror", "trylater")


def is_transient(exc: BaseException) -> bool:
    """429, 5xx, timeout e falha de conexão. 4xx (auth, validação) nunca é retentado."""
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return any(n in type(exc).__name__.lower() for n in _TRANSIENT_NAMES)


def _sleep_for(attempt: int, base: float, cap: float) -> float:
    return random.uniform(0, min(cap, base * (2 ** attempt)))  # full jitter


def retry_call(fn: Callable[[], T], *, attempts: int, base: float = 0.5, cap: float = 8.0,
               label: str = "call", sleep: Callable[[float], None] | None = None) -> T:
    attempts = max(1, attempts)
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if i == attempts - 1 or not is_transient(exc):
                raise
            delay = _sleep_for(i, base, cap)
            logger.warning("%s transient failure (%s), retry %d/%d in %.2fs",
                           label, type(exc).__name__, i + 1, attempts - 1, delay)
            (sleep or time.sleep)(delay)
    raise AssertionError("unreachable")


def stream_with_retry(make_stream: Callable[[], Iterable], *, attempts: int, base: float = 0.5,
                      cap: float = 8.0, sleep: Callable[[float], None] | None = None) -> Iterator:
    """Retenta só ANTES do primeiro chunk: depois disso o cliente já recebeu tokens e repetir
    duplicaria a resposta. (`llm.with_retry().stream()` não serve: RunnableBinding delega o
    stream direto ao modelo e o retry nunca dispara.)"""
    attempts = max(1, attempts)
    for i in range(attempts):
        started = False
        try:
            for chunk in make_stream():
                started = True
                yield chunk
            return
        except Exception as exc:
            if started or i == attempts - 1 or not is_transient(exc):
                raise
            delay = _sleep_for(i, base, cap)
            logger.warning("llm stream failed before first token (%s), retry %d/%d in %.2fs",
                           type(exc).__name__, i + 1, attempts - 1, delay)
            (sleep or time.sleep)(delay)


def friendly_error(exc: BaseException) -> str:
    """Mensagem para o evento SSE `error`, sem vazar detalhe interno além do tipo."""
    name = type(exc).__name__.lower()
    if "timeout" in name or isinstance(exc, TimeoutError):
        return "A geração da resposta demorou mais que o esperado e foi interrompida. Tente novamente."
    if "ratelimit" in name or getattr(exc, "status_code", None) == 429:
        return "O provedor de IA está com limite de uso temporário. Aguarde alguns segundos e tente novamente."
    return ("Ocorreu um erro ao consultar o Atlas ou gerar a resposta. "
            f"Tente novamente em instantes. (detalhe técnico: {type(exc).__name__})")
