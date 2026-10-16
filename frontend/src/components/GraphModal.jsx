import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import GraphTraversal from './GraphTraversal'

// Expanded view of the Graph-RAG aggregation: the traversal with labels, the
// stages with this turn's numbers, what MongoDB capability each stage uses and
// the limits we declare up front.
const LIMITS = [
  'Sem algoritmos de grafo no banco (PageRank, caminho mínimo, comunidades): as comunidades são calculadas offline (Louvain) e só o resultado fica no Atlas.',
  '$graphLookup segue um campo de uma coleção, sem peso nas arestas; a profundidade fica em 2 saltos.',
  'O grafo é extraído por LLM: entidades e relações podem faltar ou vir duplicadas. Toda resposta cita os trechos originais, não só o grafo.',
  'autoEmbed e rerank-3 estão em Preview no MongoDB 9.',
]

export default function GraphModal({ stats, onClose }) {
  const closeRef = useRef(null)
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose }, [onClose])

  useEffect(() => {
    const opener = document.activeElement
    closeRef.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') onCloseRef.current() }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      opener?.focus?.()
    }
  }, [])

  const live = Boolean(stats)
  const n = (value) => (live ? value : null)
  const stages = [
    { op: '$rankFusion', sub: '$vectorSearch (autoEmbed) + $search (nome, sigla, código) nas entidades', value: n(stats?.seeds), unit: 'entradas' },
    { op: '$lookup', sub: '1º salto: vizinhos diretos, nos dois sentidos (campo links)', value: n(stats?.graph?.nodes?.filter((x) => x.hop === 1).length), unit: 'vizinhos' },
    { op: '$graphLookup', sub: `saltos seguintes, recursivo, sem atravessar hubs (>${stats?.hub_degree ?? 40} vizinhos)`, value: n(stats?.graph?.nodes?.filter((x) => x.hop >= 2).length), unit: 'nós' },
    { op: '$lookup + $rerank', sub: 'trechos citados pelas entidades e relações, reranqueados no banco', value: n(stats ? `${stats.evidence_candidates} → ${stats.reranked}` : null), unit: 'trechos' },
    { op: '$unionWith documents', sub: 'busca híbrida direta ($rankFusion + $rerank): cobre o fato pontual que o grafo não modelou', value: n(stats ? `${stats.from_graph ?? '—'} via grafo · ${stats.from_direct ?? 0} só da busca direta` : null), unit: 'nos finais' },
    { op: '$unionWith kg_communities', sub: '$vectorSearch nos sumários de comunidade (perguntas globais)', value: n(stats?.communities?.length), unit: 'temas' },
  ]

  return createPortal(
    <div className="arch-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="arch-modal gm" role="dialog" aria-modal="true" aria-labelledby="gm-title">
        <header className="arch-head">
          <div>
            <h2 id="gm-title">Graph-RAG numa aggregation</h2>
            <p>{live ? 'O subgrafo percorrido na última pergunta.' : 'Como cada pergunta percorre o grafo, dentro do cluster Atlas.'}</p>
          </div>
          <button ref={closeRef} type="button" className="arch-close" onClick={onClose} aria-label="Fechar">×</button>
        </header>

        <div className="gm-body">
          <GraphTraversal stats={stats} size={420} large />
          <div className="gm-side">
            <code className="arch-pipe-title">kg_entities.aggregate()</code>
            {stages.map((s) => (
              <div key={s.op} className="arch-stage">
                <div className="arch-stage-top"><code>{s.op}</code></div>
                <span className="arch-stage-sub">{s.sub}</span>
                {s.value != null && <span className="arch-stage-value"><b>{s.value}</b> {s.unit}</span>}
              </div>
            ))}
            {live && stats.documents_covered?.length > 0 && (
              <p className="gm-docs">Documentos alcançados pelo subgrafo: {stats.documents_covered.join(', ')}</p>
            )}
          </div>
        </div>

        <footer className="arch-foot">
          <div className="arch-metrics">
            <span><b>1</b> ida ao banco por pergunta</span>
            <span><b>1</b> cluster: grafo, chunks e vetores</span>
            <span><b>0</b> sincronização entre bancos</span>
          </div>
          <div className="gm-limits">
            <span className="arch-replaced-label">limites declarados</span>
            <ul>{LIMITS.map((l) => <li key={l}>{l}</li>)}</ul>
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  )
}
