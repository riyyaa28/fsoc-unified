import { useState } from 'react'
import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { fmt, type Command, type Telemetry, type VideoState } from '../types/simulation'
import { CheckRow, EventList, SelectRow, SihGrid } from './controls'

function upload(file: File, onProgress: (fraction: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `/api/video/upload?filename=${encodeURIComponent(file.name)}`)
    xhr.setRequestHeader('Content-Type', 'application/octet-stream')
    xhr.upload.onprogress = (event) => event.lengthComputable && onProgress(event.loaded / event.total)
    xhr.onload = () => xhr.status < 300 ? resolve() : reject(new Error(safeDetail(xhr.responseText)))
    xhr.onerror = () => reject(new Error('Upload failed: backend unreachable.'))
    xhr.send(file)
  })
}

function safeDetail(text: string) { try { return (JSON.parse(text) as { detail?: string }).detail ?? text } catch { return text } }

async function post(path: string, body?: unknown) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  if (!response.ok) throw new Error(safeDetail(await response.text()))
  return response.json()
}

const Row = ({ label, value }: { label: string; value: string }) => <div className="readout"><span>{label}</span><strong>{value}</strong></div>

export function VideoBenchmark({ state, send, onError }: { state: Telemetry; send: (command: Command) => void; onError: (message: string) => void }) {
  const video: VideoState | undefined = state.video
  const [progress, setProgress] = useState<number | null>(null)
  const [pacing, setPacing] = useState<'realtime' | 'max'>('realtime')
  const [busy, setBusy] = useState(false)
  const status = video?.status ?? 'EMPTY'
  const run = async (task: () => Promise<unknown>) => { setBusy(true); try { await task() } catch (error) { onError(error instanceof Error ? error.message : 'Video request failed.') } finally { setBusy(false) } }
  const onFile = (file: File | undefined) => file && run(async () => { setProgress(0); try { await upload(file, setProgress) } finally { setProgress(null) } })
  const m = video?.summary
  const c = video?.current ?? {}
  const series = (video?.series ?? []).map((point) => ({ t: point.t, error: point.error }))
  const meta = video?.meta ?? {}
  return <section className="video-layout">
    <section className="panel video-panel">
      <header className="panel-head"><div><span className="eyebrow">Evaluator MP4 · same tracking core as the 3D simulation · physical PTZ bypassed</span><h2>Video benchmark</h2></div><span className={`status-tag ${status === 'COMPLETE' ? 'good' : status === 'PROCESSING' ? 'predicting' : status === 'ERROR' ? 'bad' : ''}`}>{status}</span></header>
      <div className="video-frame" style={{ aspectRatio: meta.width && meta.height ? `${meta.width} / ${meta.height}` : '4 / 3' }}>
        {video?.image ? <img src={video.image} alt="Processed video frame with detected centroid and virtual camera centre" /> : <label className="dropzone"><input type="file" accept=".mp4,.avi,.mov,.mkv,video/mp4" onChange={(event) => onFile(event.target.files?.[0])} /><strong>Upload an .mp4 video</strong><span>30 FPS evaluator footage is processed frame-by-frame</span></label>}
        {video?.progress != null && status === 'PROCESSING' && <div className="video-progress"><i style={{ width: `${video.progress * 100}%` }} /></div>}
      </div>
      <footer className="camera-legend boresight-legend"><span><i className="centre" />Virtual camera centre</span><span><i className="frame-centre" />Frame centre</span><span><i className="measured" />Detected beacon</span><span><i className="predicted" />Kalman track</span></footer>
      <p className="video-message">{progress !== null ? `Uploading… ${(progress * 100).toFixed(0)}%` : video?.message ?? 'Upload an .mp4 file to start a benchmark.'}</p>
    </section>
    <aside className="video-side">
      <section className="panel compact-panel"><div className="panel-title">Input</div>
        <label className="action file-action"><input type="file" accept=".mp4,.avi,.mov,.mkv,video/mp4" disabled={busy || status === 'PROCESSING'} onChange={(event) => { onFile(event.target.files?.[0]); event.target.value = '' }} />{status === 'EMPTY' ? 'Choose video…' : 'Replace video…'}</label>
        {meta.filename && <>
          <Row label="File" value={meta.filename} />
          <Row label="Resolution" value={`${meta.width}×${meta.height}`} />
          <Row label="Frame rate" value={`${meta.fps?.toFixed(2)} FPS${meta.is_30fps ? ' ✓ 30 FPS' : meta.fps_reported ? ' (not 30 FPS)' : ' (assumed)'}`} />
          <Row label="Frames / duration" value={`${meta.frames ?? '?'} / ${fmt(meta.duration_s ?? null, 2, ' s')}`} />
        </>}
        <SelectRow label="Pacing" value={pacing} onChange={(value) => setPacing(value as 'realtime' | 'max')} hint="Real time plays at the video FPS"><option value="realtime">Real time</option><option value="max">As fast as possible</option></SelectRow>
        <CheckRow label="Apply scenario disturbances" checked={state.config.video.apply_disturbances} onChange={(value) => send({ action: 'configure', config: { video: { apply_disturbances: value } } })} hint={`Uses "${state.scenario.name}" noise/atmosphere on the video`} />
        <div className="button-grid two">
          <button className="action primary" disabled={busy || status === 'EMPTY' || status === 'PROCESSING'} onClick={() => run(() => post('/api/video/start', { pacing }))}>{status === 'COMPLETE' || status === 'STOPPED' ? 'Re-run' : 'Process video'}</button>
          <button className="action" disabled={status !== 'PROCESSING'} onClick={() => run(() => post('/api/video/stop'))}>Stop</button>
          <button className="action" disabled={busy || !(status === 'COMPLETE' || status === 'STOPPED')} onClick={() => run(async () => { const report = await post('/api/video/report') as { pdf: string }; window.open(report.pdf, '_blank', 'noopener') })}>Generate performance report</button>
          <button className="action muted" disabled={busy || status === 'EMPTY' || status === 'PROCESSING'} onClick={() => run(async () => { const r = await fetch('/api/video', { method: 'DELETE' }); if (!r.ok) throw new Error('Could not clear video') })}>Clear</button>
        </div>
        {video?.report && <p className="report-links">Report: <a href={video.report.pdf} target="_blank" rel="noreferrer">PDF</a><a href={video.report.csv} download>CSV (per frame)</a><a href={video.report.json} download>JSON</a></p>}
      </section>
      <section className="panel compact-panel"><div className="panel-title">Current frame</div>
        <Row label="Frame / time" value={c.frame != null ? `${c.frame} / ${c.time_s?.toFixed(2)} s` : '—'} />
        <Row label="Track state" value={c.state ?? '—'} />
        <Row label="Beacon centroid (x, y)" value={c.centroid_x != null ? `(${c.centroid_x.toFixed(1)}, ${c.centroid_y?.toFixed(1)})` : 'not detected'} />
        <Row label="Camera centre (x, y)" value={c.camera_centre_x != null ? `(${c.camera_centre_x.toFixed(1)}, ${c.camera_centre_y?.toFixed(1)})` : '—'} />
        <Row label="Centroid error" value={fmt(c.tracking_error_px ?? null, 2, ' px')} />
        <Row label="Offset from frame centre" value={fmt(c.offset_from_frame_centre_px ?? null, 2, ' px')} />
        <Row label="Pan / tilt command" value={c.pan_cmd_deg != null ? `${c.pan_cmd_deg.toFixed(3)}° / ${c.tilt_cmd_deg?.toFixed(3)}°` : '—'} />
        <Row label="Processing" value={fmt(c.processing_ms ?? null, 2, ' ms')} />
      </section>
    </aside>
    <section className="panel compact-panel video-metrics">
      <div className="panel-title">Measured against SIH limits</div>
      {m ? <SihGrid items={m.sih} /> : <p className="control-note">Metrics appear once processing starts.</p>}
      {m && <div className="metric-table">
        <Row label="Processing rate" value={`${fmt(m.processing_fps, 1, ' FPS')} (mean ${fmt(m.processing_ms_mean, 2, ' ms')})`} />
        <Row label="Acquisition time" value={fmt(m.acquisition_time_s, 3, ' s')} />
        <Row label="Re-acquisition (mean / max)" value={`${fmt(m.reacquisition_time_mean_s, 3, ' s')} / ${fmt(m.reacquisition_time_max_s, 3, ' s')} (${m.reacquisition_count})`} />
        <Row label="Error mean / max / RMSE" value={`${fmt(m.error_mean_px)} / ${fmt(m.error_max_px)} / ${fmt(m.error_rmse_px)} px`} />
        <Row label="Target loss / lock retention" value={`${fmt(m.target_loss_pct, 2, '%')} / ${fmt(m.lock_retention_pct, 2, '%')}`} />
        <Row label="Frames processed" value={`${m.frames}`} />
      </div>}
      <div className="chart-box"><ResponsiveContainer width="100%" height={170}><LineChart data={series} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
        <XAxis dataKey="t" tick={{ fill: '#7891a3', fontSize: 10 }} tickFormatter={(value) => `${Number(value).toFixed(1)}s`} minTickGap={30} />
        <YAxis tick={{ fill: '#7891a3', fontSize: 10 }} width={42} />
        <Tooltip contentStyle={{ background: '#081722', border: '1px solid #31576b', fontSize: 11 }} formatter={(value) => [`${Number(value).toFixed(2)} px`, 'Centroid error']} labelFormatter={(value) => `${Number(value).toFixed(2)} s`} />
        <ReferenceLine y={state.sih_limits.tracking_error_px} stroke="#e6c378" strokeDasharray="4 3" />
        <Line dataKey="error" stroke="#75e2f6" dot={false} strokeWidth={1.6} isAnimationActive={false} connectNulls={false} />
      </LineChart></ResponsiveContainer><span className="chart-note">Centroid-to-camera-centre error; dashed line = 10 px SIH limit; gaps = no detection</span></div>
    </section>
    <section className="panel events compact-events video-events"><header className="panel-head"><div><h2>Benchmark events</h2></div></header><EventList events={video?.events ?? []} empty="No benchmark events yet." /></section>
  </section>
}
