import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Built assets are served by FastAPI from src/pi_jira_agent/static/dist.
// In dev, API calls are proxied to the backend (override with VITE_API_TARGET).
const apiTarget = process.env.VITE_API_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../src/pi_jira_agent/static/dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': apiTarget,
      '/webhooks': apiTarget,
      '/health': apiTarget,
    },
  },
})
