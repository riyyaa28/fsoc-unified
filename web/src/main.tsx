import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { InstallPrompt, isInstalledApp } from './components/InstallPrompt'
import './styles.css'

// The service worker only makes the hosted site installable; the desktop .exe does not need it.
if ('serviceWorker' in navigator && !['127.0.0.1', 'localhost'].includes(window.location.hostname)) navigator.serviceWorker.register('/sw.js').catch(() => undefined)

createRoot(document.getElementById('root')!).render(<StrictMode><App />{!isInstalledApp() && <InstallPrompt />}</StrictMode>)
