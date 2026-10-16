import { useState } from 'react'
import WorkspaceView from './WorkspaceView'
import CompareView from './CompareView'

const MODES = [
  { id: 'graph', label: 'Híbrido + grafo' },
  { id: 'compare', label: 'Lado a lado: base × base + grafo' },
]

/**
 * The Graph-RAG tab: a chat over the knowledge graph of the reference corpus,
 * and a side-by-side mode that sends each question to both engines. Both stay
 * mounted, like the workspace tabs, so switching never drops a stream.
 */
export default function GraphTab({ config, accessLevel, onStatusRefresh, panel }) {
  const [mode, setMode] = useState('graph')
  if (!config.native || !config.graph?.ready) {
    return (
      <div className="graph-missing">
        <h2>Grafo ainda não construído</h2>
        <p>
          {config.native
            ? 'Construa o grafo de conhecimento do corpus de referência no mesmo banco:'
            : 'O Graph-RAG usa o caminho nativo do Atlas (RAG_NATIVE=1) e um grafo construído no mesmo banco:'}
        </p>
        <code>ALLOW_DEMO_DB_WRITE=1 python build_graph.py</code>
      </div>
    )
  }
  return (
    <div>
      <div className="graph-modes" role="radiogroup" aria-label="Modo da aba Graph-RAG">
        {MODES.map((m) => (
          <button key={m.id} type="button" role="radio" aria-checked={mode === m.id}
            className={mode === m.id ? 'active' : undefined} onClick={() => setMode(m.id)}>
            {m.label}
          </button>
        ))}
        <span className="graph-modes-note">
          {config.graph.entities} entidades · {config.graph.relations} relações, extraídas do corpus de referência
        </span>
      </div>
      <div hidden={mode !== 'graph'}>
        <WorkspaceView config={config} scope="base" engine="graph" panel={panel}
          accessLevel={accessLevel} onStatusRefresh={onStatusRefresh} />
      </div>
      <div hidden={mode !== 'compare'}>
        <CompareView config={config} accessLevel={accessLevel} onStatusRefresh={onStatusRefresh} />
      </div>
    </div>
  )
}
