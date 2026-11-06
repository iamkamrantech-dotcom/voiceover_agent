import type { Job, StageDef, StageRun } from '../api/types'
import { secs1 } from '../lib/format'

/**
 * Where the job has got to, stage by stage.
 *
 * The stages and their order come from the pipeline itself over
 * /api/config, and the durations are measured as each one ends - so this
 * shows what actually happened rather than a guess at a percentage.
 */

type State = 'waiting' | 'running' | 'done' | 'failed'

function stateOf(
  stage: StageDef,
  order: StageDef[],
  runs: StageRun[],
  job: Job,
): State {
  const run = runs.find((r) => r.stage === stage.key)
  if (run) return run.state
  if (job.state === 'done') return 'done'

  // Nothing recorded: anything before the current stage has been passed.
  const here = order.findIndex((s) => s.key === job.stage)
  const mine = order.findIndex((s) => s.key === stage.key)
  if (here >= 0 && mine >= 0 && mine < here) return 'done'
  return 'waiting'
}

const DOT: Record<State, string> = {
  waiting: 'var(--faint)',
  running: 'var(--accent)',
  done: 'var(--success)',
  failed: 'var(--danger)',
}

/** A revision starts at the timeline: it does not look at the video or the
 *  script again, so listing those stages at all would be a lie. */
const REVISE_STAGES = new Set([
  'voice', 'align', 'subtitles', 'audio', 'render', 'qc',
])
const VOICE_STAGES: StageDef[] = [
  { key: 'candidates', label: 'Cutting candidate clips' },
  { key: 'samples', label: 'Cloning the test sentence' },
  { key: 'scoring', label: 'Scoring against the recording' },
]

export function PipelineStepper({
  job,
  stages,
}: {
  job: Job
  stages: StageDef[]
}) {
  const order =
    job.kind === 'voice'
      ? VOICE_STAGES
      : stages.filter(
          (s) =>
            s.key !== 'done' &&
            (job.kind !== 'revise' || REVISE_STAGES.has(s.key)),
        )
  const runs = job.stages ?? []

  return (
    <ol className="flex flex-col gap-0" data-testid="pipeline-stepper">
      {order.map((stage, i) => {
        const state = stateOf(stage, order, runs, job)
        const run = runs.find((r) => r.stage === stage.key)
        const last = i === order.length - 1
        return (
          <li key={stage.key} className="flex gap-3">
            <div className="flex w-3 shrink-0 flex-col items-center pt-[7px]">
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{
                  background: DOT[state],
                  boxShadow:
                    state === 'running'
                      ? '0 0 0 3px var(--accent-wash)'
                      : undefined,
                }}
                aria-hidden="true"
              />
              {!last && (
                <span
                  className="w-px flex-1"
                  style={{
                    background:
                      state === 'done' ? 'var(--success)' : 'var(--border)',
                    minHeight: 14,
                  }}
                  aria-hidden="true"
                />
              )}
            </div>
            <div className="flex min-w-0 flex-1 items-baseline justify-between gap-3 pb-2.5">
              <span
                className={
                  state === 'waiting'
                    ? 'text-base text-faint'
                    : state === 'running'
                      ? 'text-base font-medium text-text'
                      : 'text-base text-muted'
                }
              >
                {stage.label}
                <span className="sr-only">{` - ${state}`}</span>
              </span>
              <span className="tnum shrink-0 text-xs text-faint">
                {run && run.state === 'done' && run.seconds > 0
                  ? secs1(run.seconds)
                  : state === 'running'
                    ? 'running'
                    : state === 'failed'
                      ? 'failed'
                      : ''}
              </span>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
