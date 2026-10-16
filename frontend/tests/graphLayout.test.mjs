import { test } from 'node:test'
import assert from 'node:assert/strict'
import { layoutGraph, graphCounts, sourceOverlap } from '../src/graphLayout.js'

const graph = {
  nodes: [
    { id: 's1', type: 'unidade', hop: 0, seed: true, label: 'CES' },
    { id: 's2', type: 'acao', hop: 0, seed: true, label: 'AG56' },
    { id: 'a', type: 'objetivo', hop: 1, label: 'ON02' },
    { id: 'b', type: 'sistema', hop: 1, label: 'SIPEMA' },
    { id: 'c', type: 'acao', hop: 2, label: 'AG57' },
    { id: 'orphan', type: 'norma', hop: 1, label: 'LGPD' },
  ],
  edges: [
    { source: 's1', target: 's2', type: 'responsavel_por' },
    { source: 's2', target: 'a', type: 'contribui_para' },
    { source: 'b', target: 's1', type: 'relacionado_a' },
    { source: 'a', target: 'c', type: 'relacionado_a' },
    { source: 'x', target: 'a', type: 'relacionado_a' },
  ],
}

const dist = (n, size) => Math.hypot(n.x - size / 2, n.y - size / 2)

test('places each hop on its own ring', () => {
  const size = 200
  const { nodes } = layoutGraph(graph, size)
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]))
  assert.ok(Math.abs(dist(byId.s1, size) - size * 0.13) < 0.01)
  assert.ok(Math.abs(dist(byId.a, size) - size * 0.31) < 0.01)
  assert.ok(Math.abs(dist(byId.c, size) - size * 0.45) < 0.01)
  assert.equal(byId.orphan.parent, null) // no edge to a seed: still drawn
  assert.equal(byId.a.parent, 's2')
  assert.equal(byId.b.parent, 's1')
})

test('single seed sits in the centre', () => {
  const { nodes } = layoutGraph({ nodes: [{ id: 's', type: 'acao', hop: 0 }], edges: [] }, 100)
  assert.deepEqual([nodes[0].x, nodes[0].y], [50, 50])
})

test('drops edges with an endpoint outside the drawing and tags the hop of each edge', () => {
  const { edges } = layoutGraph(graph, 200)
  assert.equal(edges.length, 4)
  assert.deepEqual(edges.map((e) => e.hop), [0, 1, 1, 2])
})

test('is deterministic', () => {
  assert.deepEqual(layoutGraph(graph, 200), layoutGraph(graph, 200))
})

test('empty graph is safe', () => {
  assert.deepEqual(layoutGraph(null, 100).nodes, [])
  assert.deepEqual(graphCounts(undefined), { seeds: 0, hop1: 0, hop2: 0, edges: 0, types: [] })
})

test('counts per hop', () => {
  assert.deepEqual(graphCounts(graph), { seeds: 2, hop1: 3, hop2: 1, edges: 5, types: ['unidade', 'acao', 'objetivo', 'sistema', 'norma'] })
})

test('source overlap by document, page and preview', () => {
  const a = [{ source: 'A', page: 1, preview: 'x' }, { source: 'B', page: 2, preview: 'y' }]
  const b = [{ source: 'A', page: 1, preview: 'x' }, { source: 'C', page: 3, preview: 'z' }]
  assert.deepEqual(sourceOverlap(a, b), { shared: 1, docsA: ['A', 'B'], docsB: ['A', 'C'] })
})
