import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// GitHub Pages project sites live at /REPO_NAME/ — set VITE_BASE_PATH=/elmonte/
// in CI. User/org sites at https://user.github.io/ can leave it unset (/).
const base = process.env.VITE_BASE_PATH || '/'

export default defineConfig({
  base,
  plugins: [react()],
  // Apollo Client v4 ships CJS/ES bundles that reference `tslib` and `rxjs`
  // at runtime. Vite's dep-optimizer won't pick those up unless we name them
  // explicitly, and once it produces a stale bundle without them the running
  // process refuses to re-optimize until you restart. Listing them here
  // avoids that whole class of "Failed to resolve import" errors.
  optimizeDeps: {
    include: [
      '@apollo/client',
      '@apollo/client/react',
      'tslib',
      'rxjs',
    ],
  },
  server: {
    // Proxy /api/* to the FastAPI backend running on :8000. Same-origin from
    // the browser's perspective, so no CORS in dev.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (
            id.includes('/@xyflow/react/') ||
            id.includes('/dagre/')
          ) {
            return 'flow';
          }
          if (
            id.includes('/@apollo/client/') ||
            id.includes('/graphql/')
          ) {
            return 'apollo';
          }
          if (
            id.includes('/react/') ||
            id.includes('/react-dom/') ||
            id.includes('/react-router-dom/')
          ) {
            return 'react';
          }
        },
      },
    },
  },
})
