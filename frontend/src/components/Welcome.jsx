import { useState } from 'react'
import { C } from '../theme'
import ArchitectureModal from './ArchitectureModal'

// Rotating accent colors for the card top bars
const ACCENTS = [C.green, C.purple, C.cyan, C.teal, C.orange]

export default function Welcome({ config, onPick }) {
  const [showArch, setShowArch] = useState(false)
  return (
    <div>
      <div className="fade-up d1" style={{ padding: '18px 0 4px' }}>
        <div className="hub-badge">RAG · Atlas Vector Search</div>
        <h1 className="splash-title">
          Converse com o <span>{config.document_title}</span>.
        </h1>
        <p className="splash-sub">
          {config.document_description}{' '}
          {config.native ? (
            <>
              Embedding <strong style={{ color: C.text }}>{config.embed_model}</strong>, busca semântica e
              léxica (BM25), fusão e rerank <strong style={{ color: C.text }}>{config.rerank_model}</strong>{' '}
              acontecem <strong style={{ color: C.green }}>dentro do MongoDB Atlas</strong>, em uma única consulta.
            </>
          ) : (
            <>
              Busca semântica <strong style={{ color: C.text }}>{config.embed_model}</strong> + léxica (BM25)
              fundidas por RRF, reranking <strong style={{ color: C.text }}>{config.rerank_model}</strong> — tudo
              sobre o <strong style={{ color: C.green }}>MongoDB Atlas</strong>.
            </>
          )}
        </p>
        {config.native && (
          <div className="stack-points">
            <span><b>1</b> banco</span>
            <span><b>1</b> consulta</span>
            <span><b>0</b> pipelines de embedding</span>
            <span><b>0</b> serviços de rerank</span>
            <button type="button" className="arch-link" onClick={() => setShowArch(true)}>ver a arquitetura</button>
          </div>
        )}
        {showArch && <ArchitectureModal onClose={() => setShowArch(false)} />}
      </div>

      {config.questions?.length > 0 && (
        <div className="fade-up d3">
          <div className="sb-section-label" style={{ marginLeft: 0 }}>Escolha uma pergunta</div>
          <div className="sugg-grid">
            {config.questions.slice(0, 4).map((q, i) => (
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
