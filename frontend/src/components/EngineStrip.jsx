import { useState } from 'react'
import { C } from '../theme'
import ArchitectureModal from './ArchitectureModal'

// Atlas hybrid-search showcase: vector + lexical -> fusion -> rerank, with metadata.
export default function EngineStrip({ stats }) {
  const [showArch, setShowArch] = useState(false)
  if (!stats) return null
  const vec = stats.vector_hits ?? '—'
  const lex = stats.lexical_hits ?? 0
  const fused = stats.fused ?? '—'
  const rer = stats.reranked ?? '—'
  const dim = stats.embed_dim ?? '—'
  const embm = stats.embed_model ?? 'voyage-3'
  const rerm = stats.rerank_model ?? 'rerank-2'
  const hybrid = stats.hybrid
  const native = Boolean(stats.native)
  const levels = stats.access_levels || []
  const restrito = levels.includes('restrito')

  return (
    <div className="engine-strip">
      <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: 1.4, color: C.green, textTransform: 'uppercase' }}>
        ⚡ {native ? '1 aggregation no Atlas' : `Atlas ${hybrid ? 'Hybrid Search' : 'Vector Search'}`}
      </span>

      <span style={{ fontSize: 11, color: C.text }}>
        <span className="hint" title={`documentos retornados pelo $vectorSearch (similaridade de cosseno, ${embm})`}>
          {vec} vetoriais
        </span>
        {hybrid && (
          <>
            <span style={{ color: C.muted }}> + </span>
            <span className="hint" title="documentos retornados pelo Atlas Search léxico (BM25) — pega match exato (leis, siglas, códigos)">
              {lex} {lex === 1 ? 'léxico' : 'léxicos'}
            </span>
          </>
        )}
        <span style={{ color: C.muted }}> → </span>
        <span className="hint" title={native ? 'fusão por Reciprocal Rank Fusion feita pelo $rankFusion, dentro do Atlas' : 'candidatos únicos após Reciprocal Rank Fusion (RRF) das duas modalidades'}>
          {native ? '$rankFusion' : `${fused} fundidos${hybrid ? ' (RRF)' : ''}`}
        </span>
        <span style={{ color: C.muted }}> → </span>
        <strong className="hint" style={{ color: C.green }} title={`documentos mantidos após reordenação por relevância (${rerm} · VoyageAI${native ? ', $rerank nativo no Atlas' : ''})`}>
          {rer} reranqueados
        </strong>
      </span>

      <span style={{ fontSize: 10, color: C.sub, marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span
          className="hint"
          title={restrito ? 'Perfil restrito: inclui conteúdo restrito' : 'Perfil público: filtro de acesso aplicado no $vectorSearch + Atlas Search'}
          style={{ color: restrito ? C.green : '#F5C518', fontWeight: 700 }}
        >
          {restrito ? '🔓 acesso total' : '🔒 só público'}
        </span>
        <span>|</span>
        <span>{embm}{dim && dim !== '—' && dim !== 0 ? ` · ${dim}d` : ''}</span>
        <span>|</span>
        <span>{rerm}</span>
        {native && (
          <>
            <span className="arch-link-narrow">|</span>
            <button type="button" className="arch-link arch-link-narrow" onClick={() => setShowArch(true)}>ver arquitetura</button>
          </>
        )}
      </span>
      {showArch && (
        <ArchitectureModal stats={stats} onClose={() => setShowArch(false)} />
      )}
    </div>
  )
}
