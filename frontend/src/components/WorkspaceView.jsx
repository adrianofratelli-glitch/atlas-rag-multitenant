import { useEffect, useRef, useState } from 'react'
import Banner from '@leafygreen-ui/banner'
import { streamChat } from '../api'
import Welcome from './Welcome'
import ChatMessage from './ChatMessage'
import ChatInput from './ChatInput'
import DocumentsPanel from './DocumentsPanel'
import ArchitecturePanel from './ArchitecturePanel'
import GraphPanel from './GraphPanel'
import GraphWelcome from './GraphWelcome'

const uuid = () => {
  if (crypto.randomUUID) return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

/**
 * One isolated workspace: its own thread, messages and document scope.
 * `scope` ("base" | "uploads") is sent with every question and the backend
 * resolves it into the source list, so the two tabs never read each other's
 * corpus even when nothing is picked in the panel.
 */
export default function WorkspaceView({ config, scope, accessLevel, onStatusRefresh, panel, engine = 'hybrid', engineSwitch = false }) {
  // Uploads tab: each question can go to hybrid retrieval or to the graph of the uploaded
  // documents (built at the end of every upload). Same thread, engine chosen per question.
  const [engineSel, setEngineSel] = useState(engine)
  const activeEngine = engineSwitch ? engineSel : engine
  const graph = activeEngine === 'graph'
  const [messages, setMessages] = useState([])
  const [streaming, setStreaming] = useState(false)
  const [threadId] = useState(uuid())
  const [error, setError] = useState(null)
  const [sources, setSources] = useState([])
  const endRef = useRef(null)
  const requestRef = useRef(null)
  useEffect(() => () => requestRef.current?.abort(), [])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = async (question) => {
    if (requestRef.current) return
    const controller = new AbortController()
    requestRef.current = controller
    setError(null)
    const history = messages.map(({ role, content }) => ({ role, content }))
    setMessages((prev) => [...prev, { role: 'user', content: question }, { role: 'assistant', content: '' }])
    setStreaming(true)

    try {
      await streamChat(
        { question, messages: history, threadId, accessLevel, sources, scope, engine: activeEngine, signal: controller.signal },
        {
          onMeta: (evt) =>
            setMessages((prev) => {
              const next = [...prev]
              next[next.length - 1] = {
                ...next[next.length - 1],
                stats: evt.stats,
                sources: evt.sources,
                elapsedMs: evt.elapsed_ms,
                followups: evt.followups,
              }
              return next
            }),
          onToken: (delta) =>
            setMessages((prev) => {
              const next = [...prev]
              next[next.length - 1] = { ...next[next.length - 1], content: next[next.length - 1].content + delta }
              return next
            }),
          onDone: () => setStreaming(false),
          onError: (msg) => {
            setStreaming(false)
            setMessages((prev) => prev.slice(0, -2)) // undo the user + placeholder messages
            setError(msg)
            onStatusRefresh?.()
          },
        },
      )
    } finally {
      if (requestRef.current === controller) requestRef.current = null
      if (!controller.signal.aborted) setStreaming(false)
    }
  }

  const last = messages[messages.length - 1]
  const showFollowups = !streaming && last?.role === 'assistant' && last.followups?.length > 0
  const scopeLabel = sources.length ? sources.join(', ') : panel.subject
  const lastAnswer = [...messages].reverse().find((m) => m.role === 'assistant' && m.stats)
  const phase = streaming ? (last?.stats ? 'generating' : 'retrieving') : lastAnswer ? 'done' : 'idle'

  return (
    <div className={config.native ? 'ws-layout' : undefined}>
    <div className="ws-chat">
      {error && (
        <Banner darkMode variant="danger" dismissible onClose={() => setError(null)} style={{ marginBottom: 14 }}>
          {error}
        </Banner>
      )}

      {engineSwitch && config.native && (
        <div className="graph-modes" role="radiogroup" aria-label="Motor de recuperação">
          {[['hybrid', 'RAG híbrido'], ['graph', 'Híbrido + grafo do upload']].map(([id, label]) => (
            <button key={id} type="button" role="radio" aria-checked={engineSel === id}
              className={engineSel === id ? 'active' : undefined} disabled={streaming} onClick={() => setEngineSel(id)}>
              {label}
            </button>
          ))}
          <span className="graph-modes-note">o grafo de cada upload é montado no fim da ingestão e expira com ele</span>
        </div>
      )}

      {(!graph || scope === 'uploads') && <DocumentsPanel
        workspace={scope}
        readOnly={panel.readOnly}
        title={panel.title}
        emptyLabel={panel.emptyLabel}
        selected={sources}
        onSelected={setSources}
        onCorpusChange={onStatusRefresh}
        maxUploadMb={config.max_upload_mb}
        formats={config.supported_formats}
        ttlHours={config.upload_ttl_hours}
      />}

      {messages.length === 0 ? (
        graph && scope !== 'uploads' ? <GraphWelcome config={config} onPick={send} /> : <Welcome config={panel.welcome || config} onPick={send} />
      ) : (
        messages.map((m, i) => <ChatMessage key={i} msg={m} />)
      )}

      {showFollowups && (
        <div style={{ marginTop: 6 }}>
          <div className="sb-section-label" style={{ marginLeft: 0 }}>Perguntas relacionadas</div>
          <div className="sugg-grid">
            {last.followups.map((fq, i) => (
              <button key={i} className="sugg-card" onClick={() => send(fq)}>
                <span className="sugg-num">Continuar {String(i + 1).padStart(2, '0')}</span>
                <span className="sugg-text">{fq}</span>
                <span className="sugg-arrow">→</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div ref={endRef} />
      <ChatInput placeholder={graph && scope !== 'uploads' ? 'Pergunte sobre o que liga unidades, ações, objetivos e sistemas…' : `Pergunte sobre ${scopeLabel}…`} disabled={streaming} onSend={send} />
    </div>
    {config.native && (
      (streaming ? graph : lastAnswer ? lastAnswer.stats?.engine === 'graph' : graph)
        ? <GraphPanel phase={phase} stats={streaming ? last?.stats : lastAnswer?.stats} />
        : <ArchitecturePanel
            phase={phase}
            stats={streaming ? last?.stats : lastAnswer?.stats}
            sources={streaming ? last?.sources : lastAnswer?.sources}
          />
    )}
    </div>
  )
}
