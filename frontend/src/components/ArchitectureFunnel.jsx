import { useEffect, useMemo, useState } from 'react'

// Animated funnel of the native aggregation, looped every 10 s:
// each branch returns its candidates -> $rankFusion merges them (a chunk found
// by both branches becomes one) -> $rerank reorders -> $limit lets only the
// final ones through -> they go to the prompt.
// Sizes come from the turn's stats (branch_limit, rerank_candidates, final_n)
// and the final chunks are coloured by their real matched_by. Candidates that
// did not make the cut are not returned by the pipeline, so their overlap is
// unknown: they are drawn as distinct chunks.

const W = 256
const PITCH = 12
const LOOP_MS = 10000
const STEPS = [[100, 1], [2000, 2], [4000, 3], [6000, 4], [8000, 5], [9300, 0]]
// Shown before the first question; the caption says it is an example.
const DEMO_FINAL = ['both', 'both', 'vector', 'both', 'lexical', 'both', 'vector', 'both']

const kindOf = (matchedBy = []) => {
  const v = matchedBy.includes('vetorial')
  const l = matchedBy.includes('léxico')
  return v && l ? 'both' : l ? 'lexical' : 'vector'
}

const grid = (i, cols, x0, y0) => ({ x: x0 + (i % cols) * PITCH, y: y0 + Math.floor(i / cols) * PITCH })

function buildModel(stats, sources) {
  const k = stats?.branch_limit || 15
  const finalN = stats?.final_n || 8
  const rerankN = stats?.rerank_candidates || 30
  const hybrid = stats ? stats.hybrid !== false : true
  const finals = stats && sources?.length ? sources.map((s) => kindOf(s.matched_by)) : DEMO_FINAL
  const lexK = hybrid ? k : 0

  // Unique chunks after fusion, final ones first (already in rerank order).
  const entities = []
  let v = 0
  let l = 0
  finals.forEach((kind) => {
    const parts = []
    if (kind !== 'lexical' && v < k) parts.push(`v${v++}`)
    if (kind !== 'vector' && l < lexK) parts.push(`l${l++}`)
    if (parts.length) entities.push({ parts, final: true })
  })
  const rest = []
  for (; v < k; v++) rest.push({ parts: [`v${v}`], final: false })
  for (; l < lexK; l++) rest.push({ parts: [`l${l}`], final: false })
  // Interleave so the fused grid is not "all green then all blue".
  rest.sort((a, b) => (a.parts[0].slice(1) - b.parts[0].slice(1)) || a.parts[0].localeCompare(b.parts[0]))
  const fusedOrder = [...entities, ...rest]
  // Fusion order differs from rerank order: spread the finals through the grid.
  const fusionSlots = fusedOrder.map((_, i) => i)
  for (let i = fusionSlots.length - 1; i > 0; i--) {
    const j = (i * 7 + 3) % (i + 1)
    ;[fusionSlots[i], fusionSlots[j]] = [fusionSlots[j], fusionSlots[i]]
  }

  const dots = {}
  for (let i = 0; i < k; i++) dots[`v${i}`] = { id: `v${i}`, kind: 'vector', branch: grid(i, 5, 20, 22) }
  for (let i = 0; i < lexK; i++) dots[`l${i}`] = { id: `l${i}`, kind: 'lexical', branch: grid(i, 5, W - 20 - 4 * PITCH - 9, 22) }

  const limitX0 = (W - (finals.length - 1) * 22 - 9) / 2
  fusedOrder.forEach((entity, rank) => {
    const merged = entity.parts.length > 1
    const fusion = grid(fusionSlots[rank], 10, 70, 96)
    const rerank = grid(rank, 10, 70, 162)
    const limit = { x: limitX0 + rank * 22, y: 236 }
    entity.parts.forEach((id) => Object.assign(dots[id], { merged, final: entity.final, fusion, rerank, limit }))
  })

  return {
    k, lexK, finalN: finals.length || finalN, rerankN: Math.min(rerankN, fusedOrder.length),
    merged: entities.filter((e) => e.parts.length > 1).length,
    dots: Object.values(dots), demo: !(stats && sources?.length),
  }
}

function place(dot, step) {
  switch (step) {
    case 1: return { ...dot.branch, o: 1, s: 1 }
    case 2: return { ...dot.fusion, o: 1, s: 1 }
    case 3: return { ...dot.rerank, o: dot.final ? 1 : 0.5, s: dot.final ? 1.15 : 1 }
    case 4: return dot.final ? { ...dot.limit, o: 1, s: 1.6 } : { ...dot.rerank, o: 0, s: 0.6 }
    case 5: return dot.final ? { x: dot.limit.x, y: 290, o: 0, s: 1.6 } : { ...dot.rerank, o: 0, s: 0.6 }
    default: return { ...dot.branch, o: 0, s: 0.4 }
  }
}

const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export default function ArchitectureFunnel({ stats, sources, searching, rerankModel }) {
  const model = useMemo(() => buildModel(stats, sources), [stats, sources])
  const still = reducedMotion()
  const [step, setStep] = useState(still ? 4 : 0)

  useEffect(() => {
    if (still || searching) return undefined
    let timers = []
    const run = () => {
      timers = STEPS.map(([at, s]) => setTimeout(() => setStep(s), at))
      timers.push(setTimeout(run, LOOP_MS))
    }
    run()
    return () => timers.forEach(clearTimeout)
  }, [still, searching, model])

  const shown = searching ? 1 : step
  const caption = searching
    ? 'Os dois ramos buscam em paralelo…'
    : [
        'Reiniciando…',
        `Cada ramo devolve ${model.k} candidatos`,
        model.merged ? `$rankFusion funde por posição: ${model.merged} chunk(s) achados pelos dois ramos viram um` : '$rankFusion funde os dois rankings por posição',
        `$rerank reordena até ${model.rerankN} pelo texto da pergunta`,
        `$limit ${model.finalN}: só ${model.finalN} passam`,
        `${model.finalN} chunks seguem para o prompt do Claude`,
      ][shown]

  return (
    <div className="af">
      <div className="af-canvas" aria-hidden="true">
        <span className={`af-label left ${shown === 1 ? 'on' : ''}`} style={{ top: 0 }}>$vectorSearch · {model.k}</span>
        {model.lexK > 0 && <span className={`af-label right ${shown === 1 ? 'on' : ''}`} style={{ top: 0 }}>$search · {model.lexK}</span>}
        <span className={`af-label left ${shown === 2 ? 'on' : ''}`} style={{ top: 78 }}>$rankFusion</span>
        <span className={`af-label left ${shown === 3 ? 'on' : ''}`} style={{ top: 144 }}>$rerank · {rerankModel}</span>
        <span className={`af-label left ${shown >= 4 ? 'on' : ''}`} style={{ top: 214 }}>$limit {model.finalN}</span>
        <span className="af-rule" style={{ top: 70 }} />
        <span className="af-rule" style={{ top: 136 }} />
        <span className="af-rule" style={{ top: 206 }} />
        {model.dots.map((dot, i) => {
          const p = place(dot, shown)
          return (
            <span
              key={dot.id}
              className={`af-dot ${dot.kind}${dot.merged && shown >= 2 && dot.kind === 'lexical' ? ' half' : ''}${searching ? ' pulse' : ''}${shown === 0 ? ' reset' : ''}`}
              style={{
                transform: `translate(${p.x}px, ${p.y}px) scale(${p.s})`,
                opacity: p.o,
                transitionDelay: shown === 1 ? `${i * 18}ms` : shown === 2 ? `${(i % 10) * 25}ms` : '0ms',
              }}
            />
          )
        })}
      </div>
      <p className="af-caption" aria-live="off">{caption}</p>
      <div className="af-legend">
        <span><i className="af-dot vector static" />vetorial</span>
        <span><i className="af-dot lexical static" />léxico</span>
        <span><i className="af-dot vector static both" />os dois</span>
        {model.demo && !searching && <em>exemplo</em>}
      </div>
    </div>
  )
}
