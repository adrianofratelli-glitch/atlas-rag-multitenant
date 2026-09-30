import { C } from '../theme'

function Stat({ label, value, color }) {
  return (
    <div className="stat-item">
      <div className="stat-val" style={color ? { color } : undefined}>{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

export default function KpiRow({ totalQueries, chunksRead, documentTitle }) {
  return (
    <div className="stat-bar fade-up d2">
      <Stat label="Perguntas na sessão" value={totalQueries} color={C.green} />
      <Stat label="Chunks lidos" value={chunksRead} />
      <Stat label="Documento" value={(documentTitle || '').slice(0, 16)} />
      <Stat label="Modelo · streaming" value="Claude Sonnet" />
    </div>
  )
}
