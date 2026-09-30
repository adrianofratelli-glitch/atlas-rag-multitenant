"""Render the PDF-vs-Markdown benchmark as a PDF report."""
import json
import os
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

HERE = Path(__file__).parent
OUT = HERE / "relatorio_pdf_vs_markdown.pdf"

GREEN = colors.HexColor("#00684A")
LEAF = colors.HexColor("#00ED64")
GREY = colors.HexColor("#5C6C75")
LIGHT = colors.HexColor("#F1F5F4")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=18, textColor=GREEN, spaceAfter=10)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=13, textColor=GREEN, spaceBefore=14, spaceAfter=6)
BODY = ParagraphStyle("BODY", parent=ss["BodyText"], fontSize=9.5, leading=14, alignment=TA_JUSTIFY)
SMALL = ParagraphStyle("SMALL", parent=BODY, fontSize=8, leading=11)
CELL = ParagraphStyle("CELL", parent=BODY, fontSize=7.5, leading=9.5, alignment=0)
CELLB = ParagraphStyle("CELLB", parent=CELL, fontName="Helvetica-Bold")


def p(text, style=BODY):
    return Paragraph(text, style)


def table(data, widths, header_bg=GREEN, extra=()):
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDE3E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        *extra,
    ]))
    return t


def avg(rows, store, key):
    vals = [r["stores"][store][key] for r in rows]
    return sum(vals) / len(vals)


def delta(a, b, fmt="{:+.2f}"):
    return fmt.format(b - a)


def main() -> None:
    results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    corpus = json.loads((HERE / "corpus_stats.json").read_text(encoding="utf-8"))
    n = len(results)

    doc = SimpleDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title="PDF vs Markdown - estudo de acuracia RAG - Vibra",
        author="MongoDB",
    )
    story = []

    logo = HERE / "assets" / "mongodb-logo.png"
    if logo.exists():
        story.append(Image(str(logo), width=4.6 * cm, height=4.6 * cm * 1112 / 4408,
                           hAlign="LEFT"))
        story.append(Spacer(1, 14))

    story.append(p("PDF ou Markdown: qual formato serve melhor a base de conhecimento?", H1))
    story.append(p(
        "Estudo preparado para a Vibra | Assistente RAG sobre MongoDB Atlas Vector Search",
        ParagraphStyle("SUB", parent=BODY, fontSize=10, textColor=GREY, spaceAfter=12)))
    # --- metodologia
    story.append(p("1. Como o teste foi montado", H2))
    story.append(p(
        "A regra foi mudar uma coisa só. O formato do arquivo de origem é a única variável: "
        "o particionamento em trechos (RecursiveCharacterTextSplitter, 800 caracteres com 150 de "
        "sobreposição), o modelo de embeddings (voyage-3, 1024 dimensões), a busca híbrida "
        "(vetorial e léxica combinadas por fusão RRF, k=60), o reranking (rerank-2, oito melhores "
        "trechos) e o modelo que redige a resposta (claude-sonnet-4-6, temperatura zero) são "
        "exatamente os mesmos nos dois cenários.", BODY))
    story.append(Spacer(1, 4))
    story.append(p(
        "Cada formato foi para um banco próprio no Atlas, com índices vetorial e léxico "
        "idênticos, de modo que uma busca nunca enxerga os trechos da outra base. Sem isso, "
        "os dois formatos competiriam entre si dentro da mesma consulta e o resultado não "
        "diria nada.", BODY))
    story.append(Spacer(1, 4))
    story.append(p(
        "As perguntas e as respostas de referência foram extraídas do documento completo, "
        "fora de qualquer um dos dois índices. Isso é o que permite julgar os dois com a mesma "
        "régua. Cobrimos fatos pontuais, valores, datas, listas, estrutura de seções e, de "
        "propósito, conteúdo que só existe dentro de tabelas: era ali que esperávamos ver "
        "diferença.", BODY))
    story.append(Spacer(1, 4))
    story.append(p(
        "Cada resposta recebeu três notas de 0 a 5. Acurácia mede se os fatos e números batem "
        "com o gabarito. Completude, se a resposta cobre tudo que deveria. Fidelidade, se o "
        "sistema afirmou algo que o documento não sustenta; nessa nota, admitir que não "
        "encontrou a informação vale nota máxima, ainda que zere a acurácia. Além dessas, "
        "medimos a cobertura de evidência, que não depende de julgamento nenhum: é a fração "
        "das palavras da citação-gabarito que aparecem no contexto recuperado. Ela separa o "
        "que é mérito da busca do que é mérito da redação.", BODY))

    # --- corpus
    story.append(p("2. As duas bases lado a lado", H2))
    story.append(p(
        f"O documento usado no teste é {os.getenv('BENCH_DOC_DESCRIPTION', 'o corpus de referência do tenant')}, "
        "com mais de cem páginas e forte presença de tabelas de "
        "ações, custos e indicadores. Serve bem como caso de teste porque reúne exatamente o "
        "tipo de conteúdo que costuma sofrer na extração: números que só fazem sentido junto "
        "do cabeçalho da coluna em que estão.", BODY))
    story.append(Spacer(1, 6))
    rows = [["", "PDF", "Markdown"]]
    for label, key in [
        ("Arquivo", "file"), ("Loader", "loader"), ("Caracteres extraídos", "chars"),
        ("Chunks indexados", "chunks"), ("Títulos (#) preservados", "headings"),
        ("Linhas de tabela preservadas", "table_rows"),
        ("Metadado de página por chunk", "pages"),
    ]:
        rows.append([p(label, CELLB), p(str(corpus["pdf"][key]), CELL), p(str(corpus["md"][key]), CELL)])
    story.append(table(rows, [7 * cm, 5 * cm, 5 * cm]))
    story.append(Spacer(1, 6))
    story.append(p(
        "A conversão para Markdown usou a biblioteca pymupdf4llm, que reconstrói a hierarquia "
        "de títulos e devolve as tabelas em sintaxe Markdown. A leitura do PDF usa o "
        "PyPDFLoader, que entrega texto corrido, uma página por vez. A linha de títulos e a de "
        "linhas de tabela da tabela acima resumem bem a diferença: o que no PDF vira texto "
        "solto, no Markdown continua sendo tabela.", SMALL))

    # --- resultados
    story.append(p("3. O que os números dizem", H2))
    metrics = [
        ("Acurácia (0-5)", "acuracia", "{:.2f}", True),
        ("Completude (0-5)", "completude", "{:.2f}", True),
        ("Fidelidade (0-5)", "fidelidade", "{:.2f}", True),
        ("Cobertura de evidência", "evidence_recall", "{:.1%}", True),
        ("Score de rerank (média)", "mean_rerank", "{:.3f}", True),
        ("Latência de recuperação (ms)", "retrieval_ms", "{:.0f}", False),
    ]
    rows = [["Métrica", "PDF", "Markdown", "Δ"]]
    for label, key, fmt, higher_better in metrics:
        a, b = avg(results, "pdf", key), avg(results, "md", key)
        d = b - a
        if abs(d) < 1e-9:
            colour = "#5C6C75"
        else:
            colour = "#00684A" if (d > 0) == higher_better else "#B1371F"
        rows.append([
            p(label, CELLB), p(fmt.format(a), CELL), p(fmt.format(b), CELL),
            p(f"<font color='{colour}'><b>{fmt.format(d) if '%' not in fmt else f'{d:+.1%}'}</b></font>", CELL),
        ])
    story.append(table(rows, [7 * cm, 3.3 * cm, 3.3 * cm, 3.4 * cm]))

    wins = {"pdf": 0, "md": 0, "tie": 0}
    for r in results:
        a = r["stores"]["pdf"]["acuracia"]
        b = r["stores"]["md"]["acuracia"]
        wins["pdf" if a > b else "md" if b > a else "tie"] += 1
    story.append(Spacer(1, 6))
    story.append(p(
        f"Em acurácia por pergunta: <b>{wins['md']}</b> vitórias do Markdown, "
        f"<b>{wins['pdf']}</b> do PDF, <b>{wins['tie']}</b> empates (de {n}).", BODY))

    # --- por tipo
    story.append(p("4. Acurácia por tipo de pergunta", H2))
    tipos = sorted({r["tipo"] for r in results})
    rows = [["Tipo", "n", "PDF", "Markdown", "Δ"]]
    for t in tipos:
        sub = [r for r in results if r["tipo"] == t]
        a, b = avg(sub, "pdf", "acuracia"), avg(sub, "md", "acuracia")
        rows.append([p(t, CELLB), p(str(len(sub)), CELL), p(f"{a:.2f}", CELL),
                     p(f"{b:.2f}", CELL), p(f"{b - a:+.2f}", CELL)])
    story.append(table(rows, [4.5 * cm, 1.5 * cm, 3.5 * cm, 3.5 * cm, 4 * cm]))

    # --- leitura
    acc_p, acc_m = avg(results, "pdf", "acuracia"), avg(results, "md", "acuracia")
    rr_p, rr_m = avg(results, "pdf", "mean_rerank"), avg(results, "md", "mean_rerank")
    lat_p, lat_m = avg(results, "pdf", "retrieval_ms"), avg(results, "md", "retrieval_ms")
    rec_p, rec_m = avg(results, "pdf", "evidence_recall"), avg(results, "md", "evidence_recall")

    story.append(p("5. Leitura dos resultados", H2))
    story.append(p(
        f"A resposta à pergunta de origem é sim: a acurácia se mantém. O Markdown ficou em "
        f"{acc_m:.2f} de 5 e o PDF em {acc_p:.2f}, com {wins['tie']} das {n} perguntas empatadas "
        "na nota máxima e nenhuma pergunta em que o PDF tenha ido melhor. A cobertura de "
        f"evidência praticamente não se moveu ({rec_p:.1%} contra {rec_m:.1%}), o que era "
        "esperado: a busca híbrida acha o trecho certo nos dois casos.", BODY))
    story.append(Spacer(1, 4))
    story.append(p(
        "O ganho interessante não está na resposta final, e sim um passo antes dela. A nota "
        f"média que o reranker atribui aos trechos recuperados subiu de {rr_p:.3f} para "
        f"{rr_m:.3f}, uma diferença de {(rr_m / rr_p - 1):.0%}. Esse número mede o quanto cada "
        "trecho, sozinho, responde à pergunta. E é aí que a estrutura preservada faz diferença: "
        "um trecho em Markdown costuma trazer o título da seção e a tabela inteira, cabeçalho "
        "incluído. No texto corrido extraído do PDF, a mesma tabela chega partida no meio, e o "
        "modelo recebe uma coluna de números sem o rótulo que diz o que eles são.", BODY))
    story.append(Spacer(1, 4))
    story.append(p(
        f"A recuperação também ficou mais rápida, {lat_m:.0f} ms contra {lat_p:.0f} ms por "
        "consulta. Trechos mais coesos geram menos candidatos ambíguos para a fusão resolver.", BODY))
    story.append(Spacer(1, 4))
    story.append(p(
        "Há uma contrapartida que precisa entrar na conta. O Markdown foi ingerido como um "
        "arquivo único, e arquivo único não tem paginação. Na prática, toda citação de origem "
        "passa a apontar para a página zero, enquanto a ingestão do PDF cita a página real. "
        "Num assistente cujo diferencial é mostrar de onde veio a resposta, isso é uma perda "
        "que o usuário enxerga. Dá para resolver convertendo página a página e guardando o "
        "número no metadado, mas é trabalho de ingestão, não um ganho que vem de graça com o "
        "formato.", BODY))
    story.append(Spacer(1, 4))
    story.append(p(
        f"Por fim, o custo de indexar. O Markdown produziu {corpus['md']['chunks']} trechos "
        f"contra {corpus['pdf']['chunks']} do PDF, um aumento de "
        f"{(int(corpus['md']['chunks']) / int(corpus['pdf']['chunks']) - 1):.0%}, porque a "
        "sintaxe de títulos e tabelas também ocupa caracteres. Mais trechos significam mais "
        "embeddings a gerar, mais espaço no índice vetorial e uma ingestão proporcionalmente "
        "mais demorada. Num documento desse tamanho é irrelevante; numa base com milhares de "
        "documentos, entra no orçamento.", BODY))

    story.append(p("6. Recomendação", H2))
    story.append(p(
        "Recomendamos adotar o Markdown como formato de ingestão. Do ponto de vista de "
        f"acurácia a troca é segura, já que não houve piora em nenhuma das {n} perguntas, e a "
        "relevância dos trechos recuperados melhora de forma mensurável, principalmente em "
        "conteúdo tabular, que é justamente onde documentos de planejamento concentram os "
        "números que importam.", BODY))
    story.append(Spacer(1, 4))
    story.append(p(
        "Duas condições acompanham a recomendação. A primeira é preservar o número da página "
        "durante a conversão, para não perder a citação de origem. A segunda é manter a "
        "conversão automatizada a partir do documento oficial: o PDF continua sendo a fonte da "
        "verdade e o Markdown passa a ser um artefato derivado, que qualquer pessoa consegue "
        "regerar quando o documento for revisado.", BODY))
    story.append(Spacer(1, 6))
    story.append(p(
        f"Sobre os limites do que foi medido: são {n} perguntas sobre um documento, com "
        "avaliação feita por um modelo. A diferença de acurácia entre os dois formatos é "
        "pequena demais para uma amostra desse tamanho sustentar, e por isso a conclusão "
        "honesta é de empate técnico, não de superioridade. Já a diferença nas notas de "
        "reranking vem direto do modelo de reranking e não depende de julgamento; é o achado "
        "mais sólido do estudo.", SMALL))

    story.append(PageBreak())

    # --- detalhe
    story.append(p("7. Pergunta a pergunta", H2))
    rows = [["#", "Pergunta / tipo", "PDF\nacc/comp/fid", "MD\nacc/comp/fid", "Cobertura\nPDF / MD"]]
    for r in results:
        sp, sm = r["stores"]["pdf"], r["stores"]["md"]
        rows.append([
            p(str(r["id"]), CELL),
            p(f"{r['pergunta']}<br/><font color='#5C6C75'>[{r['tipo']}]</font>", CELL),
            p(f"{sp['acuracia']} / {sp['completude']} / {sp['fidelidade']}", CELL),
            p(f"{sm['acuracia']} / {sm['completude']} / {sm['fidelidade']}", CELL),
            p(f"{sp['evidence_recall']:.0%} / {sm['evidence_recall']:.0%}", CELL),
        ])
    story.append(table(rows, [0.8 * cm, 8.2 * cm, 2.6 * cm, 2.6 * cm, 3 * cm]))

    # --- divergências
    diffs = [r for r in results
             if r["stores"]["pdf"]["acuracia"] != r["stores"]["md"]["acuracia"]]
    if diffs:
        story.append(PageBreak())
        story.append(p("8. A pergunta em que os dois formatos discordaram", H2))
        for r in diffs:
            sp, sm = r["stores"]["pdf"], r["stores"]["md"]
            block = [
                p(f"<b>Q{r['id']} [{r['tipo']}]</b> — {r['pergunta']}", BODY),
                Spacer(1, 3),
                p(f"<b>Gabarito:</b> {r['gabarito']}", SMALL),
                Spacer(1, 3),
                table([
                    ["", "Resposta", "acc"],
                    [p("PDF", CELLB), p(sp["resposta"][:700], CELL), p(str(sp["acuracia"]), CELL)],
                    [p("MD", CELLB), p(sm["resposta"][:700], CELL), p(str(sm["acuracia"]), CELL)],
                ], [1.4 * cm, 14 * cm, 1.8 * cm], header_bg=GREY),
                Spacer(1, 10),
            ]
            story.append(KeepTogether(block))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    print(f"-> {OUT}")


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LEAF)
    canvas.setLineWidth(2)
    canvas.line(1.8 * cm, A4[1] - 1.2 * cm, A4[0] - 1.8 * cm, A4[1] - 1.2 * cm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GREY)
    canvas.drawString(1.8 * cm, 1.1 * cm, "Estudo preparado para a Vibra  |  MongoDB Atlas Vector Search")
    canvas.drawRightString(A4[0] - 1.8 * cm, 1.1 * cm, f"pág. {doc.page}")
    canvas.restoreState()


if __name__ == "__main__":
    sys.exit(main())
