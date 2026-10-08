import { useEffect, useState } from 'react'
import { PATTERN_LABELS, type AppConfig, type Command, type Telemetry } from '../types/simulation'

type Preset = { id: string; name: string; description: string; config: Partial<AppConfig> }
type Saved = { name: string; saved_at: string; config: AppConfig }

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) { const text = await response.text(); try { throw new Error((JSON.parse(text) as { detail: string }).detail) } catch (e) { throw e instanceof Error && e.message !== text ? e : new Error(text) } }
  return response.json() as Promise<T>
}

export function ScenarioPage({ state, send, onError }: { state: Telemetry; send: (command: Command) => void; onError: (message: string) => void }) {
  const [presets, setPresets] = useState<Preset[]>([])
  const [saved, setSaved] = useState<Saved[]>([])
  const [name, setName] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const refresh = async () => { try { setPresets(await json<Preset[]>('/api/presets')); setSaved(await json<Saved[]>('/api/scenarios')) } catch (error) { onError(error instanceof Error ? error.message : 'Unable to load scenarios.') } }
  useEffect(() => { void refresh() }, [])
  // Clear the "loading" marker once the server reports the new scenario.
  useEffect(() => { if (pending && state.scenario.name === pending) setPending(null) }, [pending, state.scenario.name])
  const loadPreset = (preset: Preset) => { setPending(preset.name); send({ action: 'load_preset', preset_id: preset.id }) }
  const loadSaved = async (entry: Saved) => { setPending(entry.name); try { await json(`/api/scenarios/${encodeURIComponent(entry.name)}/apply`, { method: 'POST' }) } catch (error) { setPending(null); onError(error instanceof Error ? error.message : 'Unable to load scenario.') } }
  const save = async () => { const trimmed = name.trim(); if (!trimmed) { onError('Enter a scenario name before saving.'); return } try { await json(`/api/scenarios/${encodeURIComponent(trimmed)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state.config) }); setName(''); await refresh() } catch (error) { onError(error instanceof Error ? error.message : 'Unable to save scenario.') } }
  const remove = async (entry: Saved) => { try { await json(`/api/scenarios/${encodeURIComponent(entry.name)}`, { method: 'DELETE' }); await refresh() } catch (error) { onError(error instanceof Error ? error.message : 'Unable to delete scenario.') } }
  const s = state.scenario
  const c = state.config
  const beacon = c.targets.beacons.find((b) => b.id === c.comm.target_id)
  const applied: [string, string][] = [
    ['Beacons', `${c.targets.count} (selected ${state.comm.target_label}, ${PATTERN_LABELS[beacon?.pattern ?? ''] ?? '—'})`],
    ['Target size', `${c.targets.size_px.toFixed(0)}×${c.targets.size_px.toFixed(0)} px, ${c.targets.decoy_count} decoys`],
    ['Camera', `${c.camera.resolution[0]}×${c.camera.resolution[1]}, ${c.camera.fov_h_deg.toFixed(1)}°×${c.camera.fov_v_deg.toFixed(1)}°, ${c.camera.update_rate_hz} Hz`],
    ['PTZ limits', `pan ${c.camera.max_pan_dps.toFixed(1)}°/s, tilt ${c.camera.max_tilt_dps.toFixed(1)}°/s`],
    ['Disturbances', state.disturbance_labels.join(', ') || 'none (clear)'],
  ]
  return <section className="scenario-layout">
    <section className={`panel scenario-status ${pending ? 'loading' : ''}`} aria-live="polite">
      <div className="scenario-status-main">
        <span className="eyebrow">Current scenario</span>
        <h2>{pending ? `Loading "${pending}"…` : s.name}</h2>
        <p>{s.description}{s.modified ? ' — settings changed after loading.' : ''}</p>
      </div>
      <dl className="scenario-flags">
        <div className="ok"><dt>Status</dt><dd>{pending ? 'Loading…' : 'Scenario loaded ✓'}</dd></div>
        <div className={s.status === 'ACTIVE' ? 'ok' : 'idle'}><dt>3D simulation</dt><dd>{s.status === 'ACTIVE' ? 'Active ✓ (running)' : 'Applied ✓ (paused — press Start)'}</dd></div>
        <div className={s.applies_to.video ? 'ok' : 'idle'}><dt>2D video mode</dt><dd>{s.applies_to.video ? 'Disturbances applied to video ✓' : 'Available ✓ (raw video; toggle in Video tab)'}</dd></div>
        <div className={s.modified ? 'warn' : 'ok'}><dt>Parameters</dt><dd>{s.modified ? 'Modified after load' : 'As loaded'}</dd></div>
      </dl>
      <table className="applied-table"><tbody>{applied.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}</tbody></table>
    </section>
    <section className="panel scenario-presets"><header className="panel-head"><div><h2>Evaluation scenarios</h2></div><span className="head-note">Loading resets the run so every value applies from t = 0</span></header>
      <div className="preset-grid">{presets.map((preset) => {
        const current = s.source === 'preset' && s.id === preset.id
        return <article key={preset.id} className={current ? 'current' : ''}>
          <strong>{preset.name}</strong><p>{preset.description}</p>
          <button className={`action ${current ? '' : 'primary'}`} onClick={() => loadPreset(preset)}>{current ? (s.modified ? 'Reload' : 'Loaded ✓ — reload') : 'Load scenario'}</button>
        </article>
      })}</div>
    </section>
    <section className="panel scenario-library"><header className="panel-head"><div><h2>Saved scenarios</h2></div><button className="scene-toggle" onClick={() => void refresh()}>Refresh</button></header>
      <div className="scenario-save"><input value={name} maxLength={48} onChange={(event) => setName(event.target.value.replace(/[^A-Za-z0-9_-]/g, ''))} placeholder="name_for_current_settings" aria-label="Scenario name" /><button className="action primary" onClick={() => void save()}>Save current settings</button><button className="action muted" onClick={() => send({ action: 'reset_defaults' })}>Restore defaults</button></div>
      <div className="scenario-list">{saved.length === 0 ? <p>No saved scenario yet. Adjust Settings, then save them here.</p> : saved.map((entry) => <article key={entry.name}><div><strong>{entry.name}</strong><span>{new Date(entry.saved_at).toLocaleString()}</span></div><div><button className="scene-toggle" onClick={() => void loadSaved(entry)}>{s.source === 'saved' && s.name === entry.name ? 'Loaded ✓' : 'Load'}</button><button className="scene-toggle danger" onClick={() => void remove(entry)}>Delete</button></div></article>)}</div>
    </section>
  </section>
}
