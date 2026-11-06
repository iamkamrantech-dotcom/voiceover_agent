import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { useLive } from '../api/useLive'
import type { Job, Timeline } from '../api/types'
import { useToast } from '../components/Toast'
import {
  Button,
  ConfirmDialog,
  ErrorState,
  Panel,
  Skeleton,
  StatusBadge,
} from '../components/ui'
import { clock, seconds, when } from '../lib/format'

function progressPercent(job: Job): number {
  if (job.state === 'done') return 100
  if (job.total_takes > 0 && job.done_takes > 0) {
    return Math.min(60, (job.done_takes / job.total_takes) * 60)
  }
  const byStage: Record<string, number> = {
    scenes: 5,
    script: 8,
    voice: 15,
    verify: 65,
    align: 70,
    subtitles: 75,
    audio: 80,
    render: 88,
    qc: 96,
  }
  return byStage[job.stage] ?? 2
}

function TimelineStrip({
  timeline,
  onSeek,
}: {
  timeline: Timeline
  onSeek: (at: number) => void
}) {
  const total = timeline.output_duration || 1
  return (
    <div className="space-y-2 p-4">
      <div className="relative h-10 w-full overflow-hidden rounded-md bg-raised">
        {timeline.blocks.map((block, i) => (
          <button
            key={block.block_index}
            onClick={() => onSeek(block.out_start)}
            title={`Block ${i + 1}: ${block.out_start.toFixed(1)}–${block.out_end.toFixed(1)}s`}
            className="absolute top-0 h-full border-r border-surface hover:brightness-110"
            style={{
              left: `${(block.out_start / total) * 100}%`,
              width: `${((block.out_end - block.out_start) / total) * 100}%`,
              background: 'var(--accent-wash)',
            }}
          >
            <span className="absolute left-1 top-0.5 text-2xs text-muted">
              {i + 1}
            </span>
          </button>
        ))}
        {timeline.blocks.map((block) =>
          block.extension > 0.01 ? (
            <span
              key={`ext-${block.block_index}`}
              aria-hidden="true"
              className="absolute top-0 h-full"
              style={{
                left: `${((block.out_end - block.extension) / total) * 100}%`,
                width: `${(block.extension / total) * 100}%`,
                background:
                  'repeating-linear-gradient(45deg, var(--warning-wash) 0 6px, transparent 6px 12px)',
              }}
            />
          ) : null,
        )}
        {timeline.blocks.map((block) =>
          !block.silent && block.voice_end > block.voice_start ? (
            <span
              key={`voice-${block.block_index}`}
              aria-hidden="true"
              className="absolute bottom-1.5 h-2 rounded-sm"
              style={{
                left: `${(block.voice_start / total) * 100}%`,
                width: `${((block.voice_end - block.voice_start) / total) * 100}%`,
                background: 'var(--accent)',
              }}
            />
          ) : null,
        )}
      </div>
      <p className="text-xs text-faint">
        Each bar is one image, the solid line under it is its narration.
        Hatched areas were extended to fit.{' '}
        <span className="tnum">
          {timeline.source_duration.toFixed(1)}s →{' '}
          {timeline.output_duration.toFixed(1)}s
        </span>
      </p>
    </div>
  )
}

export default function JobDetail() {
  const { id = '' } = useParams()
  const { job: liveJob } = useLive(id)
  const [job, setJob] = useState<Job | null>(null)
  const [timeline, setTimeline] = useState<Timeline | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmCancel, setConfirmCancel] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)
  const toast = useToast()

  useEffect(() => {
    api
      .job(id)
      .then(setJob)
      .catch((e: ApiError) => setError(e.message))
  }, [id])

  useEffect(() => {
    if (liveJob && liveJob.id === id) setJob(liveJob)
  }, [liveJob, id])

  useEffect(() => {
    if (job?.state === 'done' && !timeline) {
      api.timeline(id).then(setTimeline).catch(() => {})
    }
  }, [job?.state, id, timeline])

  if (error) return <ErrorState message={error} />
  if (!job) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }

  const active = job.state === 'running' || job.state === 'queued'
  const resumable =
    job.state === 'interrupted' ||
    job.state === 'failed' ||
    job.state === 'cancelled'

  function seek(at: number) {
    const video = videoRef.current
    if (!video) return
    video.currentTime = at
    void video.play()
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <Link to="/jobs" className="text-sm text-muted hover:text-text">
            Jobs
          </Link>
          <h1 className="truncate text-lg font-semibold">{job.name}</h1>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge state={job.state} />
          {active && (
            <Button variant="danger" onClick={() => setConfirmCancel(true)}>
              Cancel
            </Button>
          )}
          {resumable && (
            <Button
              variant="primary"
              onClick={() =>
                api
                  .resumeJob(job.id)
                  .then(() => toast.show('Job queued again', 'success'))
                  .catch((e: ApiError) => toast.show(e.message, 'error'))
              }
            >
              Resume
            </Button>
          )}
        </div>
      </div>

      {active && (
        <Panel title="Progress">
          <div className="space-y-3 p-4">
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised">
              <div
                className="h-full rounded-full transition-[width] duration-500"
                style={{
                  width: `${progressPercent(job)}%`,
                  background: 'var(--accent)',
                }}
              />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 text-base">
              <span data-testid="job-stage">
                {job.state === 'queued'
                  ? `Waiting in the queue, position ${job.position}`
                  : job.detail || job.stage}
              </span>
              <span className="tnum text-sm text-muted">
                {job.elapsed > 0 && clock(job.elapsed)}
                {job.remaining > 0 && ` · about ${seconds(job.remaining)} left`}
              </span>
            </div>
            {job.blocks > 0 && (
              <p className="tnum text-sm text-muted">
                Block {job.block}/{job.blocks}
                {job.takes > 0 ? `, take ${job.take}/${job.takes}` : ''}
              </p>
            )}
          </div>
        </Panel>
      )}

      {job.state === 'failed' && job.error && (
        <Panel title="Error">
          <p className="p-4 font-mono text-sm text-danger">{job.error}</p>
        </Panel>
      )}

      {job.state === 'done' && (
        <>
          <Panel>
            <div className="space-y-4 p-4">
              <div
                className={`rounded-md px-3.5 py-2.5 text-base ${
                  job.result.qc_passed
                    ? 'bg-success-wash text-success'
                    : 'bg-warning-wash text-warning'
                }`}
              >
                {job.result.qc_passed
                  ? 'Quality checks passed.'
                  : 'Quality checks found something. Open the report for detail.'}
              </div>

              <video
                ref={videoRef}
                controls
                playsInline
                preload="metadata"
                className="w-full rounded-md bg-black"
                src={api.fileUrl(job.id, 'video')}
              />

              <div className="flex flex-wrap gap-2">
                {(
                  [
                    ['video', 'Download video'],
                    ['subtitles', 'Subtitles (.srt)'],
                    ['report', 'QC report'],
                  ] as const
                ).map(([kind, label]) => (
                  <a
                    key={kind}
                    href={api.fileUrl(job.id, kind)}
                    download
                    className="inline-flex h-9 items-center rounded-md border border-border px-3.5 text-base hover:bg-raised"
                  >
                    {label}
                  </a>
                ))}
              </div>
            </div>
          </Panel>

          {(job.result.listen_to?.length ?? 0) > 0 && (
            <Panel title="Listen to these yourself">
              <ul className="divide-y divide-border">
                {job.result.listen_to?.map((item, i) => (
                  <li key={i}>
                    <button
                      onClick={() => item.at !== null && seek(item.at)}
                      disabled={item.at === null}
                      className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-base hover:bg-raised disabled:cursor-default"
                    >
                      <span>
                        <span className="font-medium">
                          Block {item.block + 1}
                        </span>
                        <span className="ml-2 text-muted">{item.why}</span>
                      </span>
                      {item.at !== null && (
                        <span className="tnum text-sm text-accent">
                          {clock(item.at)}
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          <Panel title="Timeline">
            {timeline ? (
              <TimelineStrip timeline={timeline} onSeek={seek} />
            ) : (
              <div className="p-4">
                <Skeleton className="h-10 w-full" />
              </div>
            )}
          </Panel>
        </>
      )}

      <Panel title="Details">
        <dl className="divide-y divide-border text-base">
          {[
            ['Created', when(job.created)],
            ['Started', job.started ? when(job.started) : '—'],
            ['Finished', job.finished ? when(job.finished) : '—'],
            ['Processing time', job.elapsed > 0 ? seconds(job.elapsed) : '—'],
            ['Engine', String(job.params.engine ?? '—')],
            ['Quality', job.params.draft ? 'Draft' : 'Final'],
          ].map(([label, value]) => (
            <div
              key={label}
              className="flex items-center justify-between gap-3 px-4 py-2"
            >
              <dt className="text-muted">{label}</dt>
              <dd className="tnum truncate text-right">{value}</dd>
            </div>
          ))}
        </dl>
      </Panel>

      <ConfirmDialog
        open={confirmCancel}
        title="Cancel this job?"
        body="Processing stops and the model is released. Narration already generated stays cached, so resuming later is quick."
        confirmLabel="Cancel job"
        destructive
        onConfirm={() => {
          setConfirmCancel(false)
          api
            .cancelJob(job.id)
            .then(() => toast.show('Job cancelled'))
            .catch((e: ApiError) => toast.show(e.message, 'error'))
        }}
        onCancel={() => setConfirmCancel(false)}
      />
    </div>
  )
}
