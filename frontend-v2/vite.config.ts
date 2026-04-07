import fs from 'fs'
import path from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const CERT_DIR = '/Pondside/Basement/Files/certs'
const HOST = 'primer.tail8bd569.ts.net'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: true,
    allowedHosts: [HOST],
    https: {
      cert: fs.readFileSync(path.join(CERT_DIR, `${HOST}.crt`)),
      key: fs.readFileSync(path.join(CERT_DIR, `${HOST}.key`)),
    },
    proxy: {
      "/api": "http://primer.tail8bd569.ts.net:18020",
      "/ws": {
        target: "ws://primer.tail8bd569.ts.net:18020",
        ws: true,
      },
    },
    headers: {
      "Cache-Control": "no-store",
    },
  },
})
