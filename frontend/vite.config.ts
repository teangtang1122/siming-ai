import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5371,
    proxy: {
      '/api/v1': {
        target: 'http://127.0.0.1:8521',
        changeOrigin: true,
        timeout: 600000,
        proxyTimeout: 600000,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
