import './boresight.css'
import { EventList, SihGrid } from './controls'
import { fmt, PATTERN_LABELS, type Command, type Telemetry } from '../types/simulation'

const COMM_TONE: Record<string, string> = { IDLE: 'idle', ACQUIRING: 'warn', TRACKING: 'info', CONNECTED: 'good' }
const TRACK_TONE: Record<string, string> = { SEARCHING: 'warn', ACQUIRING: 'warn', LOCKED: 'good', COASTING: 'info' }

/** Always-visible answer to: which beacon, is it talking, is it locked,
 * what disturbs it, what the camera does, and how well it performs. */
export function StatusStrip({ state }: { state: Telemetry }) {
  const sih = state.performance.sih ?? []
  const measured = sih.filter((item) => item.pass !== null)
  const passed = measured.filter((item) => item.pass).length
  return <section className="status-strip" aria-label="Situation summary">
    <div className="strip-cell"><span>Selected beacon</span><strong>{state.comm.target_label}</strong><small>{PATTERN_LABELS[state.comm.path ?? ''] ?? '—'} path</small></div>
    <div className={`strip-cell tone-${COMM_TONE[state.comm.state]}`}><span>Communication</span><strong><i />{state.comm.state}</strong><small>{state.comm.active ? `link when error ≤ ${state.comm.link_threshold_px.toFixed(0)} px` : 'not started'}</small></div>
    <div className={`strip-cell tone-${TRACK_TONE[state.tracking.state]}`}><span>Tracking</span><strong><i />{state.tracking.state}</strong><small>{state.tracking.pixel_error == null ? 'error —' : `error ${state.tracking.pixel_error.toFixed(1)} px`}</small></div>
    <div className={`strip-cell ${state.disturbance_labels.length ? 'tone-warn' : ''}`}><span>Disturbances</span><strong>{state.disturbance_labels.length ? `${state.disturbance_labels.length} active` : 'None'}</strong><small title={state.disturbance_labels.join(', ')}>{state.disturbance_labels.slice(0, 2).join(', ') || 'clear conditions'}{state.disturbance_labels.length > 2 ? ' …' : ''}</small></div>
    <div className="strip-cell"><span>Camera</span><strong>{state.camera.pan.toFixed(2)}° / {state.camera.tilt.toFixed(2)}°</strong><small>{state.camera.rate_limited ? 'slewing at rate limit' : `${Math.hypot(state.camera.pan_rate, state.camera.tilt_rate).toFixed(2)}°/s`} · {state.camera.resolution[0]}×{state.camera.resolution[1]}</small></div>
    <div className={`strip-cell ${measured.length ? (passed === measured.length ? 'tone-good' : 'tone-bad') : ''}`}><span>SIH limits</span><strong>{measured.length ? `${passed}/${measured.length} met` : 'Awaiting data'}</strong><small>{state.system.fps.toFixed(0)} Hz loop · {state.system.processing_time_ms.toFixed(1)} ms</small></div>
    <div title={state.scenario.name} className={`strip-cell scenario ${state.scenario.status === 'ACTIVE' ? 'tone-good' : ''}`}><span>Scenario</span><strong>{state.scenario.name}</strong><small>{state.scenario.status === 'ACTIVE' ? 'Active ✓' : 'Loaded ✓'}{state.scenario.modified ? ' · modified' : ''}</small></div>
  </section>
}

type MarkerType = 'measured' | 'predicted' | 'truth'
function Marker({ point, type, label, state }: { point: [number, number] | null; type: MarkerType; label: string; state: Telemetry }) {
  if (!point) return null
  return <span aria-label={`${label} position`} className={`camera-marker ${type}`} style={{ left: `${point[0] / state.camera_view.width * 100}%`, top: `${point[1] / state.camera_view.height * 100}%` }}><span>{label}</span></span>
}

export function CameraView({ state, showTruth = false }: { state: Telemetry; showTruth?: boolean }) {
  const t = state.tracking
  const hidden = state.simulation.hidden_id === state.comm.target_id
  return <section className="panel camera-panel"><header className="panel-head camera-panel-head"><div><span className="eyebrow">Virtual camera · {state.camera.resolution[0]}×{state.camera.resolution[1]} · {state.camera.fov_horizontal.toFixed(1)}°×{state.camera.fov_vertical.toFixed(1)}°</span><h2>2D camera boresight</h2></div><span className={`status-tag ${TRACK_TONE[t.state] === 'good' ? 'good' : TRACK_TONE[t.state] === 'info' ? 'predicting' : 'warn'}`}>{t.state}{hidden ? ' · beacon hidden' : ''}</span></header>
    <div className="camera-frame boresight-frame" style={{ aspectRatio: `${state.camera_view.width} / ${state.camera_view.height}` }}>
      {state.camera_view.image ? <img src={state.camera_view.image} alt="Rendered camera frame with disturbances as seen by the detector" /> : <div className="camera-empty">Preparing camera stream</div>}
      <div className="boresight-topline"><span>{state.comm.target_label.toUpperCase()}</span><span>{t.detector}</span></div>
      {showTruth && <Marker point={t.truth} type="truth" label="TRUTH" state={state} />}
      <div className="boresight-readout left"><span>Centroid {t.measured ? `(${t.measured[0].toFixed(1)}, ${t.measured[1].toFixed(1)})` : '—'}</span><span>Candidates {t.candidates}</span><span>Score {t.confidence.toFixed(2)}</span></div>
      <div className="boresight-readout right"><span>Pan {state.camera.pan.toFixed(3)}°</span><span>Tilt {state.camera.tilt.toFixed(3)}°</span><span>LOS offset {state.camera.los_offset_px[0].toFixed(1)}, {state.camera.los_offset_px[1].toFixed(1)} px</span></div>
      <div className="boresight-status"><span>{state.system.fov_ok ? 'Target in FOV' : 'Target outside FOV'}</span><span>{state.system.los_clear ? 'LOS clear' : 'LOS blocked'}</span><span>Error {fmt(t.pixel_error, 1, ' px')}</span></div>
    </div>
    <footer className="camera-legend boresight-legend"><span><i className="centre" />Camera centre</span><span><i className="measured" />Detected beacon</span><span><i className="predicted" />Kalman track</span><span><i className="candidate" />Other candidates</span></footer>
  </section>
}

export function SimulationControls({ state, send }: { state: Telemetry; send: (command: Command) => void }) {
  return <div className="button-grid four">
    <button className="action primary" disabled={state.running} onClick={() => send({ action: 'start' })}>Start</button>
    <button className="action" disabled={!state.running} onClick={() => send({ action: 'pause' })}>Pause</button>
    <button className="action" onClick={() => send({ action: 'toggle_beacon' })}>{state.simulation.beacon_hidden ? 'Restore beacon' : 'Hide beacon'}</button>
    <button className="action muted" onClick={() => send({ action: 'reset' })}>Reset</button>
  </div>
}

export function CommPanel({ state, send }: { state: Telemetry; send: (command: Command) => void }) {
  const beacons = state.targets.filter((target) => target.role === 'beacon')
  const connected = state.comm.active
  return <section className="panel compact-panel comm-panel">
    <div className="panel-title">Mission</div>
    <SimulationControls state={state} send={send} />
    <div className="panel-title spaced">Beacon communication</div>
    <ul className="beacon-list" role="radiogroup" aria-label="PTZ communication target">
      {beacons.map((beacon) => <li key={beacon.id}>
        <button role="radio" aria-checked={beacon.selected} className={beacon.selected ? 'selected' : ''} onClick={() => send({ action: 'set_target', target_id: beacon.id })}>
          <i className={beacon.hidden ? 'hidden' : ''} /><strong>{beacon.label}</strong><span>{PATTERN_LABELS[beacon.pattern]}</span>
          <em>{beacon.selected ? (connected ? state.comm.state : 'Selected') : beacon.hidden ? 'Hidden' : ''}</em>
        </button>
      </li>)}
    </ul>
    <div className="button-grid two">
      <button className="action primary" disabled={connected} onClick={() => send({ action: 'start_comm' })}>Connect to {state.comm.target_label}</button>
      <button className="action muted" disabled={!connected} onClick={() => send({ action: 'stop_comm' })}>Stop communication</button>
    </div>
    <label className="control-row"><span>Start mode<small className="hint">Auto connects when the mission starts</small></span><select value={state.comm.mode} onChange={(event) => send({ action: 'set_lock_mode', lock_mode: event.target.value as 'auto' | 'manual' })}><option value="auto">Auto</option><option value="manual">Manual</option></select></label>
    <p className={`link-readout ${state.comm.state === 'CONNECTED' ? 'ready' : ''}`}>{state.system.link_reason}</p>
    {!state.running && <p className="control-note">Simulation paused. Press Start{state.comm.mode === 'manual' ? ', then Connect,' : ''} to begin tracking.</p>}
  </section>
}

export function MissionControl({ state, send }: { state: Telemetry; send: (command: Command) => void }) {
  const sih = state.performance.sih ?? []
  return <section className="mission-layout">
    <div className="mission-main">
      <CameraView state={state} />
      <section className="panel compact-panel"><div className="panel-title">SIH performance — live, measured this session</div><SihGrid items={sih} compact /></section>
    </div>
    <aside className="mission-side">
      <CommPanel state={state} send={send} />
      <section className="panel events compact-events"><header className="panel-head"><div><h2>Event log</h2></div><span>{state.events.length}</span></header><EventList events={state.events} empty="No events yet. Press Start." /></section>
    </aside>
  </section>
}
