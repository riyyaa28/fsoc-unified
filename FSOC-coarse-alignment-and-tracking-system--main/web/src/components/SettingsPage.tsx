import { PATTERN_LABELS, type AppConfig, type BeaconConfig, type Command, type DeepPartial, type Telemetry } from '../types/simulation'
import { CheckRow, RangeRow, SelectRow } from './controls'

const PATTERNS = ['straight', 'circular', 'figure8', 'random', 'spiral', 'sinusoidal']
const RESOLUTIONS: [number, number][] = [[320, 240], [640, 480], [960, 720], [1280, 960]]

export function SettingsPage({ state, send }: { state: Telemetry; send: (command: Command) => void }) {
  const c = state.config
  const update = (patch: DeepPartial<AppConfig>) => send({ action: 'configure', config: patch })
  const beacon = (index: number, patch: Partial<BeaconConfig>) => update({ targets: { beacons: c.targets.beacons.map((_, i) => (i === index ? patch : {})) } })
  const d = c.disturbances
  return <section className="settings-layout">
    <p className="settings-note">Changes apply immediately to the running simulation (the value shown is the one the simulation is using). SIH reference values are marked <b>ref</b>.</p>
    <div className="settings-grid">
      <section className="panel compact-panel"><div className="panel-title">Camera</div>
        <SelectRow label="Resolution" hint="ref 640×480" value={c.camera.resolution.join('x')} onChange={(value) => update({ camera: { resolution: value.split('x').map(Number) as [number, number] } })}>{RESOLUTIONS.map(([w, h]) => <option key={w} value={`${w}x${h}`}>{w}×{h}</option>)}</SelectRow>
        <RangeRow label="Horizontal FOV" hint="ref 4°" value={c.camera.fov_h_deg} min={1} max={8} step={0.1} suffix="°" onCommit={(value) => update({ camera: { fov_h_deg: value } })} />
        <RangeRow label="Vertical FOV" hint="ref 3°" value={c.camera.fov_v_deg} min={0.75} max={6} step={0.05} suffix="°" onCommit={(value) => update({ camera: { fov_v_deg: value } })} />
        <RangeRow label="Update rate" hint="ref ≥ 30 Hz (min 20 Hz)" value={c.camera.update_rate_hz} min={20} max={60} step={1} suffix=" Hz" onCommit={(value) => update({ camera: { update_rate_hz: value } })} />
        <SelectRow label="Initial camera position" hint="ref screen centre; applied on Reset" value={c.camera.initial_position.mode} onChange={(value) => update({ camera: { initial_position: { mode: value as 'center' | 'custom' } } })}><option value="center">Screen centre</option><option value="custom">Custom</option></SelectRow>
        {c.camera.initial_position.mode === 'custom' && <>
          <RangeRow label="Initial X" value={c.camera.initial_position.x_pct} min={0} max={100} step={1} suffix="%" onCommit={(value) => update({ camera: { initial_position: { x_pct: value } } })} />
          <RangeRow label="Initial Y" value={c.camera.initial_position.y_pct} min={0} max={100} step={1} suffix="%" onCommit={(value) => update({ camera: { initial_position: { y_pct: value } } })} />
        </>}
      </section>
      <section className="panel compact-panel"><div className="panel-title">PTZ motion and tracking</div>
        <RangeRow label="Max pan speed" hint="ref 5–10 °/s" value={c.camera.max_pan_dps} min={1} max={20} step={0.5} suffix=" °/s" onCommit={(value) => update({ camera: { max_pan_dps: value } })} />
        <RangeRow label="Max tilt speed" hint="ref 5–10 °/s" value={c.camera.max_tilt_dps} min={1} max={20} step={0.5} suffix=" °/s" onCommit={(value) => update({ camera: { max_tilt_dps: value } })} />
        <SelectRow label="Acquisition aid" hint="Cue = coarse GPS/telemetry position" value={c.tracking.acquisition} onChange={(value) => update({ tracking: { acquisition: value as 'cue' | 'search' } })}><option value="cue">Position cue</option><option value="search">Spiral search (no cue)</option></SelectRow>
        {c.tracking.acquisition === 'cue' && <RangeRow label="Cue error (1σ)" value={c.tracking.cue_error_deg} min={0} max={1.5} step={0.05} suffix="°" onCommit={(value) => update({ tracking: { cue_error_deg: value } })} />}
        <RangeRow label="Link tolerance" hint="CONNECTED when error ≤ this" value={c.tracking.link_threshold_px} min={2} max={40} step={1} suffix=" px" onCommit={(value) => update({ tracking: { link_threshold_px: value } })} />
        <RangeRow label="Lock confirmation" value={c.tracking.confirm_frames} min={1} max={10} step={1} suffix=" frames" onCommit={(value) => update({ tracking: { confirm_frames: value } })} />
        <RangeRow label="Kalman coast limit" value={c.tracking.coast_frames} min={0} max={60} step={1} suffix=" frames" onCommit={(value) => update({ tracking: { coast_frames: value } })} />
        <SelectRow label="Detector" hint="YOLO needs ultralytics installed" value={c.tracking.detector} onChange={(value) => update({ tracking: { detector: value as 'classical' | 'yolo' } })}><option value="classical">Classical ring signature</option><option value="yolo">YOLO (beacon_yolo.pt)</option></SelectRow>
        <RangeRow label="Simulation speed" value={c.simulation.speed_multiplier} min={0.25} max={4} step={0.25} suffix="×" onCommit={(value) => update({ simulation: { speed_multiplier: value } })} />
      </section>
      <section className="panel compact-panel"><div className="panel-title">Targets</div>
        <RangeRow label="Number of beacons" value={c.targets.count} min={1} max={6} step={1} onCommit={(value) => update({ targets: { count: value } })} />
        <RangeRow label="Target size" hint="ref 5–20 px, default 10" value={c.targets.size_px} min={5} max={20} step={1} suffix=" px" onCommit={(value) => update({ targets: { size_px: value } })} />
        <RangeRow label="Decoys (clutter)" value={c.targets.decoy_count} min={0} max={12} step={1} onCommit={(value) => update({ targets: { decoy_count: value } })} />
        <table className="beacon-table"><thead><tr><th>Beacon</th><th>Motion</th><th>Speed</th><th>Path size</th><th>Start X/Y %</th></tr></thead><tbody>
          {c.targets.beacons.slice(0, c.targets.count).map((b, index) => <tr key={b.id} className={b.id === c.comm.target_id ? 'selected' : ''}>
            <td>Beacon {index + 1}</td>
            <td><select aria-label={`Beacon ${index + 1} motion`} value={b.pattern} onChange={(event) => beacon(index, { pattern: event.target.value })}>{PATTERNS.map((p) => <option key={p} value={p}>{PATTERN_LABELS[p]}</option>)}</select></td>
            <td><select aria-label={`Beacon ${index + 1} speed`} value={b.speed} onChange={(event) => beacon(index, { speed: Number(event.target.value) })}>{[0.5, 0.75, 1, 1.5, 2, 3].map((v) => <option key={v} value={v}>{v}×</option>)}</select></td>
            <td><input aria-label={`Beacon ${index + 1} path size`} type="number" min={20} max={200} value={b.size} onChange={(event) => beacon(index, { size: Number(event.target.value) })} /></td>
            <td className="xy"><input aria-label={`Beacon ${index + 1} start X`} type="number" min={5} max={95} value={b.center_pct[0]} onChange={(event) => beacon(index, { center_pct: [Number(event.target.value), b.center_pct[1]] })} /><input aria-label={`Beacon ${index + 1} start Y`} type="number" min={5} max={95} value={b.center_pct[1]} onChange={(event) => beacon(index, { center_pct: [b.center_pct[0], Number(event.target.value)] })} /></td>
          </tr>)}
        </tbody></table>
        <p className="control-note">Start X/Y is the centre of each beacon's path in the field of regard. Select the beacon to track in Mission Control.</p>
      </section>
      <section className="panel compact-panel"><div className="panel-title">Image / sensor noise</div>
        <RangeRow label="Gaussian (σ up to 90 grey levels)" value={d.noise.gaussian} min={0} max={1} step={0.05} onCommit={(value) => update({ disturbances: { noise: { gaussian: value } } })} />
        <RangeRow label="Salt & pepper (up to 35 % pixels)" value={d.noise.salt_pepper} min={0} max={1} step={0.05} onCommit={(value) => update({ disturbances: { noise: { salt_pepper: value } } })} />
        <RangeRow label="Poisson shot noise" value={d.noise.poisson} min={0} max={1} step={0.05} onCommit={(value) => update({ disturbances: { noise: { poisson: value } } })} />
        <div className="panel-title spaced">Camera jitter and platform motion</div>
        <RangeRow label="Camera jitter" hint="ref ≤ ±20 px/frame, random each frame" value={d.jitter.amplitude_px} min={0} max={20} step={1} suffix=" px" onCommit={(value) => update({ disturbances: { jitter: { amplitude_px: value } } })} />
        <SelectRow label="Platform motion" value={d.platform.pattern} onChange={(value) => update({ disturbances: { platform: { pattern: value } } })}>{['none', 'linear', 'circular', 'random', 'spiral', 'figure8'].map((p) => <option key={p} value={p}>{p === 'none' ? 'None' : p === 'figure8' ? 'Figure-8' : p[0].toUpperCase() + p.slice(1)}</option>)}</SelectRow>
        <RangeRow label="Platform amplitude" hint="ref ≤ ±20 px" value={d.platform.amplitude_px} min={0} max={20} step={1} suffix=" px" onCommit={(value) => update({ disturbances: { platform: { amplitude_px: value } } })} />
        <RangeRow label="Platform period" value={d.platform.period_s} min={0.5} max={20} step={0.5} suffix=" s" onCommit={(value) => update({ disturbances: { platform: { period_s: value } } })} />
      </section>
      <section className="panel compact-panel"><div className="panel-title">Atmosphere</div>
        <div className="segmented" role="radiogroup" aria-label="Atmospheric condition">{['clear', 'haze', 'fog', 'rain', 'low_light'].map((cond) => <button key={cond} role="radio" aria-checked={d.atmosphere.condition === cond} className={d.atmosphere.condition === cond ? 'selected' : ''} onClick={() => update({ disturbances: { atmosphere: { condition: cond } } })}>{cond === 'low_light' ? 'Low light' : cond[0].toUpperCase() + cond.slice(1)}</button>)}</div>
        <RangeRow label="Intensity" value={d.atmosphere.intensity} min={0} max={1} step={0.05} onCommit={(value) => update({ disturbances: { atmosphere: { intensity: value } } })} hint="Contrast, veil, blur and beacon attenuation" />
        <RangeRow label="Turbulence (scintillation)" value={d.turbulence.strength} min={0} max={1} step={0.05} onCommit={(value) => update({ disturbances: { turbulence: { strength: value } } })} hint="Beacon flicker, wander and blur" />
        <CheckRow label="Line-of-sight obstacle" checked={c.environment.obstacle_enabled} onChange={(value) => update({ environment: { obstacle_enabled: value } })} hint="Blocks the beacon when it passes behind" />
        <p className="control-note">These are image-domain models of each condition, applied to the camera frame before detection — watch the camera view change.</p>
      </section>
      <section className="panel compact-panel"><div className="panel-title">Mission and geometry</div>
        <SelectRow label="Tracking target" value={c.comm.target_id} onChange={(value) => update({ comm: { target_id: value } })}>{c.targets.beacons.slice(0, c.targets.count).map((b, index) => <option key={b.id} value={b.id}>Beacon {index + 1}</option>)}</SelectRow>
        <SelectRow label="Start mode" value={c.comm.mode} onChange={(value) => update({ comm: { mode: value as 'auto' | 'manual' } })}><option value="auto">Auto connect</option><option value="manual">Manual connect</option></SelectRow>
        <div className="panel-title spaced">Obstacle geometry</div>
        <RangeRow label="Obstacle X" value={c.environment.obstacle_center[0]} min={-84} max={84} step={1} onCommit={(value) => update({ environment: { obstacle_center: [value, c.environment.obstacle_center[1], c.environment.obstacle_center[2]] } })} />
        <RangeRow label="Obstacle Y" value={c.environment.obstacle_center[1]} min={0} max={30} step={1} onCommit={(value) => update({ environment: { obstacle_center: [c.environment.obstacle_center[0], value, c.environment.obstacle_center[2]] } })} />
        <RangeRow label="Obstacle Z" value={c.environment.obstacle_center[2]} min={-53} max={53} step={1} onCommit={(value) => update({ environment: { obstacle_center: [c.environment.obstacle_center[0], c.environment.obstacle_center[1], value] } })} />
        <RangeRow label="Obstacle width" value={c.environment.obstacle_size[0]} min={1} max={80} step={1} onCommit={(value) => update({ environment: { obstacle_size: [value, c.environment.obstacle_size[1], c.environment.obstacle_size[2]] } })} />
        <RangeRow label="Obstacle height" value={c.environment.obstacle_size[1]} min={1} max={40} step={1} onCommit={(value) => update({ environment: { obstacle_size: [c.environment.obstacle_size[0], value, c.environment.obstacle_size[2]] } })} />
        <RangeRow label="Obstacle depth" value={c.environment.obstacle_size[2]} min={1} max={80} step={1} onCommit={(value) => update({ environment: { obstacle_size: [c.environment.obstacle_size[0], c.environment.obstacle_size[1], value] } })} />
      </section>
      <section className="panel compact-panel"><div className="panel-title">Video benchmark</div>
        <CheckRow label="Apply scenario disturbances" checked={c.video.apply_disturbances} onChange={(value) => update({ video: { apply_disturbances: value } })} hint="Uses the selected simulation noise and atmosphere while processing video" />
      </section>
    </div>
  </section>
}
