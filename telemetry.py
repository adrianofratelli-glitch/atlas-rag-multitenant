"""Tracing opt-in por span de etapa do RAG, sobre o `tracing` do _shared (pov-shared).

Ligado só com TRACE_SINK != off. Quando ligado, TRACE_MASK_PII=1 é FORÇADO (não dá para
rodar com tracing ativo e PII crua). Atributos são sempre metadados: números, ids de
tenant do próprio processo e nomes de etapa — nunca pergunta, contexto ou resposta.
"""
import contextlib
import logging
import os

logger = logging.getLogger("rag_poc.telemetry")
_tracer = None


def init(service_name: str | None = None) -> str:
    """Chamar depois do load_dotenv e antes de criar clients. Devolve o sink ativo."""
    global _tracer
    if os.getenv("TRACE_SINK", "off").lower() == "off":
        return "off"
    # Nome do serviço sem identidade de tenant por padrão; OTEL_SERVICE_NAME sobrescreve.
    service_name = service_name or os.getenv("OTEL_SERVICE_NAME", "rag-multitenant")
    os.environ["TRACE_MASK_PII"] = "1"
    try:
        from tracing import init_tracing
        sink = init_tracing(service_name)
        if sink != "off":
            from opentelemetry import trace
            _tracer = trace.get_tracer("rag_poc")
        return sink
    except Exception:
        logger.exception("tracing init failed — continuing without tracing")
        _tracer = None
        return "off"


def enabled() -> bool:
    return _tracer is not None


class _NoSpan:
    def set_attribute(self, *_a, **_k): ...
    def set_attributes(self, *_a, **_k): ...


def _clean(attrs: dict) -> dict:
    out = {}
    for k, v in attrs.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            v = [x for x in v if isinstance(x, (int, float, bool, str))]
            if not v:
                continue
        elif not isinstance(v, (int, float, bool, str)):
            v = str(v)
        out[k] = v
    return out


@contextlib.contextmanager
def span(name: str, **attrs):
    """Span de etapa; no-op sem custo quando o tracing está desligado."""
    if _tracer is None:
        yield _NoSpan()
        return
    with _tracer.start_as_current_span(name) as sp:
        sp.set_attributes(_clean(attrs))
        yield sp


def annotate(sp, **attrs):
    if sp is not None:
        sp.set_attributes(_clean(attrs))
