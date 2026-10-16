"""Ferramenta de rótulo humano: kappa e ida e volta da planilha, sem banco."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("CLIENT_ID", "test-tenant")

from eval import human_review as hr  # noqa: E402


class TestKappa(unittest.TestCase):
    def test_perfect_and_chance(self):
        self.assertEqual(hr._kappa([("sim", "sim"), ("nao", "nao")], ["sim", "nao"]), (1.0, 1.0))
        agree, kappa = hr._kappa([("sim", "sim"), ("sim", "nao"), ("nao", "sim"), ("nao", "nao")], ["sim", "nao"])
        self.assertEqual(agree, 0.5)
        self.assertAlmostEqual(kappa, 0.0)
        self.assertIsNone(hr._kappa([], ["sim"]))


class TestRoundTrip(unittest.TestCase):
    def test_export_hides_judge_and_score_maps_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            (reports / "graph_audit.jsonl").write_text(json.dumps(
                {"label": "AG1 Ação", "type": "acao", "description": "d", "chunk_id": "c1", "suporte": "sim"}) + "\n")
            (reports / "graph_compare.jsonl").write_text(json.dumps({
                "id": "rel-1", "question": "q",
                "results": {"graph": {"answer": "resposta do grafo"}, "hybrid": {"answer": "resposta h"},
                            "hybrid_wide": {"answer": "resposta l"}},
                "pairs": {"hybrid": {"winner": "graph"}, "hybrid_wide": {"winner": "hybrid_wide"}}}) + "\n")
            with mock.patch.object(hr, "REPORTS", reports), mock.patch.object(hr, "XLSX", reports / "h.xlsx"), \
                    mock.patch.object(hr, "_chunk_texts", return_value={"c1": "trecho"}):
                hr.export(seed=1)
                from openpyxl import load_workbook
                wb = load_workbook(reports / "h.xlsx")
                self.assertEqual(wb["_chave"].sheet_state, "hidden")
                header = [c.value for c in wb["respostas"][1]]
                self.assertFalse(any("juiz" in str(h).lower() for h in header))
                wb["auditoria"]["E2"] = "sim"
                key = {r[0]: r for r in wb["_chave"].iter_rows(min_row=2, values_only=True)}
                for n, (_, _, rival, a, b) in key.items():  # humano escolhe sempre o grafo
                    wb["respostas"][f"E{n + 1}"] = "A" if a == "graph" else "B"
                wb.save(reports / "h.xlsx")
                hr.score()
            report = (reports / "human_agreement.md").read_text()
            self.assertIn("auditoria do grafo: 1 itens rotulados, concordância 100%", report)
            self.assertIn("respostas (qual atende melhor): 2 itens rotulados, concordância 50%", report)


if __name__ == "__main__":
    unittest.main()
