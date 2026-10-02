import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

// One question, one aggregation: the native pipeline drawn inside the cluster.
// With `stats` (last answer) each stage shows that turn's numbers; without it
// (welcome screen) the same drawing is shown without numbers.
const REPLACED = ['ETL / CDC', 'banco vetorial à parte', 'motor de busca à parte', 'API de embedding', 'API de rerank']

export default function ArchitectureModal({ stats, onClose }) {
  const closeRef = useRef(null)
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose }, [onClose])

  // Parent re-renders on every streamed token: keep this effect mount-only.
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
  const degraded = Boolean(stats?.native_degraded)
  const final = stats?.reranked ?? 8
  const levels = stats?.access_levels || []
  const acl = levels.includes('restrito') ? 'publico + restrito' : 'publico'
  const embm = stats?.embed_model || 'voyage-4'
  const rerm = stats?.rerank_model || 'rerank-3'
  const n = (value) => (live ? value : null)

  return createPortal(
    <div className="arch-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="arch-modal" role="dialog" aria-modal="true" aria-labelledby="arch-title">
        <header className="arch-head">
          <div>
            <h2 id="arch-title">Uma pergunta, uma aggregation</h2>
            <p>
              {live
                ? 'O que aconteceu na última pergunta, dentro do cluster Atlas.'
                : 'Como cada pergunta é respondida, dentro do cluster Atlas.'}
            </p>
          </div>
          <button ref={closeRef} type="button" className="arch-close" onClick={onClose} aria-label="Fechar">×</button>
        </header>

        {degraded && (
          <div className="arch-warn">
            Nesta pergunta o pipeline nativo falhou e a recuperação caiu para lexical-only. O chat seguiu respondendo.
          </div>
        )}

        <div className="arch-flow">
          <div className="arch-node">
            <strong>Aplicação</strong>
            <span>monta um pipeline</span>
          </div>
          <div className="arch-arrow"><span>1 aggregation</span></div>

          <div className="arch-cluster">
            <div className="arch-cluster-label">MongoDB Atlas cluster</div>
            <div className="arch-cluster-body">
              <div className="arch-pipeline">
                <code className="arch-pipe-title">documents.aggregate()</code>
                <div className="arch-branches">
                  <Stage op="$vectorSearch" sub={`autoEmbed · ${embm}`} preview
                    value={n(stats?.vector_hits)} unit={`dos ${final} finais`} dim={degraded} />
                  <Stage op="$search" sub="BM25 · text_index"
                    value={n(stats?.lexical_hits)} unit={`dos ${final} finais`} />
                </div>
                <div className="arch-filter">filtro de ACL (<code>{acl}</code>) e de tenant dentro de cada ramo</div>
                <Stage op="$rankFusion" sub="reciprocal rank fusion, no banco" dim={degraded} />
                <Stage op={`$rerank · ${rerm}`} sub="cross-encoder, no banco" preview={rerm === 'rerank-3'} dim={degraded} />
                <Stage op={`$limit ${final}`} sub="chunks que vão para o prompt" />
              </div>

              <div className="arch-same">
                <div className="arch-same-label">no mesmo cluster</div>
                <Doc title="chunks" sub="+ ACL, tenant, TTL" />
                <Doc title="vetores" sub="mantidos em sincronia pelo Atlas" />
                <Doc title="conversas" sub="retomadas por thread" />
                <Doc title="checkpoints" sub="estado do LangGraph" />
              </div>
            </div>
          </div>

          <div className="arch-arrow"><span>{final} chunks</span></div>
          <div className="arch-node">
            <strong>Claude via gateway</strong>
            <span>só a geração sai do banco</span>
          </div>
        </div>

        <footer className="arch-foot">
          <div className="arch-metrics">
            <span><b>1</b> ida ao banco por pergunta</span>
            <span><b>0</b> chaves de IA externas na recuperação</span>
          </div>
          <div className="arch-replaced">
            <span className="arch-replaced-label">o que não precisou existir</span>
            {REPLACED.map((item) => <s key={item}>{item}</s>)}
          </div>
          <p className="arch-note">autoEmbed e rerank-3 estão em Preview no MongoDB 9. Embedding e rerank são cobrados pela plataforma.</p>
        </footer>
      </div>
    </div>,
    document.body,
  )
}

function Stage({ op, sub, value, unit, preview, dim }) {
  return (
    <div className={`arch-stage${dim ? ' dim' : ''}`}>
      <div className="arch-stage-top">
        <code>{op}</code>
        {preview && <span className="arch-preview">preview</span>}
      </div>
      <span className="arch-stage-sub">{sub}</span>
      {value != null && (
        <span className="arch-stage-value"><b>{value}</b>{unit ? ` ${unit}` : ''}</span>
      )}
    </div>
  )
}

function Doc({ title, sub }) {
  return (
    <div className="arch-doc">
      <strong>{title}</strong>
      <span>{sub}</span>
    </div>
  )
}
