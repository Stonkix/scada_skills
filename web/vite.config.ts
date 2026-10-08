import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// Same-origin in dev and in Docker: /api -> API (8000), /sim -> simulator (8010).
// nginx does the same in the container (web/nginx.conf), so the app never deals with CORS.
const api = process.env.API_URL ?? 'http://127.0.0.1:8000'
const sim = process.env.SIM_URL ?? 'http://127.0.0.1:8010'

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    host: '127.0.0.1', // Windows resolves localhost to ::1 only; browsers and tools often try IPv4
    port: 5173,
    proxy: {
      '/api': { target: api, changeOrigin: true, ws: true, rewrite: (p) => p.replace(/^\/api/, '') },
      '/sim': { target: sim, changeOrigin: true, rewrite: (p) => p.replace(/^\/sim/, '') },
    },
  },
  // ECharts is one ~580 kB chunk, loaded only by views with charts (lazy routes): expected, not a regression
  build: { chunkSizeWarningLimit: 650 },
  test: { environment: 'jsdom' },
})
