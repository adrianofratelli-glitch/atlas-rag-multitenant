"""O turno de chat (recuperação híbrida -> geração) como um StateGraph do LangGraph.

Antes era uma função geradora síncrona só em `backend/api.py`: chama `retrieve_context()`,
decide recusa, monta as mensagens e faz streaming do Claude. Isto reorganiza os mesmos dois
passos em nós de grafo (`retrieve` / `generate`), com checkpoint em MongoDB quando `MONGODB_URI`
está configurada (`MemorySaver` senão — mesma lógica das outras PoVs deste workspace). O SSE
continua emitido de dentro dos nós via `emit()`, síncrono, porque o endpoint em si é uma função
geradora síncrona (streaming de tokens do driver Anthropic, não asyncio).
"""

from __future__ import annotations

import os
import time
from uuid import uuid4
from typing import Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.graph import END, StateGraph
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pymongo import MongoClient as SyncMongoClient

import telemetry
from agent import insufficient_evidence, retrieve_context
from resilience import stream_with_retry


class ChatState(TypedDict, total=False):
    question: str
    access_level: str
    sources: Optional[list]
    history: list
    scope_title: str
    static_instructions: str
    outline: Optional[str]
    context: str
    source_cards: list
    stats: dict
    elapsed_ms: int
    refused: bool
    full_response: str
    timed_out: bool
    ttft_ms: Optional[int]
    usage: dict


def _ctx(config):
    return config["configurable"]


def n_retrieve(state: ChatState, config) -> dict:
    ctx = _ctx(config)
    t0 = time.perf_counter()
    context, sources, stats = retrieve_context(
        state["question"], access_levels=ctx["access_levels"], sources=state.get("sources"))
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    refused = insufficient_evidence(sources, stats)
    if refused:
        stats = {**stats, "mode": "refused_no_evidence"}
    ctx["emit"]({"type": "meta", "stats": stats, "sources": sources, "elapsed_ms": elapsed_ms,
                 "followups": ctx["followups"]})
    return {"context": context, "source_cards": sources, "stats": stats,
            "elapsed_ms": elapsed_ms, "refused": refused}


def _route_after_retrieve(state: ChatState) -> str:
    return "refuse" if state["refused"] else "generate"


def n_refuse(state: ChatState, config) -> dict:
    ctx = _ctx(config)
    ctx["metrics"].bump("refusals_no_evidence", 1)
    ctx["emit"]({"type": "token", "delta": ctx["refusal_message"]})
    ctx["emit"]({"type": "done"})
    return {"full_response": ctx["refusal_message"]}


def n_generate(state: ChatState, config) -> dict:
    ctx = _ctx(config)
    lc_messages = [
        SystemMessage(content=[
            {"type": "text", "text": state["static_instructions"], "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"CONTEXTO:\n{state['context']}"},
        ]),
        *[(HumanMessage if m.get("role") == "user" else AIMessage)(content=m.get("content", ""))
          for m in state["history"]],
        HumanMessage(content=state["question"]),
    ]
    full, chunk_agg, ttft_ms, timed_out = "", None, None, False
    t_gen = time.perf_counter()
    with telemetry.span("rag.generate", client_id=ctx["client_id"], model=ctx["model_name"],
                         max_attempts=ctx["max_attempts"]) as gen_span:
        for chunk in stream_with_retry(lambda: ctx["llm"].stream(lc_messages), attempts=ctx["max_attempts"]):
            if chunk.content:
                if ttft_ms is None:
                    ttft_ms = int((time.perf_counter() - t_gen) * 1000)
                full += chunk.content
                ctx["emit"]({"type": "token", "delta": chunk.content})
            chunk_agg = chunk if chunk_agg is None else chunk_agg + chunk
            if ctx["stream_deadline_s"] and time.perf_counter() - t_gen > ctx["stream_deadline_s"]:
                timed_out = True
                break
        usage = getattr(chunk_agg, "usage_metadata", None) or {}
        telemetry.annotate(gen_span, ttft_ms=ttft_ms, input_tokens=usage.get("input_tokens"),
                            output_tokens=usage.get("output_tokens"), timed_out=timed_out)
    # chunk_agg (AIMessageChunk) fica de fora do estado do grafo de propósito: não é
    # serializável pelo checkpointer (msgpack não conhece o tipo), e o checkpoint
    # falhava DEPOIS do nó já ter emitido os tokens pro cliente — o mesmo formato de
    # bug já visto com ObjectId cru na PoV multiagente-atendimento. `track_usage` e a
    # extração de `usage_metadata` acontecem aqui, dentro do nó, e só o dict resultante
    # (serializável) vai para o estado.
    usage = dict(getattr(chunk_agg, "usage_metadata", None) or {})
    if timed_out:
        ctx["emit"]({"type": "error", "message":
                     "A resposta excedeu o tempo máximo de geração e foi interrompida. "
                     "Tente novamente ou refine a pergunta."})
        return {"full_response": full, "timed_out": True, "ttft_ms": ttft_ms, "usage": usage}
    ctx["track_usage"](chunk_agg)
    ctx["emit"]({"type": "done"})
    return {"full_response": full, "timed_out": False, "ttft_ms": ttft_ms, "usage": usage}


def checkpoint_db_name() -> str:
    """Banco dos checkpoints: rag_<CLIENT_ID> (fora de DB_NAME, um por tenant)."""
    return f"rag_{os.getenv('CLIENT_ID') or 'default'}"


def checkpoint_ttl_seconds() -> int | None:
    """TTL dos checkpoints. CHECKPOINT_TTL_DAYS > conversa; 0 desliga."""
    days = float(os.getenv("CHECKPOINT_TTL_DAYS", os.getenv("CONVERSATION_RETENTION_DAYS", "30")))
    return int(days * 86400) if days > 0 else None


def resolve_thread_id(thread_id: str | None) -> str:
    """Turno sem thread_id ganha uma thread própria e descartável.

    Com `None`, o LangGraph gravava todo turno anônimo na MESMA thread (`thread_id: null`):
    estado de pessoas diferentes empilhado num único histórico de checkpoints.
    """
    return thread_id or f"anon-{uuid4().hex}"


_GRAPH = None
_CHECKPOINT_CLIENT: SyncMongoClient | None = None


def _build_graph():
    builder = StateGraph(ChatState)
    builder.add_node("retrieve", n_retrieve)
    builder.add_node("refuse", n_refuse)
    builder.add_node("generate", n_generate)
    builder.set_entry_point("retrieve")
    builder.add_conditional_edges("retrieve", _route_after_retrieve, {"refuse": "refuse", "generate": "generate"})
    builder.add_edge("refuse", END)
    builder.add_edge("generate", END)

    global _CHECKPOINT_CLIENT
    mongo_uri = os.getenv("MONGO_URI", "")
    if mongo_uri:
        _CHECKPOINT_CLIENT = SyncMongoClient(mongo_uri)
        checkpointer = MongoDBSaver(
            _CHECKPOINT_CLIENT, db_name=checkpoint_db_name(),
            checkpoint_collection_name="langgraph_checkpoints",
            writes_collection_name="langgraph_checkpoint_writes",
            # Sem TTL os checkpoints (~25 KB cada: contexto recuperado + instruções) cresciam
            # para sempre. Mesmo prazo das conversas (CONVERSATION_RETENTION_DAYS).
            ttl=checkpoint_ttl_seconds(),
        )
    else:
        checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


def run_turn(*, question, access_levels, sources, history, static_instructions, llm, client_id,
             model_name, max_attempts, stream_deadline_s, refusal_message, followups, metrics,
             track_usage, emit, thread_id) -> ChatState:
    """Roda retrieve -> (refuse | generate) e devolve o estado final (`full_response`,
    `stats`, `source_cards`, `elapsed_ms`, `timed_out`, `chunk_agg`, `ttft_ms`)."""
    graph = get_graph()
    initial: ChatState = {"question": question, "sources": sources, "history": history,
                           "static_instructions": static_instructions}
    config = {"configurable": {
        "access_levels": access_levels, "llm": llm, "client_id": client_id, "model_name": model_name,
        "max_attempts": max_attempts, "stream_deadline_s": stream_deadline_s,
        "refusal_message": refusal_message, "followups": followups, "metrics": metrics,
        "track_usage": track_usage, "emit": emit, "thread_id": resolve_thread_id(thread_id),
    }}
    return graph.invoke(initial, config=config)
