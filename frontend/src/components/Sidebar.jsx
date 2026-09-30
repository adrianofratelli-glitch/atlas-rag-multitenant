import { useState } from 'react'
import Card from '@leafygreen-ui/card'
import Badge from '@leafygreen-ui/badge'
import Button from '@leafygreen-ui/button'
import TextInput from '@leafygreen-ui/text-input'
import Icon from '@leafygreen-ui/icon'
import { MongoDBLogoMark } from '@leafygreen-ui/logo'
import { C, greenLo, greenBd } from '../theme'

function Mono({ children }) {
  return (
    <span
      style={{
        width: 22, height: 22, borderRadius: 6, flexShrink: 0, display: 'flex',
        alignItems: 'center', justifyContent: 'center', background: greenLo,
        border: `1px solid ${greenBd}`, fontFamily: 'var(--mono)', fontSize: 10,
        fontWeight: 700, color: C.green,
      }}
    >
      {children}
    </span>
  )
}

function download(name, content, type) {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

export default function Sidebar({ config, status, threadId, totalQueries, chunksRead, messages, onResume, onNewChat, accessLevel, onAccessLevel }) {
  const [resumeId, setResumeId] = useState('')
  const online = status.online
  const slug = (threadId || '').slice(0, 8)

  const exportTxt = () => {
    const txt =
      `${config.client_name} — ${config.document_title}\nSession: ${threadId}\n` +
      `${'─'.repeat(50)}\n\n` +
      messages.map((m) => `[${m.role === 'user' ? 'Usuário' : 'Assistente'}]\n${m.content}\n\n`).join('')
    download(`conversa_${slug}.txt`, txt, 'text/plain')
  }
  const exportJson = () => {
    download(
      `conversa_${slug}.json`,
      JSON.stringify({ session_id: threadId, client: config.client_name, document: config.document_title, messages }, null, 2),
      'application/json',
    )
  }

  return (
    <aside className="sidebar">
      {/* Marca + selo POC */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ width: 42, height: 42, borderRadius: 11, display: 'flex', alignItems: 'center', justifyContent: 'center', background: greenLo, border: `1px solid ${greenBd}`, boxShadow: '0 0 22px rgba(0,237,100,0.18)' }}>
          <MongoDBLogoMark height={24} />
        </div>
        <div>
          <div style={{ fontSize: 17, fontWeight: 800, color: C.text, lineHeight: 1.05 }}>{config.client_name}</div>
          <div style={{ fontSize: 9, color: C.green, textTransform: 'uppercase', letterSpacing: 2, fontFamily: 'var(--mono)', fontWeight: 600, marginTop: 3 }}>
            MongoDB Atlas
          </div>
        </div>
      </div>
      <div style={{ marginTop: 14, padding: '9px 12px', borderRadius: 8, background: 'linear-gradient(100deg, rgba(0,237,100,0.16), rgba(0,237,100,0.03))', border: `1px solid ${greenBd}`, display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="status-dot" style={{ background: C.green, boxShadow: `0 0 8px ${C.green}` }} />
        <div>
          <div style={{ fontSize: 10, fontWeight: 800, color: C.green, letterSpacing: 1.6, textTransform: 'uppercase', fontFamily: 'var(--mono)' }}>Proof of Concept</div>
          <div style={{ fontSize: 9, color: C.sub }}>Powered by MongoDB Atlas Vector Search</div>
        </div>
      </div>

      <div className="sb-divider" />

      {/* Access profile (ACL) */}
      <div className="sb-section-label" style={{ marginTop: 0 }}>Perfil de acesso</div>
      <div style={{ display: 'flex', gap: 8 }}>
        {[
          ['publico', 'Público'],
          ['restrito', 'Restrito'],
        ].map(([val, label]) => {
          const active = accessLevel === val
          return (
            <button
              key={val}
              onClick={() => onAccessLevel?.(val)}
              aria-pressed={active}
              style={{
                flex: 1, cursor: 'pointer', padding: '7px 0', borderRadius: 7,
                fontFamily: 'var(--mono)', fontSize: 11, fontWeight: 700,
                background: active ? greenLo : 'transparent',
                color: active ? C.green : C.sub,
                border: `1px solid ${active ? greenBd : 'rgba(255,255,255,0.08)'}`,
              }}
            >
              {label}
            </button>
          )
        })}
      </div>
      <div style={{ fontSize: 10, color: C.muted, marginTop: 6, lineHeight: 1.5 }}>
        {accessLevel === 'publico'
          ? 'Recupera apenas conteúdo público (filtro no $vectorSearch + Atlas Search).'
          : 'Acesso total: inclui conteúdo restrito (segurança, financeiro).'}
      </div>

      <div className="sb-divider" />

      {/* Document */}
      <Card darkMode style={{ padding: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <span style={{ fontSize: 12, fontWeight: 700, letterSpacing: 1.4, textTransform: 'uppercase', color: C.text }}>Document</span>
          <Badge variant={online ? 'green' : 'red'}>{online ? 'Live' : 'Offline'}</Badge>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>
            {(config.document_title || '').slice(0, 20)}{' '}
            <span style={{ color: C.sub }}>{online && status.chunks != null ? `${status.chunks} chunks` : '— chunks'}</span>
          </span>
          <Badge variant="green">Vector</Badge>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingTop: 8 }}>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{config.db_name}</span>
          <Badge variant="green">Atlas</Badge>
        </div>
      </Card>

      {/* Connection */}
      <div style={{ marginTop: 14, background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, padding: '10px 14px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: C.sub }}>
          <span>Conexão</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontWeight: 700, color: online ? C.green : C.red }}>
            <span className="status-dot" style={{ background: online ? C.green : C.red, boxShadow: `0 0 6px ${online ? C.green : C.red}` }} />
            {online ? 'Online' : 'Offline'}
          </span>
        </div>
      </div>

      {/* KPIs */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 14 }}>
        <Card darkMode style={{ padding: '12px 14px', borderTop: `3px solid ${C.green}`, borderTopLeftRadius: 0, borderTopRightRadius: 0 }}>
          <div className="kpi-label">Perguntas</div>
          <div className="kpi-value" style={{ color: C.green }}>{totalQueries}</div>
          <div className="kpi-sub">na sessão</div>
        </Card>
        <Card darkMode style={{ padding: '12px 14px', borderTop: `3px solid ${C.greenMed}`, borderTopLeftRadius: 0, borderTopRightRadius: 0 }}>
          <div className="kpi-label">Chunks</div>
          <div className="kpi-value" style={{ color: C.mint }}>{chunksRead}</div>
          <div className="kpi-sub">lidos</div>
        </Card>
      </div>

      {/* Thread ID + retomar */}
      <div className="sb-section-label">Thread ID</div>
      <div style={{ background: 'rgba(0,0,0,0.3)', border: `1px solid ${greenBd}`, borderRadius: 7, padding: '8px 10px', fontFamily: 'var(--mono)', fontSize: 9.5, color: C.green, wordBreak: 'break-all', marginBottom: 10 }}>
        {threadId}
      </div>
      <span id="resume-label" className="sr-only">Retomar sessão pelo Thread ID</span>
      <TextInput darkMode aria-labelledby="resume-label" placeholder="cole o Thread ID aqui" value={resumeId} onChange={(e) => setResumeId(e.target.value)} sizeVariant="small" />
      <div style={{ height: 8 }} />
      <Button darkMode size="small" onClick={() => resumeId.trim().length > 10 && onResume(resumeId.trim())} style={{ width: '100%' }}>
        Retomar Sessão
      </Button>

      <div className="sb-divider" />

      {/* Exportar */}
      <div className="sb-section-label" style={{ marginTop: 0 }}>Exportar Conversa</div>
      {messages.length > 0 ? (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          <Button darkMode size="small" onClick={exportTxt} leftGlyph={<Icon glyph="Download" />}>TXT</Button>
          <Button darkMode size="small" onClick={exportJson} leftGlyph={<Icon glyph="Download" />}>JSON</Button>
        </div>
      ) : (
        <div style={{ fontSize: 11, color: C.sub }}>Inicie uma conversa para exportar.</div>
      )}

      <div className="sb-divider" />

      {/* Stack */}
      <div className="sb-section-label" style={{ marginTop: 0 }}>Stack</div>
      {[
        [<MongoDBLogoMark key="l" height={13} />, 'MongoDB Atlas', 'Vector Search · Checkpointer'],
        ['V', 'VoyageAI', `${config.embed_model} · ${config.rerank_model}`],
        ['C', 'Claude Sonnet', 'Streaming LLM'],
      ].map(([mark, name, sub], i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 8 }}>
          <Mono>{mark}</Mono>
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: C.text }}>{name}</div>
            <div style={{ fontSize: 10, color: C.muted }}>{sub}</div>
          </div>
        </div>
      ))}

      <div className="sb-divider" />

      {/* Cluster + Nova conversa */}
      <div style={{ background: greenLo, border: `1px solid ${greenBd}`, borderRadius: 8, padding: '10px 14px', marginBottom: 10 }}>
        <div style={{ fontSize: 9, color: C.muted, textTransform: 'uppercase', letterSpacing: 1.5, fontFamily: 'var(--mono)', marginBottom: 4 }}>Cluster</div>
        <div style={{ fontSize: 13, fontWeight: 700, color: C.green, fontFamily: 'var(--mono)' }}>{config.db_name} · Atlas</div>
      </div>
      <Button darkMode onClick={onNewChat} style={{ width: '100%' }} leftGlyph={<Icon glyph="Plus" />}>Nova Conversa</Button>
    </aside>
  )
}
