import { useEffect, useState } from 'react'
import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { fmt, type HistoryPoint, type PerformanceSummary, type Telemetry } from '../types/simulation'
import { SihGrid } from './controls'

type ChartLine = { dataKey: keyof HistoryPoint | 'link' | 'lock'; color: string; name: string }
type Datum = HistoryPoint & { link: number; lock: number }

function Chart({ title, data, lines, domain, limit }: { title: string; data: Datum[]; lines: ChartLine[]; domain?: [number, number]; limit?: number }) {
  return <article className="panel chart"><header><span>{title}</span></header><ResponsiveContainer width="100%" height={190}><LineChart data={data} margin={{ top: 10, right: 12, bottom: 0, left: -8 }}>
    <XAxis dataKey="timestamp" tick={{ fill: '#7891a3', fontSize: 10 }} tickFormatter={(value) => `${Number(value).toFixed(0)}s`} minTickGap={35} axisLine={false} tickLine={false} />
    <YAxis domain={domain} tick={{ fill: '#7891a3', fontSize: 10 }} width={42} axisLine={false} tickLine={false} />
    <Tooltip contentStyle={{ background: '#081722', border: '1px solid #31576b', borderRadius: 6, fontSize: 11 }} labelFormatter={(value) => `${Number(value).toFixed(2)} s`} formatter={(value) => typeof value === 'number' ? value.toFixed(3) : value} />
    {limit !== undefined && <ReferenceLine y={limit} stroke="#e6c378" strokeDasharray="4 3" />}
    {lines.map((line) => <Line key={line.dataKey} type="monotone" dataKey={line.dataKey} name={line.name} stroke={line.color} strokeWidth={1.7} dot={false} isAnimationActive={false} connectNulls={false} />)}
  </LineChart></ResponsiveContainer><footer>{lines.map((line) => <span key={line.name}><i style={{ background: line.color }} />{line.name}</span>)}</footer></article>
}

const Row = ({ label, value }: { label: string; value: string }) => <div className="readout"><span>{label}</span><strong>{value}</strong></div>

export function PerformancePage({ state, onError }: { state: Telemetry; onError: (message: string) => void }) {
  const [sessions, setSessions] = useState<PerformanceSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [links, setLinks] = useState<{ pdf: string; csv: string; json: string } | null>(null)
  const p = state.performance
  const refresh = async () => { try { const response = await fetch('/api/performance/sessions'); if (!response.ok) throw new Error('Could not load session history.'); setSessions(await response.json() as PerformanceSummary[]) } catch (error) { onError(error instanceof Error ? error.message : 'Session history failed.') } }
  useEffect(() => { void refresh() }, [])
  const generate = async () => {
    setLoading(true)
    try {
      const response = await fetch(`/api/performance/${p.session_id}/generate-pdf`, { method: 'POST' })
      if (!response.ok) { const text = await response.text(); throw new Error((() => { try { return (JSON.parse(text) as { detail: string }).detail } catch { return text } })()) }
      const result = await response.json() as { pdf: string; csv: string; json: string }
      setLinks(result); await refresh(); window.open(result.pdf, '_blank', 'noopener')
    } catch (error) { onError(error instanceof Error ? error.message : 'Report generation failed.') } finally { setLoading(false) }
  }
  const data: Datum[] = state.history.map((point) => ({ ...point, link: point.fsoc_link ? 1 : 0, lock: point.locked ? 1 : 0 }))
  return <section className="performance-layout">
    <section className="panel compact-panel perf-head">
      <header className="panel-head flush"><div><span className="eyebrow">3D simulation · session {p.session_id}</span><h2>SIH reference compliance</h2></div>
        <div className="head-actions"><button className="action primary" disabled={loading || !p.frames} onClick={() => void generate()}>{loading ? 'Generating…' : 'Generate performance report'}</button>
          {links && <span className="report-links"><a href={links.pdf} target="_blank" rel="noreferrer">PDF</a><a href={links.csv} download>CSV</a><a href={links.json} download>JSON</a></span>}</div></header>
      {p.frames ? <SihGrid items={p.sih ?? []} /> : <p className="control-note">Start the simulation and connect to a beacon — metrics are measured from the running session, never preset. For uploaded videos see the Video Benchmark tab.</p>}
      <div className="metric-table columns">
        <Row label="Session duration" value={fmt(p.duration_s, 2, ' s')} />
        <Row label="Loop rate (measured)" value={fmt(p.loop_fps, 1, ' Hz')} />
        <Row label="Processing time mean / max" value={`${fmt(p.processing_ms_mean, 2)} / ${fmt(p.processing_ms_max, 2)} ms`} />
        <Row label="Processing rate" value={fmt(p.processing_fps, 1, ' FPS')} />
        <Row label="Acquisition (first / all)" value={`${fmt(p.acquisition_time_s, 3, ' s')} / ${(p.acquisition_times_s ?? []).map((v) => v.toFixed(2)).join(', ') || '—'}`} />
        <Row label="Re-acquisition mean / max" value={`${fmt(p.reacquisition_time_mean_s, 3)} / ${fmt(p.reacquisition_time_max_s, 3)} s (${p.reacquisition_count ?? 0})`} />
        <Row label="Tracking error mean / max" value={`${fmt(p.error_mean_px)} / ${fmt(p.error_max_px)} px`} />
        <Row label="RMSE" value={fmt(p.error_rmse_px, 2, ' px')} />
        <Row label="Target loss" value={fmt(p.target_loss_pct, 2, '%')} />
        <Row label="Lock retention" value={fmt(p.lock_retention_pct, 2, '%')} />
        <Row label="Communication connected" value={fmt(p.fsoc_link_retention_rate as number | undefined, 1, '%')} />
        <Row label="Beacon hidden (excluded)" value={fmt(p.hidden_time_s, 2, ' s')} />
      </div>
      <p className="control-note">Tracking error is the ground-truth distance between the selected beacon and the camera centre, in camera pixels, on post-acquisition locked frames. Acquisition ends when the lock is centred within the link tolerance.</p>
    </section>
    <section className="chart-grid">
      <Chart title="Pointing error (px) — dashed = 10 px limit" data={data} lines={[{ dataKey: 'pixel_error', color: '#ff997c', name: 'Pointing error' }]} limit={state.sih_limits.tracking_error_px} />
      <Chart title="Camera vs target angle" data={data} lines={[{ dataKey: 'target_azimuth', color: '#75e2f6', name: 'Target az' }, { dataKey: 'camera_pan', color: '#f2ad6a', name: 'Camera pan' }, { dataKey: 'target_elevation', color: '#ac94f4', name: 'Target el' }, { dataKey: 'camera_tilt', color: '#61d09a', name: 'Camera tilt' }]} />
      <Chart title="Pan / tilt rate (°/s, rate-limited)" data={data} lines={[{ dataKey: 'pan_rate', color: '#f2ad6a', name: 'Pan rate' }, { dataKey: 'tilt_rate', color: '#61d09a', name: 'Tilt rate' }]} />
      <Chart title="Lock and link state" data={data} domain={[0, 1]} lines={[{ dataKey: 'lock', color: '#70c5ff', name: 'Locked' }, { dataKey: 'link', color: '#61dfa1', name: 'Connected' }]} />
      <Chart title="Processing time (ms)" data={data} lines={[{ dataKey: 'processing_time_ms', color: '#a5b3c0', name: 'Processing' }]} />
      <Chart title="Detection score" data={data} domain={[0, 1]} lines={[{ dataKey: 'confidence', color: '#61d9a4', name: 'Score' }]} />
    </section>
    <section className="panel reports"><header className="panel-head"><div><h2>Report sessions</h2></div><button className="scene-toggle" onClick={() => void refresh()}>Refresh</button></header>
      <div className="session-list">{sessions.length === 0 ? <p>Sessions appear after generating a report or pressing Reset.</p> : sessions.slice(0, 12).map((session) => <article key={session.session_id}><div><strong>{session.source === 'video_benchmark' ? 'Video' : 'Simulation'} · {session.session_id}</strong><span>{fmt(session.duration_s ?? session.duration ?? null, 1, ' s')} · error {fmt(session.error_mean_px ?? null, 2, ' px')} · loss {fmt(session.target_loss_pct ?? null, 1, '%')}{session.scenario ? ` · ${session.scenario}` : ''}</span></div><div><a href={`/api/performance/${session.session_id}/pdf`} target="_blank" rel="noreferrer">PDF</a><a href={`/api/performance/${session.session_id}/csv`} download>CSV</a><a href={`/api/performance/${session.session_id}/json`} download>JSON</a></div></article>)}</div>
    </section>
  </section>
}
