import { useEffect, useRef, useState } from 'react'
import Banner from '@leafygreen-ui/banner'
import { streamChat } from '../api'
import Welcome from './Welcome'
import ChatMessage from './ChatMessage'
import ChatInput from './ChatInput'
import DocumentsPanel from './DocumentsPanel'

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
export default function WorkspaceView({ config, scope, accessLevel, onStatusRefresh, panel }) {
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
        { question, messages: history, threadId, accessLevel, sources, scope, signal: controller.signal },
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

  return (
    <>
      {error && (
        <Banner darkMode variant="danger" dismissible onClose={() => setError(null)} style={{ marginBottom: 14 }}>
          {error}
        </Banner>
      )}

      <DocumentsPanel
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
      />

      {messages.length === 0 ? (
        <Welcome config={panel.welcome || config} onPick={send} />
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
      <ChatInput placeholder={`Pergunte sobre ${scopeLabel}…`} disabled={streaming} onSend={send} />
    </>
  )
}
