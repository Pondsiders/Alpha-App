import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// Dev preview pages — no router, just path matching
const path = window.location.pathname;

let Page = App;
if (path.endsWith('/dev/tool-fallback')) {
  const { default: DevToolFallback } = await import('./pages/DevToolFallback.tsx');
  Page = DevToolFallback;
} else if (path.endsWith('/dev/context-ring')) {
  const { default: DevContextRing } = await import('./pages/DevContextRing.tsx');
  Page = DevContextRing;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Page />
  </StrictMode>,
)
