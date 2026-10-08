import { useEffect, useState, type ReactNode } from 'react'
import type { SihItem } from '../types/simulation'

export function SelectRow({ label, value, children, onChange, hint }: { label: string; value: string | number; children: ReactNode; onChange: (value: string) => void; hint?: string }) {
  return <label className="control-row"><span>{label}{hint && <small className="hint">{hint}</small>}</span><select value={value} onChange={(event) => onChange(event.target.value)}>{children}</select></label>
}

/** Slider that shows the authoritative server value, keeps a local draft while
 * dragging, and commits once on release so the simulation is not flooded. */
export function RangeRow({ label, value, min, max, step = 0.01, suffix = '', digits, onCommit, hint }: { label: string; value: number; min: number; max: number; step?: number; suffix?: string; digits?: number; onCommit: (value: number) => void; hint?: string }) {
  const [draft, setDraft] = useState<number | null>(null)
  useEffect(() => { setDraft(null) }, [value])
  const shown = draft ?? value
  const places = digits ?? (step < 1 ? (step < 0.1 ? 2 : 1) : 0)
  const commit = () => { if (draft !== null && draft !== value) onCommit(draft) }
  return <label className="range-row"><span>{label}<small>{shown.toFixed(places)}{suffix}</small></span>
    <input type="range" min={min} max={max} step={step} value={shown} onChange={(event) => setDraft(Number(event.target.value))} onPointerUp={commit} onKeyUp={commit} onBlur={commit} />
    {hint && <em className="hint">{hint}</em>}</label>
}

export function CheckRow({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (value: boolean) => void; hint?: string }) {
  return <label className="check-row"><span>{label}{hint && <small className="hint">{hint}</small>}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /></label>
}

export function SihBadge({ item }: { item: SihItem }) {
  const state = item.pass === null ? 'pending' : item.pass ? 'pass' : 'fail'
  const value = item.value == null ? 'not measured' : `${item.value.toFixed(item.unit === 'FPS' ? 1 : 2)} ${item.unit}`
  return <article className={`sih-item ${state}`}>
    <span className="sih-label">{item.label}</span>
    <strong>{value}</strong>
    <span className="sih-limit">limit {item.op} {item.limit} {item.unit}</span>
    <b>{state === 'pending' ? 'Awaiting data' : state === 'pass' ? 'Pass' : 'Fail'}</b>
  </article>
}

export function SihGrid({ items, compact = false }: { items: SihItem[]; compact?: boolean }) {
  return <section className={`sih-grid ${compact ? 'compact' : ''}`}>{items.map((item) => <SihBadge key={item.key} item={item} />)}</section>
}

export function EventList({ events, empty }: { events: { timestamp: number; message: string; category: string }[]; empty: string }) {
  return <div className="event-list">{events.length === 0 ? <p>{empty}</p> : [...events].reverse().map((event, index) => <article key={`${event.timestamp}-${index}`}><time>{event.timestamp.toFixed(2)}s</time><span className={event.category}>{event.category}</span><p>{event.message}</p></article>)}</div>
}
