import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import LeafyGreenProvider from '@leafygreen-ui/leafygreen-provider'
import './index.css'
import './pov-signature.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <LeafyGreenProvider darkMode>
      <App />
    </LeafyGreenProvider>
  </StrictMode>,
)
