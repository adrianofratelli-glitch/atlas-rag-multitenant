import { C } from '../theme'

// Rotating accent colors for the card top bars
const ACCENTS = [C.green, C.purple, C.cyan, C.teal, C.orange]

export default function Welcome({ config, onPick }) {
  return (
    <div>
      <div className="fade-up d1" style={{ padding: '18px 0 4px' }}>
        <div className="hub-badge">RAG · Atlas Vector Search</div>
        <h1 className="splash-title">
          Converse com o <span>{config.document_title}</span>.
        </h1>
        <p className="splash-sub">
          {config.document_description} Busca semântica <strong style={{ color: C.text }}>{config.embed_model}</strong>{' '}
          + léxica (BM25) fundidas por RRF, reranking <strong style={{ color: C.text }}>{config.rerank_model}</strong>{' '}
          e respostas em streaming — tudo sobre o <strong style={{ color: C.green }}>MongoDB Atlas</strong>.
        </p>
      </div>

      {config.questions?.length > 0 && (
        <div className="fade-up d3">
          <div className="sb-section-label" style={{ marginLeft: 0 }}>Escolha uma pergunta</div>
          <div className="sugg-grid">
            {config.questions.map((q, i) => (
              <button
                key={i}
                className="sugg-card"
                style={{ '--card-accent': ACCENTS[i % ACCENTS.length] }}
                onClick={() => onPick(q)}
              >
                <span className="sugg-num">Pergunta {String(i + 1).padStart(2, '0')}</span>
                <span className="sugg-text">{q}</span>
                <span className="sugg-arrow">→</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
