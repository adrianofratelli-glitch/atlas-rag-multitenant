// Radial layout for the traversed subgraph: entry points (hop 0) in the
// centre, first hop on the middle ring, second hop on the outer ring. Each
// neighbour sits near the angle of the node that reached it, so the drawing
// reads as "these seeds pulled in these neighbours". Pure and deterministic:
// the same `graph` stats always produce the same picture.

export const TYPE_COLORS = {
  unidade: '#3d9dff',
  acao: '#00ED64',
  objetivo: '#ffad00',
  sistema: '#d959ea',
  norma: '#71F6A6',
  indicador: '#cdd5ce',
  risco: '#FF6960',
}

export const TYPE_LABELS = {
  unidade: 'unidade',
  acao: 'ação',
  objetivo: 'objetivo',
  sistema: 'sistema',
  norma: 'norma',
  indicador: 'indicador',
  risco: 'risco',
}

const TAU = Math.PI * 2

function adjacency(edges) {
  const adj = new Map()
  const add = (a, b) => {
    if (!adj.has(a)) adj.set(a, [])
    adj.get(a).push(b)
  }
  edges.forEach((e) => {
    add(e.source, e.target)
    add(e.target, e.source)
  })
  return adj
}

export function layoutGraph(graph, size = 256) {
  const nodes = graph?.nodes || []
  const edges = graph?.edges || []
  const c = size / 2
  const rings = [size * 0.13, size * 0.31, size * 0.45]
  const adj = adjacency(edges)
  const byHop = [0, 1, 2].map((h) => nodes.filter((n) => Math.min(n.hop, 2) === h))
  const pos = new Map()

  const seeds = byHop[0]
  seeds.forEach((n, i) => {
    const angle = -Math.PI / 2 + (TAU * i) / Math.max(seeds.length, 1)
    const r = seeds.length === 1 ? 0 : rings[0]
    pos.set(n.id, { x: c + r * Math.cos(angle), y: c + r * Math.sin(angle), angle, parent: null })
  })

  // Each ring is split into equal sectors, one per parent of the ring before;
  // children spread inside their parent's sector.
  for (let hop = 1; hop <= 2; hop++) {
    const ring = byHop[hop]
    if (!ring.length) continue
    const previous = byHop[hop - 1].filter((n) => pos.has(n.id))
    const groups = new Map(previous.map((p) => [p.id, []]))
    const orphans = []
    ring.forEach((n) => {
      const parent = (adj.get(n.id) || []).find((id) => groups.has(id))
      if (parent) groups.get(parent).push(n)
      else orphans.push(n)
    })
    const ordered = [...groups.entries()].filter(([, kids]) => kids.length)
    if (orphans.length) ordered.push([null, orphans])
    const total = ring.length
    let cursor = ordered.length && ordered[0][0] ? pos.get(ordered[0][0]).angle - (TAU * ordered[0][1].length) / total / 2 : -Math.PI / 2
    ordered.forEach(([parentId, kids]) => {
      const span = (TAU * kids.length) / total
      kids.forEach((n, i) => {
        const angle = cursor + (span * (i + 0.5)) / kids.length
        pos.set(n.id, {
          x: c + rings[hop] * Math.cos(angle),
          y: c + rings[hop] * Math.sin(angle),
          angle,
          parent: parentId,
        })
      })
      cursor += span
    })
  }

  const laidNodes = nodes.filter((n) => pos.has(n.id)).map((n) => ({ ...n, ...pos.get(n.id) }))
  const hopOf = new Map(laidNodes.map((n) => [n.id, Math.min(n.hop, 2)]))
  const laidEdges = edges
    .filter((e) => pos.has(e.source) && pos.has(e.target))
    .map((e) => {
      const a = pos.get(e.source)
      const b = pos.get(e.target)
      return { ...e, x1: a.x, y1: a.y, x2: b.x, y2: b.y, hop: Math.max(hopOf.get(e.source), hopOf.get(e.target)) }
    })
  return { nodes: laidNodes, edges: laidEdges, size }
}

/** Counts shown next to the drawing: how much of the subgraph each step adds. */
export function graphCounts(graph) {
  const nodes = graph?.nodes || []
  return {
    seeds: nodes.filter((n) => n.hop === 0).length,
    hop1: nodes.filter((n) => n.hop === 1).length,
    hop2: nodes.filter((n) => n.hop >= 2).length,
    edges: (graph?.edges || []).length,
    types: [...new Set(nodes.map((n) => n.type))],
  }
}

/** Overlap between two answers' source cards, keyed by document + page + preview. */
export function sourceOverlap(a = [], b = []) {
  const key = (s) => `${s.source}|${s.page}|${(s.preview || '').slice(0, 60)}`
  const kb = new Set(b.map(key))
  const shared = a.filter((s) => kb.has(key(s))).length
  const docs = (list) => [...new Set(list.map((s) => s.source).filter(Boolean))]
  return { shared, docsA: docs(a), docsB: docs(b) }
}
