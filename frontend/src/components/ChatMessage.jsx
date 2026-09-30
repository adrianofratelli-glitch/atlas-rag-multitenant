import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { MongoDBLogoMark } from '@leafygreen-ui/logo'
import Icon from '@leafygreen-ui/icon'
import Card from '@leafygreen-ui/card'
import EngineStrip from './EngineStrip'
import Sources from './Sources'
import QueryDetails from './QueryDetails'
import { C } from '../theme'

export default function ChatMessage({ msg }) {
  const isAssistant = msg.role === 'assistant'
  return (
    <div className="chat-row">
      <div className={`avatar ${isAssistant ? 'assistant' : 'user'}`}>
        {isAssistant ? <MongoDBLogoMark height={20} /> : <Icon glyph="Person" />}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Card darkMode style={{ padding: '14px 18px' }}>
          {msg.content ? (
            <div className="md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
            </div>
          ) : (
            <span style={{ color: C.sub, fontStyle: 'italic' }}>Buscando no Atlas…</span>
          )}

          {isAssistant && msg.stats && (
            <EngineStrip stats={msg.stats} elapsedMs={msg.elapsedMs} />
          )}
          {isAssistant && msg.stats?.query_details?.map((detail, index) => (
            <QueryDetails
              key={`${detail.operation}-${index}`}
              operation={detail.operation}
              namespace={detail.namespace}
              query={detail.pipeline}
            />
          ))}
          {isAssistant && <Sources sources={msg.sources} />}
        </Card>
      </div>
    </div>
  )
}
