import { useState } from 'react'
import { MissionControl, StatusStrip } from './components/Mission'
import { PerformancePage } from './components/PerformancePage'
import { ScenarioPage } from './components/ScenarioPage'
import { SettingsPage } from './components/SettingsPage'
import { Simulation3D } from './components/Simulation3D'
import { VideoBenchmark } from './components/VideoBenchmark'
import { isInstalledApp, SHOW_INSTALL_EVENT } from './components/InstallPrompt'
import { useTelemetry } from './hooks/useTelemetry'

type Page = 'mission' | 'sim3d' | 'video' | 'scenario' | 'settings' | 'performance' | 'about'

const NAV: [Page, string][] = [['mission', 'Mission Control'], ['sim3d', '3D Simulation'], ['video', 'Video Benchmark'], ['scenario', 'Scenario'], ['settings', 'Settings'], ['performance', 'Performance']]

function About() {
  return <section className="about-layout"><section className="panel about-hero"><h2>AI-Based Virtual Camera Tracking for Coarse Alignment of Mobile FSOC Terminals</h2>
    <p>One Python process owns the state. Every view — 3D scene, camera image, metrics and reports — is derived from it. The 3D simulation and the uploaded-video benchmark run the same tracking core.</p>
    <div className="flow"><span>Beacon paths + clutter</span><b>→</b><span>Camera render (resolution, FOV, jitter, platform)</span><b>→</b><span>Atmosphere + sensor noise</span><b>→</b><span>Ring-signature detection</span><b>→</b><span>Gated Kalman lock</span><b>→</b><span>Rate-limited PTZ</span><b>→</b><span>Measured metrics</span></div></section>
    <section className="about-grid">
      <article className="panel about-card"><h3>Operator workflow</h3><p>Load a scenario, press Start, pick any beacon in Mission Control and Connect. Hide the beacon to test re-acquisition. Generate the performance report from Performance, or upload an MP4 in Video Benchmark.</p></article>
      <article className="panel about-card"><h3>What is measured</h3><p>In simulation, tracking error is measured against ground truth. In video, it is the distance between the detected centroid and a virtual camera centre that starts at the screen centre and moves under the same pan/tilt limits.</p></article>
      <article className="panel about-card"><h3>Model boundaries</h3><p>Haze, fog, rain, low light and turbulence are image-domain models, not optical propagation or link-budget models. YOLO is optional; without ultralytics the classical detector is used and the UI says so.</p></article>
    </section></section>
}

export default function App() {
  const { state, connection, error, clearError, reportError, send } = useTelemetry()
  const [page, setPage] = useState<Page>('mission')
  if (!state) return <main className="loading"><div className="spinner" /><h1>FSOC Mission Control</h1><p>{connection === 'offline' ? 'Backend unavailable. Start it with: python -m server  (http://127.0.0.1:8011)' : 'Connecting to the Python simulation…'}</p></main>
  return <main className="app-shell">
    <header className="topbar"><div className="brand"><span aria-hidden="true">◈</span><div><h1>FSOC Coarse Alignment Mission Control</h1><p>AI virtual camera tracking for mobile free-space optical terminals</p></div></div>
      <div className="header-states"><span><i className={`dot ${connection}`} />{connection === 'connected' ? 'Backend connected' : connection === 'paused' ? 'Live updates paused' : connection}</span><span><i className={`dot ${state.running ? 'connected' : 'offline'}`} />{state.running ? 'Simulation running' : 'Simulation paused'}</span>{state.video && state.video.status === 'PROCESSING' && <span><i className="dot connecting" />Video processing</span>}<span>{state.system.fps.toFixed(1)} Hz</span>{!isInstalledApp() && <button className="get-app" onClick={() => window.dispatchEvent(new Event(SHOW_INSTALL_EVENT))}>Get the app</button>}</div></header>
    <nav className="nav-tabs" aria-label="Sections">{NAV.map(([id, label]) => <button className={page === id ? 'selected' : ''} aria-current={page === id ? 'page' : undefined} onClick={() => setPage(id)} key={id}>{label}</button>)}<button className={`about-link ${page === 'about' ? 'selected' : ''}`} onClick={() => setPage('about')}>About</button></nav>
    <StatusStrip state={state} />
    {connection === 'paused' && <div className="stream-paused" role="status"><section className="panel"><span className="eyebrow">Saving data</span><h2>Live updates paused</h2><p>This tab was idle, so telemetry stopped streaming. Click anywhere to resume.</p></section></div>}
    {error && <div className="error-banner" role="alert"><span>{error}</span><button onClick={clearError}>Dismiss</button></div>}
    {page === 'mission' && <MissionControl state={state} send={send} />}
    {page === 'sim3d' && <Simulation3D state={state} send={send} />}
    {page === 'video' && <VideoBenchmark state={state} send={send} onError={reportError} />}
    {page === 'scenario' && <ScenarioPage state={state} send={send} onError={reportError} />}
    {page === 'settings' && <SettingsPage state={state} send={send} />}
    {page === 'performance' && <PerformancePage state={state} onError={reportError} />}
    {page === 'about' && <About />}
  </main>
}
