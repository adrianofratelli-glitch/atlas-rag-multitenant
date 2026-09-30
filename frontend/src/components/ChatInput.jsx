import { useState } from 'react'
import Button from '@leafygreen-ui/button'
import TextInput from '@leafygreen-ui/text-input'
import Icon from '@leafygreen-ui/icon'

export default function ChatInput({ placeholder, disabled, onSend }) {
  const [value, setValue] = useState('')

  const submit = () => {
    const q = value.trim()
    if (!q || disabled) return
    setValue('')
    onSend(q)
  }

  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', marginTop: 18 }}>
      <div style={{ flex: 1 }}>
        <span id="chat-q-label" className="sr-only">Pergunta sobre o documento</span>
        <TextInput
          darkMode
          aria-labelledby="chat-q-label"
          placeholder={placeholder}
          value={value}
          disabled={disabled}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit()
          }}
        />
      </div>
      <Button darkMode variant="primary" disabled={disabled || !value.trim()} onClick={submit} leftGlyph={<Icon glyph="ArrowUp" />}>
        Enviar
      </Button>
    </div>
  )
}
