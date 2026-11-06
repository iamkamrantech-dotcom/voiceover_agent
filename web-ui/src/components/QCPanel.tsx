import type { QCReport } from '../api/types'
import { clock, secs1 } from '../lib/format'

/**
 * What the finished file measured.
 *
 * Every number here was taken from the rendered mp4, not from the plan, so
 * it says what a viewer will actually get. Each row carries its own verdict
 * rather than one overall pass mark, because "the loudness is right but the
 * sync drifted" is a different problem from its opposite.
 */

type Tone = 'pass' | 'warn' | 'none'

function Row({
  label,
  value,
  tone,
  note,
}: {
  label: string
  value: string
  tone: Tone
  note?: string
}) {
  const colour =
    tone === 'pass' ? 'var(--success)'
      : tone === 'warn' ? 'var(--warning)'
        : 'var(--faint)'
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-border py-2.5 last:border-b-0">
      <div className="min-w-0">
        <span className="text-base">{label}</span>
        {note && <span className="block text-xs text-faint">{note}</span>}
      </div>
      <span className="tnum shrink-0 text-base" style={{ color: colour }}>
        {value}
      </span>
    </div>
  )
}

export function QCPanel({
  report,
  onSeek,
}: {
  report: QCReport
  onSeek: (at: number) => void
}) {
  const loudness = report.loudness_i
  const peak = report.loudness_tp

  return (
    <div className="p-4">
      <Row
        label="Loudness"
        value={loudness === null ? 'not measured' : `${loudness.toFixed(2)} LUFS`}
        tone={loudness === null ? 'none' : report.loudness_ok ? 'pass' : 'warn'}
        note="YouTube plays back at -14 LUFS"
      />
      <Row
        label="True peak"
        value={peak === null ? 'not measured' : `${peak.toFixed(2)} dBTP`}
        tone={report.clipping ? 'warn' : peak === null ? 'none' : 'pass'}
        note={report.clipping ? 'Clipping detected' : 'Headroom before clipping'}
      />
      <Row
        label="Sync drift"
        value={
          !report.drift_applicable || report.drift_median === null
            ? 'not applicable'
            : `${secs1(report.drift_median)} median`
        }
        tone={
          !report.drift_applicable ? 'none' : report.drift_ok ? 'pass' : 'warn'
        }
        note={
          report.drift_applicable && report.drift_max !== null
            ? `Worst word ${secs1(report.drift_max)}. Judged on the median, `
              + 'because transcription timestamps jitter by a tenth either way.'
            : 'The mock engine makes tones, which cannot be transcribed.'
        }
      />
      <Row
        label="Words matched"
        value={
          report.total_words
            ? `${report.matched_words} of ${report.total_words}`
            : 'not applicable'
        }
        tone={
          !report.total_words ? 'none'
            : report.matched_words >= report.total_words - 1 ? 'pass' : 'warn'
        }
        note="Heard in the finished file against the script"
      />
      <Row
        label="Length"
        value={clock(report.duration_actual)}
        tone={report.duration_ok ? 'pass' : 'warn'}
        note={`Planned ${clock(report.duration_expected)}`}
      />

      {report.problems.length > 0 && (
        <div className="mt-4 rounded-md border border-border bg-warning-wash p-3">
          <h3 className="text-base font-medium">Problems found</h3>
          <ul className="mt-1 list-disc pl-4 text-base text-muted">
            {report.problems.map((problem, i) => (
              <li key={i}>{problem}</li>
            ))}
          </ul>
        </div>
      )}

      {report.listen_to.length > 0 && (
        <div className="mt-4">
          <h3 className="text-base font-medium">Worth checking yourself</h3>
          <p className="mb-1.5 text-xs text-muted">
            Not faults - places where a person should use their own ears.
          </p>
          <ul className="divide-y divide-border border-t border-border">
            {report.listen_to.map((item, i) => (
              <li key={i}>
                <button
                  type="button"
                  className="flex w-full items-baseline justify-between gap-3 py-2 text-left hover:text-accent"
                  onClick={() => item.at !== null && onSeek(item.at)}
                  disabled={item.at === null}
                  data-testid={`listen-${i}`}
                >
                  <span className="text-base">
                    Block {item.block + 1}
                    <span className="ml-2 text-muted">{item.why}</span>
                  </span>
                  <span className="tnum shrink-0 text-xs text-faint">
                    {item.at === null ? '' : clock(item.at)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
