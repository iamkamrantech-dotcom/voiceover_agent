import type { Waveform } from '../../api/types'

/**
 * The waveform arrives as one byte per bucket, base64 encoded - about
 * 30 KB for ten minutes. Decoding a 48 kHz wav in the browser instead
 * would mean downloading tens of megabytes before the first pixel.
 */
export function decodePeaks(wave: Waveform | null): Uint8Array {
  if (!wave?.peaks) return new Uint8Array(0)
  try {
    const binary = atob(wave.peaks)
    const out = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i)
    return out
  } catch {
    return new Uint8Array(0)
  }
}

/** Loudest peak between two times, for one column of pixels. */
export function peakBetween(
  peaks: Uint8Array,
  perSecond: number,
  from: number,
  to: number,
): number {
  const first = Math.max(0, Math.floor(from * perSecond))
  const last = Math.min(peaks.length - 1, Math.ceil(to * perSecond))
  let top = 0
  for (let i = first; i <= last; i += 1) {
    const value = peaks[i]
    if (value !== undefined && value > top) top = value
  }
  return top / 255
}
