import { useState } from 'react'
import { C } from '../theme'
import { TYPE_COLORS, TYPE_LABELS } from '../graphLayout'
import GraphModal from './GraphModal'

const ACCENTS = [C.purple, C.green, C.cyan, C.orange]

// Empty state of the Graph-RAG tab: what the graph is, how big it is, and
// relational questions where traversal changes the answer.
export default function GraphWelcome({ config, onPick, compare = false }) {
  const [showArch, setShowArch] = useState(false)
  const g = config.graph || {}
  const types = Object.entries(g.types || {}).filter(([, n]) => n > 0)
  return (
    <div>
      <div className="fade-up d1" style={{ padding: '18px 0 4px' }}>
        <div className="hub-badge">Graph-RAG · $graphLookup</div>
        <h1 className="splash-title">
          {compare ? <>Mesma pergunta, <span>duas recuperações</span>.</> : <>Pergunte o que <span>liga</span> os documentos.</>}
        </h1>
        <p className="splash-sub">
          {compare ? (
            <>
              Graph-RAG não troca a busca: soma uma camada. À esquerda, a busca híbrida da outra aba
              ($vectorSearch + $search + $rerank sobre chunks). À direita, a mesma busca mais o grafo extraído dos
              mesmos documentos, percorrido com <strong style={{ color: C.text }}>$graphLookup</strong>. Mesmo corpus,
              mesmo Claude: muda só o que vai ao prompt.
            </>
          ) : (
            <>
              A busca híbrida continua sendo a base; o grafo é uma camada por cima. Unidades, ações, objetivos,
              sistemas e normas extraídos do corpus moram no <strong style={{ color: C.green }}>mesmo banco</strong> dos
              chunks. Cada pergunta acha as entidades de entrada, percorre as relações com{' '}
              <strong style={{ color: C.text }}>$graphLookup</strong> e junta os trechos que provam cada ligação aos da
              busca híbrida, em uma única aggregation. Vale para perguntas sobre relações; fato pontual, a base já resolve.
            </>
          )}
        </p>
        <div className="stack-points">
          <span><b>{g.entities ?? '—'}</b> entidades</span>
          <span><b>{g.relations ?? '—'}</b> relações</span>
          <span><b>{g.communities ?? '—'}</b> comunidades</span>
          <span><b>1</b> aggregation</span>
          <button type="button" className="arch-link arch-link-narrow" onClick={() => setShowArch(true)}>ver a travessia</button>
        </div>
        {types.length > 0 && (
          <div className="gw-types">
            {types.map(([t, n]) => (
              <span key={t}><i style={{ background: TYPE_COLORS[t] || C.muted }} />{n} {TYPE_LABELS[t] || t}</span>
            ))}
          </div>
        )}
        {showArch && <GraphModal onClose={() => setShowArch(false)} />}
      </div>

      {config.graph_questions?.length > 0 && (
        <div className="fade-up d3">
          <div className="sb-section-label" style={{ marginLeft: 0 }}>Perguntas onde o grafo faz diferença</div>
          <div className="sugg-grid">
            {config.graph_questions.slice(0, 6).map((q, i) => (
              <button key={i} className="sugg-card" style={{ '--card-accent': ACCENTS[i % ACCENTS.length] }} onClick={() => onPick(q)}>
                <span className="sugg-num">Pergunta {String(i + 1).padStart(2, '0')}</span>
                <span className="sugg-text">{q}</span>
                <span className="sugg-arrow">→</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
