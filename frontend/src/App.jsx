import { useEffect, useState } from 'react'
import Banner from '@leafygreen-ui/banner'
import { getConfig, getStatus } from './api'
import TopBar from './components/TopBar'
import OfflineHero from './components/OfflineHero'
import WorkspaceView from './components/WorkspaceView'
import { C } from './theme'

const OFFLINE_CONFIG = {
  client_name: 'RAG PoC',
  document_title: 'documento configurado',
  document_description: 'Assistente documental com recuperação híbrida.',
  db_name: 'indisponível',
  questions: [],
  embed_model: 'Voyage AI',
  rerank_model: 'Voyage AI',
}

/**
 * Two isolated workspaces over the same tenant DB. The reference corpus tab is
 * read-only; everything uploaded during a demo lands in the second tab. Neither
 * retrieves the other's documents — the backend enforces it from `scope`.
 */
const TABS = [
  {
    id: 'base',
    label: 'Corpus de referência',
    panel: {
      readOnly: true,
      title: 'Documentos do tenant',
      emptyLabel: 'Nenhum documento de referência indexado. Rode python ingest.py <arquivo>.',
    },
  },
  {
    id: 'uploads',
    label: 'Novo conteúdo',
    panel: {
      readOnly: false,
      title: 'Conteúdo enviado nesta demo',
      emptyLabel: 'Nada enviado ainda — arraste um arquivo acima para indexar.',
    },
  },
]

export default function App() {
  const [config, setConfig] = useState(null)
  const [status, setStatus] = useState({ online: false, chunks: null })
  const [error, setError] = useState(null)
  const [reconnecting, setReconnecting] = useState(false)
  const [accessLevel, setAccessLevel] = useState('publico') // produção deriva isso de claims autenticadas
  const [tab, setTab] = useState('base')
  // Bumped per tab by "Nova conversa"; the workspace resets on the change.
  const [resets, setResets] = useState({ base: 0, uploads: 0 })

  const refreshStatus = async (force = false) => {
    try {
      setStatus(await getStatus(force))
    } catch {
      setStatus({ online: false, chunks: null })
    }
  }

  useEffect(() => {
    // getConfig() já reenvia com backoff enquanto o backend termina de subir;
    // o status só é buscado depois, para não gravar "offline" na corrida de boot.
    getConfig()
      .then((cfg) => {
        setConfig(cfg)
        return refreshStatus()
      })
      .catch(() => {
        setConfig(OFFLINE_CONFIG)
        setStatus({ online: false, chunks: null })
        setError('A API não respondeu. Inicie o backend e tente reconectar.')
      })
  }, [])

  const reconnect = async () => {
    setReconnecting(true)
    try {
      const cfg = await getConfig({ retries: 0 })
      setConfig(cfg)
      await refreshStatus(true)
      setError(null)
    } catch {
      setError('A API não respondeu. Inicie o backend e tente reconectar.')
    } finally {
      setReconnecting(false)
    }
  }
  const newChat = () => setResets((prev) => ({ ...prev, [tab]: prev[tab] + 1 }))

  if (!config) {
    return <div style={{ padding: 40, color: C.sub }}>Carregando…</div>
  }

  // Uploaded content has no tenant title/questions of its own; the panel names
  // the scope and the starter questions would point at the wrong corpus.
  const uploadsConfig = {
    ...config,
    document_title: 'conteúdo que você enviou',
    document_description: 'Mesma esteira do corpus de referência — chunk, voyage-3, Atlas Vector Search — sobre o documento enviado nesta aba.',
    questions: [],
  }

  return (
    <div className="app-shell" data-pov-shell>
      <a className="pov-skip-link" href="#conteudo-principal">Pular para o conteúdo</a>
      <main id="conteudo-principal" tabIndex={-1} className="main">
        <TopBar config={config} online={status.online} onRefresh={() => refreshStatus(true)} onNewChat={newChat} accessLevel={accessLevel} onAccessLevel={setAccessLevel} />

        {error && (
          <Banner darkMode variant="danger" dismissible onClose={() => setError(null)} style={{ marginBottom: 14 }}>
            {error}
          </Banner>
        )}

        {!status.online ? (
          <OfflineHero dbName={config.db_name} onReconnect={reconnect} reconnecting={reconnecting} />
        ) : (
          <>
            <div className="ws-tabs" role="tablist" aria-label="Espaços de trabalho">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  role="tab"
                  aria-selected={tab === t.id}
                  className={tab === t.id ? 'active' : undefined}
                  onClick={() => setTab(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {/* Both stay mounted: switching tabs must not drop a running stream
                or the conversation already on screen. */}
            {TABS.map((t) => (
              <div key={t.id} role="tabpanel" hidden={tab !== t.id}>
                <WorkspaceView
                  /* Remount on "Nova conversa": fresh thread and history. */
                  key={`${t.id}-${resets[t.id]}`}
                  config={t.id === 'uploads' ? uploadsConfig : config}
                  scope={t.id}
                  panel={{ ...t.panel, subject: t.id === 'uploads' ? uploadsConfig.document_title : config.document_title }}
                  accessLevel={accessLevel}
                  onStatusRefresh={() => refreshStatus(true)}
                />
              </div>
            ))}
          </>
        )}
      </main>
    </div>
  )
}
