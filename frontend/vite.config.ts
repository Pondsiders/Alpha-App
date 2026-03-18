import fs from 'node:fs'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

// Configurable ports via environment variables
const FRONTEND_PORT = parseInt(process.env.VITE_PORT || '18011', 10)
const BACKEND_PORT = parseInt(process.env.VITE_BACKEND_PORT || '18010', 10)

// Load HTTPS config if certs exist (for dev server), otherwise use false (for build)
function getHttpsConfig() {
  const certPath = '/Pondside/Basement/Files/certs/primer.tail8bd569.ts.net.crt'
  const keyPath = '/Pondside/Basement/Files/certs/primer.tail8bd569.ts.net.key'

  try {
    if (fs.existsSync(certPath) && fs.existsSync(keyPath)) {
      return {
        cert: fs.readFileSync(certPath),
        key: fs.readFileSync(keyPath),
      }
    }
  } catch (e) {
    // Fall through to return false
  }

  return false
}

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.ico', 'apple-touch-icon.png'],
      manifest: {
        name: 'Alpha',
        short_name: 'Alpha',
        description: 'Alpha — the duck in the machine',
        theme_color: '#1c1c1c',
        background_color: '#1c1c1c',
        display: 'standalone',
        scope: '/',
        start_url: '/',
        icons: [
          {
            src: 'icon-192x192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: 'icon-512x512.png',
            sizes: '512x512',
            type: 'image/png',
          },
          {
            src: 'icon-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff,woff2}'],
        // Don't cache API calls — those need to hit the server
        navigateFallbackDenylist: [/^\/api/],
        // Activate new SW immediately — don't wait for old tabs to close.
        // Without this, deploys serve stale cached JS until a hard refresh.
        skipWaiting: true,
        clientsClaim: true,
      },
    }),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: FRONTEND_PORT,
    host: '0.0.0.0',
    https: getHttpsConfig(),
    proxy: {
      '/api': {
        target: `https://localhost:${BACKEND_PORT}`,
        changeOrigin: true,
        secure: false,  // Accept self-signed Tailscale certs
      },
      '/ws': {
        target: `https://localhost:${BACKEND_PORT}`,
        changeOrigin: true,
        ws: true,
        secure: false,
      },
    },
    allowedHosts: [
      'primer',
      '.tail8bd569.ts.net',
    ]
  },
})
