"""Regressão do /api/chat depois da migração pra rag_graph.py (retrieve -> generate
como StateGraph, streaming via thread + fila em vez do generator síncrono direto).

Roda em subprocess com interpretador limpo pelo mesmo motivo de test_config_tenant.py:
config.py levanta na importação sem CLIENT_ID, e outros módulos de teste já podem ter
importado config/api no mesmo processo.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(script: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLIENT_ID": "teste", "MONGO_URI": "", "PYTHONPATH": str(ROOT)}
    return subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)


class ChatStreamTests(unittest.TestCase):
    def test_generate_path_streams_tokens_and_saves_conversation(self):
        script = textwrap.dedent("""
            import sys, json
            from unittest.mock import patch, MagicMock
            from fastapi.testclient import TestClient
            sys.path.insert(0, ".")
            import db
            with patch.object(db, "get_client", return_value=MagicMock()), \\
                 patch.object(db, "verify_tenant_identity", return_value=None):
                import backend.api as api

                class Chunk:
                    def __init__(self, content):
                        self.content = content
                        self.usage_metadata = {"input_tokens": 1, "output_tokens": 1}
                    def __add__(self, other):
                        return self if other is None else Chunk(self.content + (other.content or ""))

                def fake_retrieve(question, access_levels=None, sources=None):
                    return "contexto de teste", [{"chunk_id": "c1"}], {"mode": "hybrid"}

                fake_llm = MagicMock()
                fake_llm.stream.return_value = [Chunk("Olá"), Chunk(" mundo")]

                saved = {}
                def fake_save(thread_id, messages):
                    saved["thread_id"] = thread_id
                    saved["messages"] = messages

                with patch.object(api, "_get_llm", return_value=fake_llm), \\
                     patch.object(api, "_save_conversation", fake_save), \\
                     patch("rag_graph.retrieve_context", fake_retrieve), \\
                     patch("rag_graph.insufficient_evidence", return_value=False):
                    client = TestClient(api.app)
                    with client.stream("POST", "/api/chat", json={"question": "oi", "messages": []}) as resp:
                        body = "".join(resp.iter_text())
                events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
                tokens = "".join(e["delta"] for e in events if e["type"] == "token")
                assert tokens == "Olá mundo", tokens
                assert events[-1]["type"] == "done", events
                assert saved["messages"][-1] == {"role": "assistant", "content": "Olá mundo"}, saved
                print("OK")
        """)
        result = _run(script)
        self.assertIn("OK", result.stdout, result.stderr)

    def test_refusal_path_never_calls_the_llm(self):
        script = textwrap.dedent("""
            import sys, json
            from unittest.mock import patch, MagicMock
            sys.path.insert(0, ".")
            import db
            with patch.object(db, "get_client", return_value=MagicMock()), \\
                 patch.object(db, "verify_tenant_identity", return_value=None):
                import backend.api as api
                from fastapi.testclient import TestClient

                def fake_retrieve(question, access_levels=None, sources=None):
                    return "", [], {"mode": "hybrid"}

                fake_llm = MagicMock()
                with patch.object(api, "_get_llm", return_value=fake_llm), \\
                     patch("rag_graph.retrieve_context", fake_retrieve), \\
                     patch("rag_graph.insufficient_evidence", return_value=True):
                    client = TestClient(api.app)
                    with client.stream("POST", "/api/chat", json={"question": "oi", "messages": []}) as resp:
                        body = "".join(resp.iter_text())
                events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
                assert events[-1]["type"] == "done", events
                assert not fake_llm.stream.called
                print("OK")
        """)
        result = _run(script)
        self.assertIn("OK", result.stdout, result.stderr)


if __name__ == "__main__":
    unittest.main()
