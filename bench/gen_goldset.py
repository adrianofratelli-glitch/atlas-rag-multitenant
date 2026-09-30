"""Generate the benchmark question set with gold answers straight from the source PDF.

The gold set is derived from the raw document, never from either index, so it can
judge the PDF-backed and Markdown-backed pipelines impartially.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.llm import complete  # noqa: E402

N_QUESTIONS = 15
OUT = Path(__file__).parent / "goldset.json"

SYSTEM = """Você é um avaliador de sistemas RAG. A partir do documento fornecido,
crie um conjunto de perguntas de teste com resposta-gabarito.

Regras:
- As respostas devem ser 100% extraíveis do documento. Nada inferido.
- Cubra tipos variados: fato pontual, valores/números, datas/prazos, conteúdo de
  tabelas, listas/enumerações, estrutura de seções, e síntese de um trecho.
- Inclua pelo menos 4 perguntas cuja resposta esteja dentro de uma TABELA.
- A "evidencia" deve ser uma citação literal curta (<=200 caracteres) do documento.
- Responda SOMENTE com JSON válido, sem cercas de código."""

TEMPLATE = """Documento:

<documento>
{doc}
</documento>

Gere exatamente {n} itens no formato:

{{"perguntas": [
  {{"id": 1,
    "pergunta": "...",
    "gabarito": "resposta correta e completa, 1-4 frases",
    "evidencia": "citação literal curta do documento",
    "tipo": "fato|numero|data|tabela|lista|estrutura|sintese"}}
]}}"""


def main() -> None:
    doc = Path(os.environ["BENCH_MD"]).read_text(encoding="utf-8")
    raw = complete(SYSTEM, TEMPLATE.format(doc=doc, n=N_QUESTIONS))
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    data = json.loads(raw)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(data['perguntas'])} perguntas -> {OUT}")
    for q in data["perguntas"]:
        print(f"  [{q['tipo']:9}] {q['pergunta']}")


if __name__ == "__main__":
    main()
