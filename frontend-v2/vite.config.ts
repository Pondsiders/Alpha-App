import path from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: true,
    allowedHosts: ["primer.tail8bd569.ts.net"],
    proxy: {
      "/api": "http://alpha.tail8bd569.ts.net:18010",
    },
    headers: {
      "Cache-Control": "no-store",
    },
  },
})
