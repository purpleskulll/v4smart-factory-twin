import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Der Prod-Build enthält KEINE absoluten URLs — die App spricht ausschließlich
// über relative Pfade (/api, /ws), das Routing macht Caddy (Schritt 06).
// Der Proxy hier gilt nur für lokale Iteration mit `npm run dev`.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://middleware-core:8080', changeOrigin: true },
      '/ws': { target: 'http://middleware-core:8080', ws: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
