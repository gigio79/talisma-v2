import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './lib/i18n'
import App from './App.tsx'

// Register the push-notifications service worker (best-effort; a missing
// registration must never block app startup). `update()` forces an immediate
// update check on every load — passive checks are throttled by Chrome to ~24h,
// so without it a changed sw.js could take a day to reach devices.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/sw.js')
      .then((registration) => registration.update().catch(() => {}))
      .catch(() => {})
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
