import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { useLive } from '../api/useLive'
import type { Job, MachineInfo, Stats, VoiceState } from '../api/types'
import {
  Button,
  EmptyState,
  ErrorState,
  Panel,
  Skeleton,
  StatusBadge,
} from '../components/ui'
import { percent, seconds, when } from '../lib/format'

function Figure({
  label,
  value,
  note,
  loading,
}: {
  label: string
  value: string
  note?: string
  loading: boolean
}) {
  return (
    <div className="px-4 py-3.5">
      <p className="text-sm text-muted">{label}</p>
      {loading ? (
        <Skeleton className="mt-1.5 h-7 w-20" />
      ) : (
        <p className="tnum mt-0.5 text-xl font-semibold">{value}</p>
      )}
      {note && !loading && <p className="mt-0.5 text-xs text-faint">{note}</p>}
    </div>
  )
}

function RunningJob({ job }: { job: Job }) {
  const pct =
    job.total_takes > 0 && job.done_takes > 0
      ? Math.min(95, Math.round((job.done_takes / job.total_takes) * 60))
      : job.stage === 'render'
        ? 85
        : job.stage === 'qc'
          ? 95
          : 10

  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Link
          to={`/jobs/${job.id}`}
          className="text-base font-medium hover:text-accent"
        >
          {job.name}
        </Link>
        <span className="tnum text-sm text-muted">
          {job.remaining > 0 ? `about ${seconds(job.remaining)} left` : ''}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised">
        <div
          className="h-full rounded-full transition-[width] duration-500"
          style={{ width: `${pct}%`, background: 'var(--accent)' }}
        />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted">
        <span>{job.detail || job.stage}</span>
        {job.blocks > 0 && (
          <span className="tnum">
            Block {job.block}/{job.blocks}
            {job.takes > 1 ? `, take ${job.take}/${job.takes}` : ''}
          </span>
        )}
      </div>
    </div>
  )
}

export default function Overview() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [machine, setMachine] = useState<MachineInfo | null>(null)
  const [voice, setVoice] = useState<VoiceState | null>(null)
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { queue } = useLive()

  // Each panel loads on its own. Bundling them in one Promise.all meant a
  // single slow call (scanning the model cache) left the whole page in
  // skeletons, and one failure blanked everything.
  const load = useCallback(() => {
    setError(null)
    api
      .stats()
      .then(setStats)
      .catch((e: ApiError) => setError(e.message))
    api
      .jobs()
      .then((j) => setJobs(j.jobs))
      .catch(() => setJobs([]))
    api.voice().then(setVoice).catch(() => {})
    api.machine().then(setMachine).catch(() => {})
  }, [])

  useEffect(load, [load])

  // Keep the recent list honest while a job is running.
  useEffect(() => {
    if (!queue.running) return
    const id = window.setInterval(() => {
      api.jobs().then((j) => setJobs(j.jobs)).catch(() => {})
    }, 4000)
    return () => window.clearInterval(id)
  }, [queue.running])

  if (error) return <ErrorState message={error} onRetry={load} />

  const loading = stats === null
  const running = jobs?.find((j) => j.state === 'running') ?? null
  const queued = jobs?.filter((j) => j.state === 'queued') ?? []
  const recent = jobs?.slice(0, 6) ?? []

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Overview</h1>
        <Link
          to="/new"
          className="inline-flex h-9 items-center rounded-md bg-accent px-3.5 text-base font-medium text-accent-ink hover:bg-accent-hover"
        >
          New video
        </Link>
      </div>

      <Panel>
        <div className="grid grid-cols-2 divide-border sm:grid-cols-4 sm:divide-x">
          <Figure
            label="Videos made"
            value={String(stats?.videos ?? 0)}
            note={
              stats && stats.videos > 0
                ? `${stats.qc_passed} passed QC`
                : undefined
            }
            loading={loading}
          />
          <Figure
            label="Minutes produced"
            value={(stats?.minutes ?? 0).toFixed(1)}
            loading={loading}
          />
          <Figure
            label="Average voice match"
            value={percent(stats?.avg_similarity ?? null)}
            // An em dash on its own reads as a bug. Say why it is empty.
            note={
              stats?.avg_similarity == null
                ? 'no measurements yet'
                : 'blocks over 3.5s only'
            }
            loading={loading}
          />
          <Figure
            label="Average sync drift"
            value={
              stats?.avg_drift != null ? `${stats.avg_drift.toFixed(3)}s` : '—'
            }
            note={
              stats?.avg_drift == null ? 'no measurements yet' : 'median per job'
            }
            loading={loading}
          />
        </div>
      </Panel>

      <div className="grid gap-5 lg:grid-cols-[1.6fr_1fr]">
        <div className="space-y-5">
          <Panel
            title="Now processing"
            action={
              queued.length > 0 ? (
                <span className="tnum text-sm text-muted">
                  {queued.length} queued
                </span>
              ) : null
            }
          >
            {running ? (
              <RunningJob job={running} />
            ) : (
              <EmptyState
                title="Nothing is processing"
                body="Start a new video and it will appear here with live progress."
                action={
                  <Link to="/new">
                    <Button variant="primary">New video</Button>
                  </Link>
                }
              />
            )}
            {queued.length > 0 && (
              <ul className="border-t border-border">
                {queued.map((job, i) => (
                  <li
                    key={job.id}
                    className="flex items-center justify-between gap-3 px-4 py-2.5 text-base"
                  >
                    <span className="truncate">{job.name}</span>
                    <span className="tnum text-sm text-muted">
                      position {job.position || i + 1}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel
            title="Recent jobs"
            action={
              <Link to="/jobs" className="text-sm text-accent hover:underline">
                View all
              </Link>
            }
          >
            {recent.length === 0 ? (
              <EmptyState
                title="No jobs yet"
                body="Finished videos will be listed here with their QC result."
              />
            ) : (
              <table className="w-full text-base">
                <tbody>
                  {recent.map((job) => (
                    <tr
                      key={job.id}
                      className="border-t border-border first:border-t-0"
                    >
                      <td className="px-4 py-2.5">
                        <Link
                          to={`/jobs/${job.id}`}
                          className="font-medium hover:text-accent"
                        >
                          {job.name}
                        </Link>
                      </td>
                      <td className="px-2 py-2.5">
                        <StatusBadge state={job.state} />
                      </td>
                      <td className="tnum px-2 py-2.5 text-right text-sm text-muted">
                        {job.elapsed > 0 ? seconds(job.elapsed) : '—'}
                      </td>
                      <td className="px-4 py-2.5 text-right text-sm text-muted">
                        {when(job.created)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        </div>

        <div className="space-y-5">
          <Panel title="Active voice">
            {voice === null ? (
              <div className="p-4">
                <Skeleton className="h-10 w-full" />
              </div>
            ) : (
              <div className="space-y-3 p-4">
                <div>
                  <p className="text-base font-medium">
                    {voice.active
                      ? `${voice.active.profile} / ${voice.active.candidate}`
                      : 'Default reference'}
                  </p>
                  <p className="mt-0.5 break-all text-xs text-faint">
                    {voice.active
                      ? `Set ${when(voice.active.set_at)}`
                      : voice.fallback.split(/[\\/]/).pop()}
                  </p>
                </div>
                <audio
                  controls
                  preload="none"
                  className="w-full"
                  src={api.voiceAudioUrl(
                    voice.active ? voice.active.reference : voice.fallback,
                  )}
                />
                <Link to="/voice" className="inline-block text-sm text-accent hover:underline">
                  Manage voices
                </Link>
              </div>
            )}
          </Panel>

          <Panel title="This machine">
            {machine === null ? (
              <div className="space-y-2 p-4">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-2/3" />
              </div>
            ) : (
              <dl className="divide-y divide-border text-base">
                {[
                  ['Processing', machine.accelerator],
                  [
                    'CPU threads',
                    `${machine.threads} of ${machine.cpu_count ?? '?'}`,
                  ],
                  [
                    'Free memory',
                    `${machine.ram_free_gb} GB of ${machine.ram_total_gb} GB`,
                  ],
                  ['Free disk', `${machine.disk_free_gb ?? '—'} GB`],
                  ['Engine', machine.engine],
                  ['FFmpeg', machine.ffmpeg ?? 'not found'],
                ].map(([label, value]) => (
                  <div
                    key={label}
                    className="flex items-center justify-between gap-3 px-4 py-2"
                  >
                    <dt className="text-muted">{label}</dt>
                    <dd className="tnum truncate text-right">{value}</dd>
                  </div>
                ))}
                <div className="px-4 py-2">
                  <dt className="text-muted">Models cached</dt>
                  <dd className="mt-1 space-y-0.5">
                    {machine.models.length === 0 ? (
                      <span className="text-sm text-faint">
                        None yet. The first run downloads them.
                      </span>
                    ) : (
                      machine.models.slice(0, 4).map((model) => (
                        <div
                          key={model.name}
                          className="flex justify-between gap-3 text-sm"
                        >
                          <span className="truncate text-muted">
                            {model.name}
                          </span>
                          <span className="tnum text-faint">{model.gb} GB</span>
                        </div>
                      ))
                    )}
                  </dd>
                </div>
              </dl>
            )}
          </Panel>
        </div>
      </div>
    </div>
  )
}
