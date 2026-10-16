import { useEffect, useRef, useState } from 'react'
import Banner from '@leafygreen-ui/banner'
import { streamChat } from '../api'
import { sourceOverlap } from '../graphLayout'
import ChatMessage from './ChatMessage'
import ChatInput from './ChatInput'
import GraphWelcome from './GraphWelcome'
import GraphPanel from './GraphPanel'

const SIDES = [
  { engine: 'hybrid', label: 'RAG híbrido (a base)', sub: '$rankFusion + $rerank sobre chunks' },
  { engine: 'graph', label: 'Híbrido + grafo', sub: 'a mesma busca + $graphLookup sobre entidades' },
]

const empty = () => ({ content: '', stats: null, sources: null, status: 'streaming', error: null })

/**
 * Side by side: every question goes to both engines at once, over the same
 * reference corpus and the same Claude. Each question is independent (no
 * history), so the two answers differ only by retrieval.
 */
export default function CompareView({ config, accessLevel, onStatusRefresh }) {
  const [rows, setRows] = useState([])
  const [error, setError] = useState(null)
  const controllers = useRef(new Set())
  const endRef = useRef(null)
  useEffect(() => () => controllers.current.forEach((c) => c.abort()), [])
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [rows.length])

  const busy = rows.some((r) => SIDES.some((s) => r[s.engine].status === 'streaming'))

  const patch = (id, engine, fn) => setRows((prev) => prev.map((r) => (r.id === id ? { ...r, [engine]: fn(r[engine]) } : r)))

  const send = (question) => {
    if (busy) return
    setError(null)
    const id = `${Date.now()}`
    setRows((prev) => [...prev, { id, question, hybrid: empty(), graph: empty() }])
    SIDES.forEach(({ engine }) => {
      const controller = new AbortController()
      controllers.current.add(controller)
      streamChat(
        { question, messages: [], threadId: null, accessLevel, sources: [], scope: 'base', engine, signal: controller.signal },
        {
          onMeta: (evt) => patch(id, engine, (s) => ({ ...s, stats: evt.stats, sources: evt.sources })),
          onToken: (delta) => patch(id, engine, (s) => ({ ...s, content: s.content + delta })),
          onDone: () => patch(id, engine, (s) => ({ ...s, status: 'done' })),
          onError: (msg) => {
            patch(id, engine, (s) => ({ ...s, status: 'error', error: msg }))
            onStatusRefresh?.()
          },
        },
      ).finally(() => {
        controllers.current.delete(controller)
        patch(id, engine, (s) => (s.status === 'streaming' ? { ...s, status: 'done' } : s))
      })
    })
  }

  const last = rows[rows.length - 1]
  const graphSide = last?.graph
  const phase = !graphSide ? 'idle' : graphSide.status === 'streaming' ? (graphSide.stats ? 'generating' : 'retrieving') : 'done'

  return (
    <div className="ws-layout">
      <div className="ws-chat">
        {error && (
          <Banner darkMode variant="danger" dismissible onClose={() => setError(null)} style={{ marginBottom: 14 }}>{error}</Banner>
        )}
        {rows.length === 0 ? (
          <GraphWelcome config={config} onPick={send} compare />
        ) : (
          rows.map((row) => (
            <section key={row.id} className="cmp-row" aria-label={`Comparação: ${row.question}`}>
              <h3 className="cmp-question">{row.question}</h3>
              <div className="cmp-cols">
                {SIDES.map((side) => {
                  const s = row[side.engine]
                  return (
                    <div key={side.engine} className={`cmp-side ${side.engine}`}>
                      <div className="cmp-side-head">
                        <strong>{side.label}</strong><span>{side.sub}</span>
                      </div>
                      {s.status === 'error' ? (
                        <Banner darkMode variant="danger">{s.error}</Banner>
                      ) : (
                        <ChatMessage msg={{ role: 'assistant', content: s.content, stats: s.stats, sources: s.sources }} />
                      )}
                    </div>
                  )
                })}
              </div>
              <CompareMetrics row={row} />
            </section>
          ))
        )}
        <div ref={endRef} />
        <ChatInput placeholder="Uma pergunta, as duas recuperações…" disabled={busy} onSend={send} />
      </div>
      <GraphPanel phase={phase} stats={graphSide?.stats} />
    </div>
  )
}

function CompareMetrics({ row }) {
  const h = row.hybrid
  const g = row.graph
  if (!h.stats || !g.stats) return null
  const overlap = sourceOverlap(h.sources || [], g.sources || [])
  const graphDocs = g.stats.documents_covered || []
  const items = [
    ['Trechos citáveis no prompt', h.sources?.length ?? 0, g.sources?.length ?? 0],
    ['Documentos nas fontes', overlap.docsA.length, overlap.docsB.length],
    ['Contexto estruturado', '—', g.stats.graph_degraded ? 'grafo indisponível' : `${g.stats.graph_nodes} entidades · ${g.stats.facts} relações`],
    ['Documentos alcançados', '—', graphDocs.length || '—'],
    ['Temas do corpus (comunidades)', '—', g.stats.communities?.length || '—'],
  ]
  return (
    <div className="cmp-metrics" role="table" aria-label="Diferenças entre as recuperações">
      <div role="row" className="cmp-metrics-head">
        <span role="columnheader">o que foi ao prompt</span><span role="columnheader">híbrido</span><span role="columnheader">híbrido + grafo</span>
      </div>
      {items.map(([label, a, b]) => (
        <div role="row" key={label}><span role="cell">{label}</span><b role="cell">{a}</b><b role="cell" className="g">{b}</b></div>
      ))}
      <p className="cmp-note">{overlap.shared} trecho(s) em comum entre as duas listas de fontes.</p>
    </div>
  )
}
