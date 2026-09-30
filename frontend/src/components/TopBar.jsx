import Button from '@leafygreen-ui/button'
import Icon from '@leafygreen-ui/icon'
import { C } from '../theme'

export default function TopBar({ config, online, onRefresh, onNewChat, accessLevel, onAccessLevel }) {
  return (
    <div className="top-nav">
      <span style={{ fontSize: 18, fontWeight: 800, letterSpacing: '-0.02em', color: C.text }}>
        RAG híbrido
      </span>
      <span style={{ fontFamily: 'var(--mono)', fontSize: 12, color: C.muted }}>{config.document_title}</span>

      <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
        <span className="access-switch" aria-label="Perfil de acesso">
          {['publico', 'restrito'].map((value) => <button key={value} aria-pressed={accessLevel === value} onClick={() => onAccessLevel(value)}>{value}</button>)}
        </span>
        <span className={`status-pill ${online ? 'on' : 'off'}`}>
          <span className="status-dot" />
          {online ? 'Atlas Online' : 'Atlas Offline'}
        </span>
        <Button darkMode size="small" onClick={onNewChat} leftGlyph={<Icon glyph="Plus" />}>
          Nova conversa
        </Button>
        {!online && <Button darkMode size="small" onClick={onRefresh} leftGlyph={<Icon glyph="Refresh" />}>Reconectar</Button>}
      </span>
    </div>
  )
}
