import axios from 'axios'

/**
 * O backend leva alguns segundos para subir (imports pesados), e o launcher abre
 * o browser assim que a porta responde. Sem retry, a primeira carga cai na casca
 * offline e só um reload manual conserta.
 */
export async function getConfig({ retries = 4, delayMs = 800 } = {}) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      const { data } = await axios.get('/api/config', { timeout: 5000 })
      return data
    } catch (err) {
      if (attempt >= retries) throw err
      await new Promise((resolve) => setTimeout(resolve, delayMs * 2 ** attempt))
    }
  }
}

export async function getStatus(force = false) {
  const { data } = await axios.get('/api/status', { params: force ? { force: true } : {}, timeout: 5000 })
  return data
}

export async function getHistory(threadId) {
  const { data } = await axios.get(`/api/history/${encodeURIComponent(threadId)}`, { timeout: 30000 })
  return data.messages || []
}

export async function getDocuments() {
  const { data } = await axios.get('/api/documents', { timeout: 30000 })
  return data
}

/** Upload a document; ingestion runs server-side as a job (poll getJob). */
export async function uploadDocument({ file, nivelAcesso = 'publico', reset = false }) {
  const form = new FormData()
  form.append('file', file)
  form.append('nivel_acesso', nivelAcesso)
  form.append('reset', String(reset))
  const { data } = await axios.post('/api/documents', form, { timeout: 120000 })
  return data
}

export async function getJob(jobId) {
  const { data } = await axios.get(`/api/documents/jobs/${encodeURIComponent(jobId)}`, { timeout: 30000 })
  return data
}

export async function deleteDocument(source) {
  const { data } = await axios.delete(`/api/documents/${encodeURIComponent(source)}`, { timeout: 30000 })
  return data
}

/**
 * Stream the chat over SSE (fetch + ReadableStream).
 * handlers: { onMeta(evt), onToken(delta), onDone(), onError(msg) }
 */
export async function streamChat({ question, messages, threadId, accessLevel, sources, scope, signal }, handlers) {
  const { onMeta, onToken, onDone, onError } = handlers
  if (signal?.aborted) return
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  const deadline = setTimeout(abort, 180000)
  let reader
  let terminal = false
  const fail = (message) => {
    if (terminal || signal?.aborted) return
    terminal = true
    onError?.(message)
  }
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      body: JSON.stringify({
        question, messages, thread_id: threadId,
        access_level: accessLevel || 'publico',
        sources: sources || [], scope: scope || 'all',
      }),
    })
    if (!res.ok || !res.body) {
      fail(`Falha na requisição ao backend (HTTP ${res.status}).`)
      return
    }
    reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (!terminal) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      // SSE aceita LF e CRLF, inclusive quando o par chega em chunks distintos.
      buffer = buffer.replace(/\r\n/g, '\n')
      let idx
      while (!terminal && (idx = buffer.indexOf('\n\n')) >= 0) {
        const raw = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        const data = raw.split('\n').filter(l => l.startsWith('data:')).map(l => l.slice(5).trimStart()).join('\n')
        if (!data) continue // heartbeat/comentário
        const evt = JSON.parse(data)
        if (evt.type === 'meta') onMeta?.(evt)
        else if (evt.type === 'token') onToken?.(evt.delta)
        else if (evt.type === 'done') { terminal = true; onDone?.() }
        else if (evt.type === 'error') fail(evt.message || 'Falha ao gerar a resposta.')
      }
      if (done) break
    }
    if (!terminal) fail('A conexão terminou antes da resposta completa. Tente novamente.')
  } catch {
    fail(controller.signal.aborted
      ? 'A resposta excedeu o tempo limite. Verifique a conexão e tente novamente.'
      : 'A conexão com a API foi interrompida. Tente novamente.')
  } finally {
    clearTimeout(deadline)
    signal?.removeEventListener('abort', abort)
    if (reader) {
      reader.cancel().catch(() => {})
      reader.releaseLock()
    }
  }
}
