import { useState } from 'react'
import ArchitectureModal from './ArchitectureModal'

// Side panel beside the chat: the native pipeline, lit by the phase of the
// current turn. idle -> retrieving (Atlas stages pulse) -> generating (numbers
// in, Claude active) -> done. Only rendered on wide screens (see .ws-aside).
const REPLACED = ['ETL / CDC', 'banco vetorial', 'motor de busca', 'API de embedding', 'API de rerank']

const CAPTION = {
  idle: 'Faça uma pergunta: cada estágio acende aqui.',
  retrieving: 'Buscando no Atlas, numa aggregation só…',
  generating: 'Recuperação pronta. Claude redigindo a resposta…',
  done: 'Última pergunta, estágio por estágio.',
}

export default function ArchitecturePanel({ phase, stats, elapsedMs }) {
  const [expanded, setExpanded] = useState(false)
  const live = phase === 'generating' || phase === 'done'
  const degraded = Boolean(stats?.native_degraded)
  const final = stats?.reranked ?? 8
  const levels = stats?.access_levels || []
  const acl = levels.includes('restrito') ? 'publico + restrito' : 'publico'
  const embm = stats?.embed_model || 'voyage-4'
  const rerm = stats?.rerank_model || 'rerank-3'
  const atlas = phase === 'retrieving' ? 'pulse' : live ? 'lit' : ''
  const stage = (dim) => `ap-stage ${dim && live ? 'dim' : atlas}`

  return (
    <aside className="ws-aside" aria-label="Arquitetura da recuperação">
      <div className="ap-head">
        <span className="ap-kicker">Uma pergunta, uma aggregation</span>
        <p className="ap-caption" aria-live="polite">{CAPTION[phase]}</p>
      </div>

      <div className={`ap-node ${phase !== 'idle' ? 'lit' : ''}`}>
        <strong>Aplicação</strong><span>monta um pipeline</span>
      </div>
      <div className={`ap-link ${phase !== 'idle' ? 'lit' : ''}`}><span>1 aggregation</span></div>

      <div className={`ap-cluster ${atlas}`}>
        <div className="ap-cluster-label">MongoDB Atlas</div>
        <code className="ap-pipe-title">documents.aggregate()</code>
        <div className="ap-branches">
          <div className={stage(degraded)}>
            <code>$vectorSearch</code>
            <span>autoEmbed · {embm}</span>
            {live && !degraded && <b>{stats?.vector_hits ?? 0}<small> de {final}</small></b>}
          </div>
          <div className={stage(false)}>
            <code>$search</code>
            <span>BM25</span>
            {live && <b>{stats?.lexical_hits ?? 0}<small> de {final}</small></b>}
          </div>
        </div>
        <div className="ap-filter">ACL <code>{acl}</code> + tenant em cada ramo</div>
        <div className={stage(degraded)}><code>$rankFusion</code><span>fusão no banco</span></div>
        <div className={stage(degraded)}><code>$rerank · {rerm}</code><span>rerank no banco</span></div>
        <div className={stage(false)}><code>$limit {final}</code><span>chunks para o prompt</span></div>
        <div className="ap-same">
          <span>no mesmo cluster:</span> chunks, vetores, conversas, checkpoints
        </div>
        {live && elapsedMs != null && (
          <div className="ap-ms"><b>{elapsedMs} ms</b> nesta pergunta</div>
        )}
      </div>

      <div className={`ap-link ${live ? 'lit' : ''}`}><span>{final} chunks</span></div>
      <div className={`ap-node ${phase === 'generating' ? 'pulse' : phase === 'done' ? 'lit' : ''}`}>
        <strong>Claude via gateway</strong><span>só a geração sai do banco</span>
      </div>

      {degraded && live && (
        <p className="ap-warn">Nativo falhou nesta pergunta: caiu para lexical-only.</p>
      )}

      <div className="ap-replaced">
        <span>o que não precisou existir</span>
        <div>{REPLACED.map((item) => <s key={item}>{item}</s>)}</div>
      </div>

      <button type="button" className="arch-link ap-expand" onClick={() => setExpanded(true)}>ampliar</button>
      {expanded && (
        <ArchitectureModal stats={live ? stats : null} elapsedMs={elapsedMs} onClose={() => setExpanded(false)} />
      )}
    </aside>
  )
}
