import { useEffect, useState } from 'react'

// Windows desktop build, committed to the repo under release/.
const EXE_URL = 'https://github.com/shruti12719/FSOC-coarse-alignment-and-tracking-system-/raw/main/release/FSOC_Tracker.exe'
const DISMISS_KEY = 'fsoc-install-dismissed'
export const SHOW_INSTALL_EVENT = 'fsoc:show-install'

interface InstallEvent extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }> }

// beforeinstallprompt can fire before React mounts, so capture it at module load.
let deferred: InstallEvent | null = null
const listeners = new Set<() => void>()
window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault()
  deferred = event as InstallEvent
  listeners.forEach((notify) => notify())
})
window.addEventListener('appinstalled', () => { deferred = null; listeners.forEach((notify) => notify()) })

// The desktop .exe serves the app from 127.0.0.1; an installed app runs standalone.
export const isInstalledApp = () =>
  ['127.0.0.1', 'localhost'].includes(window.location.hostname) || window.matchMedia('(display-mode: standalone)').matches || (navigator as { standalone?: boolean }).standalone === true

const ua = navigator.userAgent
const isWindows = /Windows/.test(ua)
const isIOS = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)

function dismissed() { try { return localStorage.getItem(DISMISS_KEY) === '1' } catch { return false } }
function remember() { try { localStorage.setItem(DISMISS_KEY, '1') } catch { /* storage blocked: ask again next visit */ } }

export function InstallPrompt() {
  const [open, setOpen] = useState(false)
  const [canPrompt, setCanPrompt] = useState(deferred !== null)

  useEffect(() => {
    if (isInstalledApp()) return
    const sync = () => setCanPrompt(deferred !== null)
    const show = () => setOpen(true)
    listeners.add(sync)
    window.addEventListener(SHOW_INSTALL_EVENT, show)
    const timer = dismissed() ? undefined : window.setTimeout(show, 1500)
    return () => { listeners.delete(sync); window.removeEventListener(SHOW_INSTALL_EVENT, show); window.clearTimeout(timer) }
  }, [])

  if (!open) return null
  const close = () => { remember(); setOpen(false) }
  const install = async () => {
    if (!deferred) return
    await deferred.prompt()
    if ((await deferred.userChoice).outcome === 'accepted') close()
    deferred = null
    setCanPrompt(false)
  }

  return <div className="install-backdrop" onClick={close}>
    <section className="panel install-card" role="dialog" aria-modal="true" aria-labelledby="install-title" onClick={(event) => event.stopPropagation()}>
      <img src="/icon-192.png" alt="" width={56} height={56} />
      <span className="eyebrow">Get the app</span>
      <h2 id="install-title">Install FSOC Mission Control</h2>
      <p>Run the tracking system as an app on your device, in its own window.</p>
      {isWindows && <div className="install-option">
        <a className="action primary" href={EXE_URL} onClick={close}>Download for Windows (.exe)</a>
        <small>Works fully offline. Open the downloaded file to run it. If Windows shows “Windows protected your PC”, choose More info → Run anyway.</small>
      </div>}
      {canPrompt && <div className="install-option">
        <button className={`action ${isWindows ? '' : 'primary'}`} onClick={install}>Install web app</button>
        <small>Adds FSOC Tracker to your {isWindows ? 'Start menu and desktop' : 'home screen or apps'}. Needs an internet connection.</small>
      </div>}
      {!canPrompt && isIOS && <p className="install-hint">On iPhone or iPad: tap <b>Share</b>, then <b>Add to Home Screen</b>.</p>}
      {!canPrompt && !isIOS && <p className="install-hint">To install the web app, open the browser menu and choose <b>Install app</b> or <b>Add to Home screen</b> (Chrome or Edge).</p>}
      <button className="install-dismiss" onClick={close}>Not now</button>
    </section>
  </div>
}
