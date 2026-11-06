"""Auditoria do grafo: lote que falha não pode virar `suporte: None` em silêncio."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MONGO_URI", "mongodb://localhost/test")
os.environ.setdefault("VOYAGE_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("CLIENT_ID", "test-tenant")

from eval import graph_audit  # noqa: E402


def _items(n):
    return [{"source": f"S{i}", "target": f"T{i}", "type": "contribui_para", "chunk_id": f"c{i}"} for i in range(n)]


def _render(it):
    return f"{it['source']} -> {it['target']}"


class Parse(unittest.TestCase):
    def test_json_depois_de_analise_em_prosa(self):
        raw = 'Analisando a relação X.\n\nO trecho mostra Y.\n\n{"itens": [{"n": 1, "suporte": "parcial"}]}'
        self.assertEqual(graph_audit._parse(raw)[1]["suporte"], "parcial")

    def test_json_com_cerca_de_codigo(self):
        self.assertEqual(graph_audit._parse('```json\n{"itens": [{"n": 2, "suporte": "sim"}]}\n```')[2]["suporte"], "sim")


class JudgeBatchRetry(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(graph_audit, "_chunk_text", return_value="trecho")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_lote_que_falha_e_refeito_item_a_item(self):
        calls = []

        def fake_complete(system, user, max_tokens=0):
            calls.append(user.count("### Item"))
            if user.count("### Item") > 1:
                raise ValueError("JSON truncado")
            return json.dumps({"itens": [{"n": 1, "suporte": "sim"}]})

        with mock.patch.object(graph_audit, "complete", fake_complete):
            rows = graph_audit.judge_batch(None, _items(3), "sys", _render)
        self.assertEqual([r["suporte"] for r in rows], ["sim", "sim", "sim"])
        self.assertEqual(calls, [3, 1, 1, 1])

    def test_veredito_ausente_so_refaz_os_que_faltam(self):
        def fake_complete(system, user, max_tokens=0):
            if user.count("### Item") == 2:
                return json.dumps({"itens": [{"n": 1, "suporte": "parcial"}]})
            return json.dumps({"itens": [{"n": 1, "suporte": "nao"}]})

        with mock.patch.object(graph_audit, "complete", fake_complete):
            rows = graph_audit.judge_batch(None, _items(2), "sys", _render)
        self.assertEqual([r["suporte"] for r in rows], ["parcial", "nao"])

    def test_falha_persistente_continua_visivel_como_none(self):
        with mock.patch.object(graph_audit, "complete", side_effect=RuntimeError("fora do ar")):
            rows = graph_audit.judge_batch(None, _items(2), "sys", _render)
        self.assertEqual([r["suporte"] for r in rows], [None, None])


if __name__ == "__main__":
    unittest.main()
