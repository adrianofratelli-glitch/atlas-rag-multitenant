import { C } from '../theme'

// Atlas hybrid-search showcase: vector + lexical -> RRF -> rerank-2, with metadata.
export default function EngineStrip({ stats, elapsedMs }) {
  if (!stats) return null
  const vec = stats.vector_hits ?? '—'
  const lex = stats.lexical_hits ?? 0
  const fused = stats.fused ?? '—'
  const rer = stats.reranked ?? '—'
  const dim = stats.embed_dim ?? '—'
  const idx = stats.index ?? 'vector_index'
  const embm = stats.embed_model ?? 'voyage-3'
  const rerm = stats.rerank_model ?? 'rerank-2'
  const hybrid = stats.hybrid
  const levels = stats.access_levels || []
  const restrito = levels.includes('restrito')

  return (
    <div className="engine-strip">
      <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: 1.4, color: C.green, textTransform: 'uppercase' }}>
        ⚡ Atlas {hybrid ? 'Hybrid Search' : 'Vector Search'}
      </span>

      <span style={{ fontSize: 11, color: C.text }}>
        <span className="hint" title="documentos retornados pelo $vectorSearch (similaridade de cosseno, voyage-3)">
          {vec} vetoriais
        </span>
        {hybrid && (
          <>
            <span style={{ color: C.muted }}> + </span>
            <span className="hint" title="documentos retornados pelo Atlas Search léxico (BM25) — pega match exato (leis, siglas, códigos)">
              {lex} léxicos
            </span>
          </>
        )}
        <span style={{ color: C.muted }}> → </span>
        <span className="hint" title="candidatos únicos após Reciprocal Rank Fusion (RRF) das duas modalidades">
          {fused} fundidos{hybrid ? ' (RRF)' : ''}
        </span>
        <span style={{ color: C.muted }}> → </span>
        <strong className="hint" style={{ color: C.green }} title="documentos mantidos após reordenação por relevância (rerank-2 · VoyageAI)">
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
        <span>{embm} · {dim}d</span>
        <span>|</span>
        <span>{rerm}</span>
        <span>|</span>
        <span>idx <code style={{ color: C.green }}>{idx}</code></span>
        {elapsedMs != null && (
          <>
            <span>|</span>
            <strong style={{ color: C.green }}>{elapsedMs} ms</strong>
          </>
        )}
      </span>
    </div>
  )
}
