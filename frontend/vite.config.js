import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

const apiProxy = {
  '/api': {
    // RAG_API_URL: backend em outra porta (teste ao vivo sem ocupar a porta da demo).
    target: process.env.RAG_API_URL || 'http://localhost:8180',
    changeOrigin: true,
  },
}

// Proxy /api -> FastAPI backend (:8180) to avoid CORS in development.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // @leafygreen-ui/emotion puxa @emotion/server (SSR) que quebra no browser.
      '@emotion/server/create-instance': fileURLToPath(
        new URL('./src/emotion-server-stub.js', import.meta.url),
      ),
    },
  },
  server: {
    // Dedicated port for this POC; 5173 (the Vite default) collides with other local apps.
    port: 5180,
    strictPort: true, // fail loudly instead of silently switching ports
    host: '127.0.0.1',
    proxy: apiProxy,
  },
  preview: { host: '127.0.0.1', port: 5180, strictPort: true, proxy: apiProxy },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('@leafygreen-ui') || id.includes('@lg-')) return 'leafygreen'
          if (/node_modules\/(react-markdown|remark-|rehype-|micromark|mdast-|unified|unist-|hast-|vfile)/.test(id)) return 'markdown'
          return 'vendor'
        },
      },
    },
  },
})
