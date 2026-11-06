/** Formatting helpers. Every duration in the UI goes through one of these,
 *  so the same number never appears in two different shapes. */

export function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !isFinite(value)) return '—'
  const total = Math.max(0, Math.round(value))
  const m = Math.floor(total / 60)
  const s = total % 60
  if (m === 0) return `${s}s`
  if (m < 60) return `${m}m ${String(s).padStart(2, '0')}s`
  const h = Math.floor(m / 60)
  return `${h}h ${String(m % 60).padStart(2, '0')}m`
}

/** mm:ss, for anything that sits next to a video player. */
export function clock(value: number | null | undefined): string {
  if (value === null || value === undefined || !isFinite(value)) return '--:--'
  const total = Math.max(0, Math.round(value))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

/** A duration in seconds with one decimal, for scene ranges. */
export function secs1(value: number): string {
  return `${value.toFixed(1)}s`
}

export function timeRange(start: number, end: number): string {
  return `${start.toFixed(1)}–${end.toFixed(1)}s`
}

export function when(iso: string): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  const time = date.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  })
  if (sameDay) return `Today ${time}`
  return `${date.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
  })} ${time}`
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined || !isFinite(value)) return '—'
  return `${value.toFixed(1)}%`
}

export function words(count: number): string {
  return `${count} ${count === 1 ? 'word' : 'words'}`
}
