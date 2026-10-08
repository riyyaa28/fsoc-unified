import { useCallback, useRef, useState } from 'react'
import { WorldScene, ZOOM_LIMITS, type WorldHandle, type WorldOptions } from '../three/WorldScene'
import { fmt, type Command, type Telemetry } from '../types/simulation'
import { CameraView, SimulationControls } from './Mission'
import { EventList } from './controls'

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: () => void }) {
  return <button className={`scene-toggle ${checked ? 'on' : ''}`} aria-pressed={checked} onClick={onChange}>{label}</button>
}

function Readout({ label, value }: { label: string; value: string }) { return <div className="readout"><span>{label}</span><strong>{value}</strong></div> }

export function Simulation3D({ state, send }: { state: Telemetry; send: (command: Command) => void }) {
  const [options, setOptions] = useState<WorldOptions>({ grid: true, labels: true, trajectories: true, fov: true, beam: true, resetKey: 0 })
  const [distance, setDistance] = useState(ZOOM_LIMITS.start)
  const [inset, setInset] = useState(true)
  const world = useRef<WorldHandle>(null)
  const onDistance = useCallback((value: number) => setDistance(value), [])
  const toggle = (key: keyof Omit<WorldOptions, 'resetKey'>) => setOptions((current) => ({ ...current, [key]: !current[key] }))
  const zoomPct = Math.round(100 * ZOOM_LIMITS.start / distance)
  const d = state.disturbances
  return <section className="sim-layout">
    <section className="panel scene-panel world-panel">
      <header className="panel-head"><div><span className="eyebrow">Field of regard {state.field.width_deg}°×{state.field.height_deg}° · plan view</span><h2>3D simulation</h2></div>
        <div className="head-actions"><span className={`status-tag ${state.running ? 'good' : ''}`}>{state.running ? 'Running' : 'Paused'}</span></div></header>
      <div className="world-wrap">
        <WorldScene ref={world} state={state} options={options} onDistance={onDistance} />
        <div className="zoom-controls" aria-label="Zoom">
          <button onClick={() => world.current?.zoom(0.8)} aria-label="Zoom in">+</button>
          <span title={`Camera distance ${distance.toFixed(0)} (limits ${ZOOM_LIMITS.min}-${ZOOM_LIMITS.max})`}>{zoomPct}%</span>
          <button onClick={() => world.current?.zoom(1.25)} aria-label="Zoom out">−</button>
        </div>
        {inset && <div className="camera-inset" aria-label="Live camera inset"><img src={state.camera_view.image} alt="Live camera" /><span>Camera {state.tracking.state}</span></div>}
      </div>
      <footer className="scene-tools">
        <Toggle label="Grid" checked={options.grid} onChange={() => toggle('grid')} /><Toggle label="Labels" checked={options.labels} onChange={() => toggle('labels')} />
        <Toggle label="Trajectories" checked={options.trajectories} onChange={() => toggle('trajectories')} /><Toggle label="Camera FOV" checked={options.fov} onChange={() => toggle('fov')} />
        <Toggle label="Optical beam" checked={options.beam} onChange={() => toggle('beam')} /><Toggle label="Camera inset" checked={inset} onChange={() => setInset(!inset)} />
        <button className="scene-toggle" onClick={() => setOptions((value) => ({ ...value, resetKey: value.resetKey + 1 }))}>Reset view</button>
        <span className="scene-hint">Scroll or +/− to zoom · drag to orbit · right-drag to pan</span>
      </footer>
    </section>
    <aside className="sim-side">
      <section className="panel compact-panel simulation-controls-panel"><div className="panel-title">Simulation controls</div><SimulationControls state={state} send={send} /></section>
      <section className="panel compact-panel"><div className="panel-title">Camera / PTZ</div>
        <Readout label="Pan / tilt (actual)" value={`${state.camera.pan.toFixed(3)}° / ${state.camera.tilt.toFixed(3)}°`} />
        <Readout label="Pan / tilt rate" value={`${state.camera.pan_rate.toFixed(2)} / ${state.camera.tilt_rate.toFixed(2)} °/s`} />
        <Readout label="Rate limit" value={`${state.camera.max_pan_dps.toFixed(1)} / ${state.camera.max_tilt_dps.toFixed(1)} °/s${state.camera.rate_limited ? ' · saturated' : ''}`} />
        <Readout label="Resolution / FOV" value={`${state.camera.resolution[0]}×${state.camera.resolution[1]} · ${state.camera.fov_horizontal.toFixed(1)}°×${state.camera.fov_vertical.toFixed(1)}°`} />
        <Readout label="Update rate (set / measured)" value={`${state.camera.update_rate_hz} Hz / ${state.system.fps.toFixed(1)} Hz`} />
        <Readout label="Line-of-sight disturbance" value={`${state.camera.los_offset_px[0].toFixed(1)}, ${state.camera.los_offset_px[1].toFixed(1)} px`} />
        <Readout label="Pointing error (truth)" value={fmt(state.tracking.pixel_error, 2, ' px')} />
      </section>
      <section className="panel compact-panel"><div className="panel-title">Active disturbances</div>
        {state.disturbance_labels.length === 0 ? <p className="control-note">None — clear conditions. Change them under Settings or load a scenario.</p> : <ul className="chip-list">{state.disturbance_labels.map((label) => <li key={label}>{label}</li>)}</ul>}
        <p className="control-note">Atmosphere: {d.atmosphere.condition.replace('_', ' ')}{d.atmosphere.condition !== 'clear' ? ` ${d.atmosphere.intensity.toFixed(2)}` : ''} · the camera inset shows the exact frame the detector sees.</p>
      </section>
      <section className="panel events compact-events"><header className="panel-head"><div><h2>Event log</h2></div><span>{state.events.length}</span></header><EventList events={state.events} empty="Start the simulation to record events." /></section>
    </aside>
  </section>
}

export { CameraView }
