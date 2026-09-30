// Browser stub for @emotion/server/create-instance.
// @leafygreen-ui/emotion calls createEmotionServer(cache) at module load, which
// pulls in html-tokenize/readable-stream (Node APIs) and breaks in the browser.
// Those functions only matter for SSR (critical-CSS extraction), which a
// client-only SPA never uses.
export default function createEmotionServer() {
  return {
    extractCritical: (html) => ({ html, ids: [], css: '' }),
    renderStylesToString: (html) => html,
    renderStylesToNodeStream: () => {
      throw new Error('renderStylesToNodeStream is not supported in the browser')
    },
  }
}
