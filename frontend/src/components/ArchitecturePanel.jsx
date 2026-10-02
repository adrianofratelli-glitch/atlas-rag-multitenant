import { useState } from 'react'
import ArchitectureModal from './ArchitectureModal'
import ArchitectureFunnel from './ArchitectureFunnel'

// Side panel beside the chat: the native pipeline, lit by the phase of the
// current turn. idle -> retrieving (Atlas stages pulse) -> generating (numbers
// in, Claude active) -> done. Only rendered on wide screens (see .ws-aside).
const REPLACED = ['ETL / CDC', 'banco vetorial', 'motor de busca', 'API de embedding', 'API de rerank']

const CAPTION = {
  idle: 'Faça uma pergunta: cada estágio acende aqui.',
  retrieving: 'Buscando no Atlas, numa aggregation só…',
  generating: 'Recuperação pronta. Claude redigindo a resposta…',
  done: 'Última pergunta, operador por operador.',
}

export default function ArchitecturePanel({ phase, stats, sources }) {
  const [expanded, setExpanded] = useState(false)
  const live = phase === 'generating' || phase === 'done'
  const degraded = Boolean(stats?.native_degraded)
  const levels = stats?.access_levels || []
  const acl = levels.includes('restrito') ? 'publico + restrito' : 'publico'
  const rerm = stats?.rerank_model || 'rerank-3'
  const atlas = phase === 'retrieving' ? 'pulse' : live ? 'lit' : ''

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
        <ArchitectureFunnel
          stats={live && !degraded ? stats : null}
          sources={live && !degraded ? sources : null}
          searching={phase === 'retrieving'}
          rerankModel={rerm}
        />
        <div className="ap-filter">ACL <code>{acl}</code> + tenant dentro de cada ramo</div>
        <div className="ap-same">
          <span>no mesmo cluster:</span> chunks, vetores, conversas, checkpoints
        </div>
      </div>

      <div className={`ap-link ${live ? 'lit' : ''}`} />
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
        <ArchitectureModal stats={live ? stats : null} onClose={() => setExpanded(false)} />
      )}
    </aside>
  )
}
