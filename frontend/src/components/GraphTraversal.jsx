import { useEffect, useMemo, useState } from 'react'
import { layoutGraph, graphCounts, TYPE_COLORS, TYPE_LABELS } from '../graphLayout'

// Animated traversal of the Graph-RAG aggregation, looped every 11 s after a
// question, with the real subgraph of the turn (stats.graph):
//   $rankFusion finds the entry entities -> $lookup adds the first hop ->
//   $graphLookup the second (hubs are not crossed) -> $lookup gathers the
//   chunks cited by those entities/relations (plus a few from a direct hybrid
//   search, $unionWith documents) -> $rerank keeps final_n ->
//   $unionWith brings community summaries -> everything goes to the prompt.
// Before the first question a fixed placeholder graph stays still and faded.

const LOOP_MS = 11000
const STEPS = [[100, 1], [1700, 2], [3300, 3], [5000, 4], [6900, 5], [8700, 6], [10400, 0]]
const PARTICLES = 14

const DEMO = (() => {
  const nodes = [
    { id: 's1', type: 'unidade', hop: 0, seed: true, label: 'unidade' },
    { id: 's2', type: 'acao', hop: 0, seed: true, label: 'ação' },
    { id: 's3', type: 'sistema', hop: 0, seed: true, label: 'sistema' },
  ]
  const edges = []
  const types = ['acao', 'objetivo', 'acao', 'indicador', 'sistema', 'acao', 'objetivo', 'unidade', 'acao']
  types.forEach((type, i) => {
    nodes.push({ id: `a${i}`, type, hop: 1, label: type })
    edges.push({ source: `s${(i % 3) + 1}`, target: `a${i}`, type: 'relacionado_a' })
  })
  ;['objetivo', 'acao', 'norma', 'acao', 'objetivo', 'acao', 'risco', 'acao', 'sistema', 'acao', 'objetivo', 'acao'].forEach((type, i) => {
    nodes.push({ id: `b${i}`, type, hop: 2, label: type })
    edges.push({ source: `a${i % 9}`, target: `b${i}`, type: 'relacionado_a' })
  })
  edges.push({ source: 's1', target: 's2', type: 'responsavel_por' })
  return { nodes, edges }
})()

const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

const short = (text, n) => (text.length > n ? `${text.slice(0, n - 1)}…` : text)

export default function GraphTraversal({ stats, searching, size = 264, large = false }) {
  const graph = stats?.graph
  const idle = !searching && !graph?.nodes?.length
  const layout = useMemo(() => layoutGraph(idle || searching ? DEMO : graph, size), [graph, idle, searching, size])
  const counts = useMemo(() => {
    const c = graphCounts(idle || searching ? DEMO : graph)
    // The drawing may be a sample; captions use the real per-hop totals.
    const real = !idle && !searching ? graph?.hop_counts : null
    return real ? { ...c, seeds: real[0], hop1: real[1], hop2: real[2] } : c
  }, [graph, idle, searching])
  const still = reducedMotion()
  const [step, setStep] = useState(still ? 3 : 0)

  useEffect(() => {
    if (still || searching || idle) return undefined
    let timers = []
    const run = () => {
      timers = STEPS.map(([at, s]) => setTimeout(() => setStep(s), at))
      timers.push(setTimeout(run, LOOP_MS))
    }
    run()
    return () => timers.forEach(clearTimeout)
  }, [still, searching, idle, layout])

  const shown = idle ? 3 : searching ? 1 : step
  const finalN = stats?.reranked ?? stats?.final_n ?? 8
  const candidates = stats?.evidence_candidates ?? 0
  const communities = stats?.communities || []
  const H = size + 64
  const barY = size + 30

  // Chunk particles leave from the nodes that cite them (seeds and first hop first).
  const emitters = layout.nodes.filter((n) => n.hop <= 1).slice(0, PARTICLES)
  const particleCount = Math.min(PARTICLES, Math.max(emitters.length, 1), Math.max(candidates, finalN, 1))
  const particles = Array.from({ length: particleCount }, (_, i) => {
    const from = emitters[i % Math.max(emitters.length, 1)] || { x: size / 2, y: size / 2 }
    const slotX = size / 2 + (i - (particleCount - 1) / 2) * Math.min(16, (size - 40) / particleCount)
    const keep = i < Math.min(finalN, particleCount)
    return { id: i, from, slotX, keep }
  })

  const caption = idle
    ? 'Faça uma pergunta: o grafo percorrido aparece aqui.'
    : searching
    ? 'Procurando entidades de entrada no grafo…'
    : [
        'Reiniciando…',
        `$rankFusion acha ${counts.seeds} entidade(s) de entrada (vetorial + léxico)`,
        `$lookup: 1º salto traz ${counts.hop1} vizinho(s)`,
        counts.hop2 ? `$graphLookup: 2º salto traz mais ${counts.hop2}, sem atravessar hubs` : '$graphLookup: sem 2º salto útil nesta pergunta',
        `$lookup: ${candidates} trecho(s) citados pelo subgrafo${stats?.direct_candidates ? ` + ${stats.direct_candidates} da busca direta` : ''}`,
        stats?.from_direct ? `$rerank mantém ${finalN}: ${stats.from_graph} via grafo, ${stats.from_direct} só da busca direta` : `$rerank mantém ${finalN} pelo texto da pergunta`,
        communities.length ? `$unionWith: ${communities.length} tema(s) do corpus + fatos + trechos vão ao prompt` : 'fatos do grafo + trechos vão ao prompt',
      ][shown]

  const nodeVisible = (n) => shown >= 1 && (n.hop === 0 || (n.hop === 1 && shown >= 2) || (n.hop >= 2 && shown >= 3))
  const edgeVisible = (e) => shown >= 1 && e.hop <= Math.min(shown - 1, 2) && shown <= 6
  const r = large ? 7 : 5
  const seedNodes = layout.nodes.filter((n) => n.seed)
  const seedIndex = new Map(seedNodes.map((n, i) => [n.id, i + 1]))

  return (
    <div className={`gt${large ? ' large' : ''}`}>
      <svg className="gt-canvas" viewBox={`0 0 ${size} ${H}`} width={size} height={H} aria-hidden="true">
        <circle className="gt-ring" cx={size / 2} cy={size / 2} r={size * 0.31} />
        <circle className="gt-ring" cx={size / 2} cy={size / 2} r={size * 0.45} />
        {layout.edges.map((e, i) => (
          <line
            key={`e${i}`}
            className={`gt-edge${shown >= 4 && shown <= 5 ? ' hot' : ''}`}
            x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2}
            style={{ opacity: idle ? 0.12 : edgeVisible(e) ? (e.hop === 0 ? 0.9 : 0.45) : 0 }}
          />
        ))}
        {layout.nodes.map((n, i) => {
          const visible = nodeVisible(n)
          const scale = visible ? (n.seed ? 1.25 : 1) : 0.2
          return (
            <g
              key={n.id}
              className={`gt-node${searching && n.seed ? ' pulse' : ''}`}
              style={{
                transform: `translate(${n.x}px, ${n.y}px) scale(${scale})`,
                opacity: idle ? 0.3 : visible ? 1 : 0,
                transitionDelay: shown >= 1 && shown <= 3 ? `${(i % 12) * 30}ms` : '0ms',
              }}
            >
              <title>{`${n.label} · ${TYPE_LABELS[n.type] || n.type}${n.seed ? ' · ponto de entrada' : ` · ${n.hop}º salto`}`}</title>
              {n.seed && <circle r={r + 4} className="gt-halo" />}
              <circle r={r} fill={TYPE_COLORS[n.type] || '#9ea2a1'} />
              {large && n.seed && !idle && (
                <text className="gt-num" x={0} y={3.5} textAnchor="middle">{seedIndex.get(n.id)}</text>
              )}
              {large && n.hop === 1 && !idle && (
                // Outside the ring, anchored away from the centre so labels never cross the seeds.
                <text className="gt-label" x={n.x < size / 2 ? -(r + 4) : r + 4} y={3}
                  textAnchor={n.x < size / 2 ? 'end' : 'start'}>{short(n.label, 16)}</text>
              )}
            </g>
          )
        })}
        <rect className={`gt-bar${shown >= 4 && shown <= 5 ? ' on' : ''}`} x={14} y={barY - 11} width={size - 28} height={22} rx={6} />
        <text className="gt-bar-label" x={size / 2} y={barY + 24} textAnchor="middle">
          {shown === 4 ? `${candidates} trechos` : shown >= 5 ? `$rerank → ${finalN}` : 'documents'}
        </text>
        {!idle && !searching && particles.map((p) => {
          const at = shown === 4 ? { x: p.slotX, y: barY, o: 1 }
            : shown === 5 ? { x: p.slotX, y: barY, o: p.keep ? 1 : 0.12 }
            : shown === 6 ? { x: p.slotX, y: H + 6, o: 0 }
            : { x: p.from.x, y: p.from.y, o: 0 }
          return (
            <circle
              key={`p${p.id}`}
              className={`gt-chunk${p.keep ? ' keep' : ''}`}
              r={3.2}
              style={{ transform: `translate(${at.x}px, ${at.y}px)`, opacity: at.o, transitionDelay: shown === 4 ? `${p.id * 40}ms` : '0ms' }}
            />
          )
        })}
      </svg>
      <p className="gt-caption" aria-live="off">{caption}</p>
      {large && !idle && !searching && seedNodes.length > 0 && (
        <ol className="gt-seeds">
          {seedNodes.map((n) => (
            <li key={n.id}><i style={{ background: TYPE_COLORS[n.type] || '#9ea2a1' }} />{n.label}</li>
          ))}
        </ol>
      )}
      {!idle && !searching && shown === 6 && communities.length > 0 && (
        <div className="gt-communities">
          {communities.map((c) => <span key={c.title}>{c.title}</span>)}
        </div>
      )}
      <div className="gt-legend">
        {(idle || searching ? Object.keys(TYPE_COLORS) : counts.types).map((t) => (
          <span key={t}><i style={{ background: TYPE_COLORS[t] || '#9ea2a1' }} />{TYPE_LABELS[t] || t}</span>
        ))}
      </div>
    </div>
  )
}
