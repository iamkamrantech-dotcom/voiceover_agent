import type { FitRow } from '../api/types'

/**
 * How well a block's narration fits its scene.
 *
 * This is the one place in the dashboard that earns a strong visual
 * treatment, because it is the decision the whole storyboard exists to
 * support: is this script going to fit, and if not, by how much?
 *
 * The marker is the point where the speech no longer fits the picture.
 * Fill up to it is comfortable; the segment past it is the part that has
 * to be absorbed, and its colour says how: a small speed-up, a short
 * video extension, or cutting words.
 *
 * The label and the numbers must never disagree. An earlier version said
 * "Fits" beside "3.6s speech / 3.4s available" and drew a red overhang,
 * which read as broken even though the speed-up genuinely absorbed it.
 */

const GATE = 72 // where "no longer fits" sits on the track

type Tone = { color: string; label: string }

function toneFor(row: FitRow): Tone {
  if (row.state === 'empty') {
    return { color: 'var(--faint)', label: 'Silent' }
  }
  if (row.state === 'over') {
    return {
      color: 'var(--danger)',
      label: `Cut about ${row.cut_words} words`,
    }
  }
  if (row.state === 'tight') {
    return {
      color: 'var(--warning)',
      label: `Needs ${row.extension.toFixed(1)}s more picture`,
    }
  }
  // Fits, but only because the narration will be sped up slightly.
  if (row.estimated > row.available) {
    return { color: 'var(--accent)', label: 'Fits with a slight speed-up' }
  }
  return { color: 'var(--success)', label: 'Fits' }
}

export function FitBar({ row }: { row: FitRow }) {
  const tone = toneFor(row)
  const ratio = row.available > 0 ? row.estimated / row.available : 0
  const within = Math.max(0, Math.min(ratio, 1)) * GATE
  const beyond = Math.max(0, Math.min(ratio - 1, 0.7)) * GATE

  return (
    <div className="space-y-1.5">
      <div className="relative" aria-hidden="true">
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised">
          <div className="flex h-full">
            <div
              className="h-full transition-[width] duration-200"
              style={{
                width: `${within}%`,
                background:
                  row.state === 'empty' ? 'var(--faint)' : 'var(--success)',
              }}
            />
            {beyond > 0 && (
              <div
                className="h-full transition-[width] duration-200"
                style={{ width: `${beyond}%`, background: tone.color }}
              />
            )}
          </div>
        </div>
        <span
          className="absolute top-[-3px] h-[12px] w-px"
          style={{ left: `${GATE}%`, background: 'var(--border-strong)' }}
        />
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs">
        <span className="font-medium" style={{ color: tone.color }}>
          {tone.label}
        </span>
        {row.state !== 'empty' && (
          <span className="tnum text-faint">
            {row.estimated.toFixed(1)}s of speech in {row.available.toFixed(1)}s
            of picture
          </span>
        )}
      </div>
    </div>
  )
}
