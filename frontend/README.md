# Frontend

Cliente React + Vite do assistente RAG, construído com o design system
[LeafyGreen](https://www.mongodb.design/) da MongoDB.

- **Servidor de dev:** `npm run dev` — serve em http://localhost:5180 e faz proxy de
  `/api` para o backend FastAPI na porta 8180.
- **Build:** `npm run build` — gera o bundle de produção em `dist/`.
- **Lint:** `npm run lint`.

Veja o [README do projeto](../README.md) para o setup completo, incluindo o
backend e a configuração do MongoDB Atlas.

## Organização

- `src/App.jsx` — estado da aplicação e orquestração
- `src/api.js` — requisições de config/status e o stream SSE do chat
- `src/components/` — componentes de UI (TopBar, ChatMessage, EngineStrip, Sources; Sidebar e KpiRow são legados fora do shell atual)
- `src/index.css` — estilos globais e tokens de design
- `src/theme.js` — paleta de cores
