import { useState } from 'react'
import GraphTraversal from './GraphTraversal'
import GraphModal from './GraphModal'

// Side panel of the Graph-RAG tab: the same "one question, one aggregation"
// story as ArchitecturePanel, but the aggregation walks the knowledge graph.
const REPLACED = ['banco de grafo à parte', 'sincronizar grafo ↔ vetor', 'API de embedding', 'API de rerank', 'ETL entre bancos']

const CAPTION = {
  idle: 'Faça uma pergunta: a travessia acende aqui.',
  retrieving: 'Percorrendo o grafo no Atlas, numa aggregation só…',
  generating: 'Subgrafo e trechos prontos. Claude redigindo…',
  done: 'Última pergunta, salto por salto.',
}

export default function GraphPanel({ phase, stats }) {
  const [expanded, setExpanded] = useState(false)
  const live = phase === 'generating' || phase === 'done'
  const degraded = Boolean(stats?.graph_degraded)
  const atlas = phase === 'retrieving' ? 'pulse' : live ? 'lit' : ''
  const shown = live && !degraded ? stats : null

  return (
    <aside className="ws-aside" aria-label="Travessia do grafo">
      <div className="ap-head">
        <span className="ap-kicker">Uma pergunta, um grafo, uma aggregation</span>
        <p className="ap-caption" aria-live="polite">{CAPTION[phase]}</p>
      </div>

      <div className={`ap-node ${phase !== 'idle' ? 'lit' : ''}`}>
        <strong>Aplicação</strong><span>monta um pipeline</span>
      </div>
      <div className={`ap-link ${phase !== 'idle' ? 'lit' : ''}`}><span>1 aggregation</span></div>

      <div className={`ap-cluster ${atlas}`}>
        <div className="ap-cluster-label">MongoDB Atlas</div>
        <code className="ap-pipe-title">kg_entities.aggregate()</code>
        <GraphTraversal stats={shown} searching={phase === 'retrieving'} />
        {shown && (
          <div className="gp-numbers">
            <span><b>{shown.seeds}</b> entradas</span>
            <span><b>{shown.graph_nodes}</b> nós</span>
            <span><b>{shown.graph_edges}</b> relações</span>
            <span><b>{shown.documents_covered?.length ?? 0}</b> docs</span>
          </div>
        )}
        <div className="ap-filter">tenant em cada estágio · hubs com mais de <code>{stats?.hub_degree ?? 40}</code> vizinhos não são atravessados</div>
        <div className="ap-same">
          <span>no mesmo cluster:</span> entidades, relações, comunidades, chunks, vetores
        </div>
      </div>

      <div className={`ap-link ${live ? 'lit' : ''}`} />
      <div className={`ap-node ${phase === 'generating' ? 'pulse' : phase === 'done' ? 'lit' : ''}`}>
        <strong>Claude via gateway</strong><span>fatos do grafo + trechos citáveis</span>
      </div>

      {degraded && live && (
        <p className="ap-warn">O grafo não respondeu nesta pergunta: caiu para a busca híbrida.</p>
      )}

      <div className="ap-replaced">
        <span>o que não precisou existir</span>
        <div>{REPLACED.map((item) => <s key={item}>{item}</s>)}</div>
      </div>

      <button type="button" className="arch-link ap-expand" onClick={() => setExpanded(true)}>ampliar</button>
      {expanded && <GraphModal stats={shown} onClose={() => setExpanded(false)} />}
    </aside>
  )
}
