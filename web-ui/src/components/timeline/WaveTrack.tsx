import { useEffect, useRef } from 'react'
import { decodePeaks, peakBetween } from './peaks'
import type { Waveform } from '../../api/types'

/**
 * The narration, drawn.
 *
 * On a canvas rather than in the DOM, and only ever as wide as the
 * viewport: at ten minutes and a readable zoom the full timeline is tens
 * of thousands of pixels across, which no canvas should be. It redraws on
 * scroll, zoom and theme change only - never on playback, which is what
 * keeps the playhead at sixty frames a second.
 */
export function WaveTrack({
  wave,
  width,
  height,
  startTime,
  secondsPerPixel,
  theme,
}: {
  wave: Waveform | null
  width: number
  height: number
  startTime: number
  secondsPerPixel: number
  theme: string
}) {
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const element = canvas.current
    if (!element || width <= 0) return
    const ratio = window.devicePixelRatio || 1
    element.width = Math.floor(width * ratio)
    element.height = Math.floor(height * ratio)

    const ctx = element.getContext('2d')
    if (!ctx) return
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
    ctx.clearRect(0, 0, width, height)

    const peaks = decodePeaks(wave)
    if (!peaks.length || !wave) return

    // Read the colour from the token, so both themes work without the
    // canvas knowing which one is on.
    const styles = getComputedStyle(document.documentElement)
    ctx.fillStyle = styles.getPropertyValue('--accent').trim() || '#3e7bfa'

    const middle = height / 2
    for (let x = 0; x < width; x += 1) {
      const from = startTime + x * secondsPerPixel
      const peak = peakBetween(peaks, wave.per_second, from,
                               from + secondsPerPixel)
      if (peak <= 0) continue
      // Mirrored around the centre: speech is near enough symmetric, and
      // storing one peak per bucket halves what has to be sent.
      const half = Math.max(0.5, peak * (height / 2 - 1))
      ctx.fillRect(x, middle - half, 1, half * 2)
    }
  }, [wave, width, height, startTime, secondsPerPixel, theme])

  return (
    <canvas
      ref={canvas}
      style={{ width, height }}
      aria-hidden="true"
      className="block"
    />
  )
}
