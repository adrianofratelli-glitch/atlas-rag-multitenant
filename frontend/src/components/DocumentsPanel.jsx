import { useEffect, useRef, useState } from 'react'
import Icon from '@leafygreen-ui/icon'
import { getDocuments, getJob, uploadDocument } from '../api'
import { C } from '../theme'

const PHASE_LABEL = {
  queued: 'na fila',
  loading: 'lendo o arquivo',
  embedding: 'gerando embeddings',
  done: 'concluído',
  error: 'erro',
}

/**
 * Document library: send a file during the demo and it goes through the same
 * pipeline as the pre-loaded corpus (chunk -> voyage-3 -> Atlas). Selecting
 * documents here scopes retrieval to them via `metadata.source`.
 */
/** "expira em 23h" / "expira em 45min" — o TTL só existe em documento enviado na demo. */
function expiryLabel(iso) {
  if (!iso) return null
  const ms = new Date(iso).getTime() - Date.now()
  if (Number.isNaN(ms)) return null
  if (ms <= 0) return 'expirando'
  const hours = Math.floor(ms / 3600000)
  return hours >= 1 ? `expira em ${hours}h` : `expira em ${Math.max(1, Math.round(ms / 60000))}min`
}

/**
 * `workspace` restricts the panel to one tab's corpus: "base" is the tenant's
 * reference documents (read-only) and "uploads" is what was sent through the
 * UI. The backend derives the same split from the TTL stamp.
 */
export default function DocumentsPanel({ selected, onSelected, onCorpusChange, maxUploadMb, formats, ttlHours, workspace = 'uploads', readOnly = false, title = 'Base de conhecimento', emptyLabel = 'Nenhum documento indexado neste tenant ainda.' }) {
  const [docs, setDocs] = useState([])
  const [job, setJob] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [open, setOpen] = useState(false)
  const inputRef = useRef(null)
  const pollRef = useRef(null)

  const inWorkspace = (list) => (list || []).filter((d) => d.workspace === workspace)

  const refresh = async () => {
    try {
      const data = await getDocuments()
      setDocs(inWorkspace(data.documents))
    } catch {
      setError('Não foi possível listar os documentos indexados.')
    }
  }

  useEffect(() => {
    getDocuments()
      .then((data) => setDocs((data.documents || []).filter((d) => d.workspace === workspace)))
      .catch(() => setError('Não foi possível listar os documentos indexados.'))
    return () => clearTimeout(pollRef.current)
  }, [workspace])

  // Ingestion is a background job: poll until it finishes. Embedding is
  // rate-limited upstream, so a large document legitimately takes minutes.
  const poll = (jobId) => {
    pollRef.current = setTimeout(async () => {
      try {
        const next = await getJob(jobId)
        setJob(next)
        if (next.status === 'done') {
          setBusy(false)
          await refresh()
          onCorpusChange?.()
          onSelected?.([next.source])
        } else if (next.status === 'error') {
          setBusy(false)
          setError(next.error || 'A ingestão falhou.')
        } else {
          poll(jobId)
        }
      } catch {
        setBusy(false)
        setError('Perdemos o acompanhamento da ingestão. Recarregue a lista.')
      }
    }, 2000)
  }

  const send = async (file) => {
    if (!file || busy) return
    setError(null)
    setBusy(true)
    setOpen(true)
    try {
      const started = await uploadDocument({ file, reset: true })
      setJob(started)
      poll(started.job_id)
    } catch (e) {
      setBusy(false)
      setError(e?.response?.data?.detail || 'Falha no envio do arquivo.')
    }
  }

  const toggle = (source) => {
    const set = new Set(selected || [])
    if (set.has(source)) set.delete(source)
    else set.add(source)
    onSelected?.([...set])
  }

  const scope = selected?.length ? `${selected.length} documento(s)` : 'todos os documentos'
  const pct = job?.total ? Math.round((job.done / job.total) * 100) : 0

  return (
    <div className="docs-panel">
      <button
        className="docs-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <Icon glyph={open ? 'ChevronDown' : 'ChevronRight'} />
        <span>{title}</span>
        <span className="docs-scope">{docs.length} indexado(s) · consultando {scope}</span>
      </button>

      {open && (
        <div className="docs-body">
          {!readOnly && (
          <div
            className={`docs-drop${dragging ? ' dragging' : ''}`}
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              send(e.dataTransfer.files?.[0])
            }}
          >
            <Icon glyph="Upload" fill={C.green} />
            <span>
              Arraste um documento ou{' '}
              <button className="docs-link" onClick={() => inputRef.current?.click()} disabled={busy}>
                escolha um arquivo
              </button>
            </span>
            <span className="docs-hint">
              Até {maxUploadMb || 25} MB · {(formats || []).join(' ') || 'pdf docx txt csv md html json xlsx pptx'}
            </span>
            {ttlHours > 0 && (
              <span className="docs-hint">
                Conteúdo de demo: os chunks enviados aqui expiram sozinhos em {ttlHours}h.
                O documento pré-carregado do tenant não expira.
              </span>
            )}
            <input
              ref={inputRef}
              type="file"
              hidden
              accept={(formats || []).join(',')}
              onChange={(e) => {
                send(e.target.files?.[0])
                e.target.value = ''
              }}
            />
          </div>
          )}

          {job && job.status !== 'done' && (
            <div className="docs-job">
              <div className="docs-job-head">
                <span>{job.filename}</span>
                <span style={{ color: job.status === 'error' ? '#FF6960' : C.sub }}>
                  {PHASE_LABEL[job.phase] || job.phase}
                  {job.total ? ` · ${job.done}/${job.total} chunks` : ''}
                </span>
              </div>
              <div className="src-track">
                <div
                  className="src-fill"
                  style={{
                    width: `${job.status === 'error' ? 100 : pct}%`,
                    background: job.status === 'error' ? '#FF6960' : C.green,
                  }}
                />
              </div>
              <p className="docs-hint">
                O tier gratuito da VoyageAI limita 3 requisições por minuto — documentos
                grandes levam alguns minutos.
              </p>
            </div>
          )}

          {error && <p className="docs-error">{error}</p>}

          <ul className="docs-list">
            {docs.map((d) => {
              const active = (selected || []).includes(d.source)
              return (
                <li key={d.source} className={active ? 'active' : undefined}>
                  <label>
                    <input type="checkbox" checked={active} onChange={() => toggle(d.source)} />
                    <span className="docs-name">{d.source}</span>
                    <span className="docs-meta">{d.chunks} chunks</span>
                    {d.nivel_acesso?.includes('restrito') && (
                      <span className="docs-tag">restrito</span>
                    )}
                    {expiryLabel(d.expires_at) && (
                      <span className="docs-tag ttl">{expiryLabel(d.expires_at)}</span>
                    )}
                  </label>
                  {!d.expires_at && <span className="docs-tag base">base</span>}
                </li>
              )
            })}
            {docs.length === 0 && (
              <li className="docs-empty">{emptyLabel}</li>
            )}
          </ul>

          {selected?.length > 0 && (
            <button className="docs-link" onClick={() => onSelected?.([])}>
              Limpar seleção (consultar todos)
            </button>
          )}
        </div>
      )}
    </div>
  )
}
