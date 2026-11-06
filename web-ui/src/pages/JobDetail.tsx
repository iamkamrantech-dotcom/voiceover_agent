import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { useLive } from '../api/useLive'
import type {
  BlockDetail, Job, JobVersion, QCReport, StageDef, Timeline as TimelineData,
  Waveform,
} from '../api/types'
import { clock, seconds, when } from '../lib/format'
import {
  Button, ConfirmDialog, Drawer, EmptyState, ErrorState, Panel, Skeleton,
  SkeletonRows, StatusBadge,
} from '../components/ui'
import { useToast } from '../components/Toast'
import { PipelineStepper } from '../components/PipelineStepper'
import { BlockGrid, blockTiles, liveTiles } from '../components/BlockGrid'
import { LogConsole } from '../components/LogConsole'
import { QCPanel } from '../components/QCPanel'
import { BlockReview } from '../components/BlockReview'
import { PlayerKeys, VideoPlayer } from '../components/VideoPlayer'
import { Timeline } from '../components/timeline/Timeline'
import { askToNotify, canAsk, notifyFinished } from '../lib/notify'

/**
 * One page for a job, running or finished.
 *
 * While it runs: which stage, which block, what the log says, and how long
 * is left - measured from this run's own pace.
 *
 * When it has finished: the video, a timeline you can scrub, what the
 * quality checks measured, and - the point of this phase - a way to fix
 * one block without running the whole thing again.
 */

const ACTIVE = new Set(['running', 'queued'])
const RESUMABLE = new Set(['interrupted', 'failed', 'cancelled'])

/** What the job is doing, in the pipeline's own words. */
function stageLabel(job: Job, stages: StageDef[]): string {
  const known = stages.find((s) => s.key === job.stage)
  return known?.label ?? job.detail ?? job.stage
}

export default function JobDetail() {
  const { id = '' } = useParams()
  const toast = useToast()
  const live = useLive(id)

  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stages, setStages] = useState<StageDef[]>([])
  const [versions, setVersions] = useState<JobVersion[]>([])
  const [version, setVersion] = useState<number | null>(null)
  const [timeline, setTimeline] = useState<TimelineData | null>(null)
  const [wave, setWave] = useState<Waveform | null>(null)
  const [qc, setQc] = useState<QCReport | null>(null)
  const [blocks, setBlocks] = useState<BlockDetail[]>([])
  const [takesRecorded, setTakesRecorded] = useState(true)
  const [open, setOpen] = useState<number | null>(null)
  const [confirmCancel, setConfirmCancel] = useState(false)
  const [removing, setRemoving] = useState<number | null>(null)
  const [askPermission, setAskPermission] = useState(canAsk)
  const videoRef = useRef<HTMLVideoElement>(null)
  const wasRunning = useRef(false)

  const state = job?.state
  const active = state ? ACTIVE.has(state) : false
  const finished = state === 'done'

  // ---- loading ---------------------------------------------------------
  useEffect(() => {
    api.job(id).then(setJob).catch((e: ApiError) => setError(e.message))
    api.config().then((c) => setStages(c.stages)).catch(() => setStages([]))
  }, [id])

  useEffect(() => {
    if (live.job?.id === id) setJob(live.job)
  }, [live.job, id])

  const loadResults = useCallback(() => {
    api
      .versions(id)
      .then((r) => {
        setVersions(r.versions)
        setVersion((current) =>
          current && r.versions.some((v) => v.version === current)
            ? current
            : (r.versions[r.versions.length - 1]?.version ?? null))
      })
      .catch(() => setVersions([]))
    api.blocks(id)
      .then((r) => { setBlocks(r.blocks); setTakesRecorded(r.takes_recorded) })
      .catch(() => setBlocks([]))
  }, [id])

  useEffect(() => {
    if (finished) loadResults()
  }, [finished, loadResults])

  // Panels load independently: a slow one must not hold up the rest.
  useEffect(() => {
    if (!finished || version === null) return
    setTimeline(null)
    setWave(null)
    setQc(null)
    api.timeline(id, version).then(setTimeline).catch(() => setTimeline(null))
    api.waveform(id, version).then(setWave).catch(() => setWave(null))
    api.qc(id, version).then(setQc).catch(() => setQc(null))
  }, [finished, id, version])

  // ---- a notification when it ends ------------------------------------
  useEffect(() => {
    if (active) wasRunning.current = true
    if (!wasRunning.current || active || !job) return
    wasRunning.current = false
    notifyFinished(job)
  }, [active, job, state])

  const seek = useCallback((at: number) => {
    const video = videoRef.current
    if (!video) return
    video.currentTime = at
    void video.play()
  }, [])

  const tiles = useMemo(() => {
    if (!job) return []
    if (finished && blocks.length) return blockTiles(blocks, 70)
    return liveTiles(job)
  }, [job, finished, blocks])

  const openBlock = useMemo(
    () => blocks.find((b) => b.block === open) ?? null,
    [blocks, open],
  )

  if (error) return <ErrorState message={error} />
  if (!job) return <SkeletonRows rows={6} />

  const current = versions.find((v) => v.version === version) ?? null
  const isLatest = !current || current.version === versions.length
  const videoUrl = api.fileUrl(job.id, 'video', version ?? undefined)

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <nav className="text-xs text-muted" aria-label="Breadcrumb">
            <Link to="/jobs" className="hover:text-accent">Jobs</Link>
            <span className="px-1.5 text-faint">/</span>
            <span>{job.name}</span>
          </nav>
          <h1 className="truncate text-xl font-semibold">{job.name}</h1>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge state={job.state} />
          {active && (
            <Button variant="danger" onClick={() => setConfirmCancel(true)}>
              Cancel
            </Button>
          )}
          {RESUMABLE.has(job.state) && (
            <Button
              variant="primary"
              onClick={() =>
                api.resumeJob(job.id)
                  .then(() => toast.show('Queued again.', 'success'))
                  .catch((e: ApiError) => toast.show(e.message, 'error'))
              }
            >
              Resume
            </Button>
          )}
        </div>
      </header>

      {/* ---- running ---------------------------------------------- */}
      {active && (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
          <div className="space-y-5">
            <Panel
              title={job.kind === 'revise' ? 'Rebuilding' : 'Progress'}
              action={
                <div className="flex items-center gap-3">
                  {askPermission && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        void askToNotify().then((granted) => {
                          setAskPermission(false)
                          if (granted) {
                            toast.show('You will be told when this finishes.',
                                       'success')
                          }
                        })
                      }}
                    >
                      Tell me when it is done
                    </Button>
                  )}
                  <span className="tnum text-xs text-muted">
                    {clock(job.elapsed)}
                    {job.remaining > 0 &&
                      ` · about ${seconds(job.remaining)} left`}
                  </span>
                </div>
              }
            >
              <div className="space-y-2 p-4">
                {/* The stage, then the counts - not the server's detail
                    string, which says the same counts again in its own
                    shorthand. */}
                <p className="text-base" data-testid="job-stage">
                  {job.state === 'queued'
                    ? `Waiting in the queue, position ${job.position}`
                    : stageLabel(job, stages)}
                </p>
                {job.blocks > 0 && job.state === 'running' && (
                  <p className="tnum text-xs text-muted">
                    Block {job.block} of {job.blocks}
                    {job.takes > 0 && `, take ${job.take} of ${job.takes}`}
                    {job.cached_takes > 0 &&
                      ` · ${job.cached_takes} found in cache`}
                  </p>
                )}
              </div>
              {tiles.length > 0 && <BlockGrid tiles={tiles} />}
            </Panel>

            <Panel title="Log">
              <LogConsole lines={live.log} />
            </Panel>
          </div>

          <Panel title="Stages">
            <div className="p-4">
              {stages.length ? (
                <PipelineStepper job={job} stages={stages} />
              ) : (
                <Skeleton className="h-40 w-full" />
              )}
            </div>
          </Panel>
        </div>
      )}

      {job.state === 'failed' && job.error && (
        <Panel title="Error">
          <p className="p-4 font-mono text-base text-danger">{job.error}</p>
        </Panel>
      )}

      {/* ---- finished --------------------------------------------- */}
      {finished && (
        <>
          <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
            <div className="space-y-5">
              <Panel>
                <div className="space-y-3 p-4">
                  <div
                    className="rounded-md px-3 py-2 text-base"
                    style={{
                      background: current?.qc_passed === false
                        ? 'var(--warning-wash)' : 'var(--success-wash)',
                      color: current?.qc_passed === false
                        ? 'var(--warning)' : 'var(--success)',
                    }}
                  >
                    {current?.qc_passed === false
                      ? 'Quality checks found something worth a look.'
                      : 'Quality checks passed.'}
                  </div>
                  <VideoPlayer
                    src={videoUrl}
                    fps={timeline?.fps ?? 25}
                    videoRef={videoRef}
                  />
                  <div className="flex flex-wrap items-center gap-2">
                    {([
                      ['video', 'Download video'],
                      ['subtitles', 'Subtitles (.srt)'],
                      ['timeline', 'Timeline (.json)'],
                      ['report', 'Quality report'],
                    ] as const).map(([kind, label]) => (
                      <a
                        key={kind}
                        href={api.fileUrl(job.id, kind, version ?? undefined)}
                        download
                        className="inline-flex h-9 items-center rounded-md border border-border px-3.5 text-base hover:bg-raised"
                      >
                        {label}
                      </a>
                    ))}
                    <span className="flex-1" />
                    <PlayerKeys />
                  </div>
                </div>
              </Panel>

              <Panel title="Timeline">
                {timeline ? (
                  <Timeline
                    jobId={job.id}
                    data={timeline}
                    wave={wave}
                    blocks={blocks}
                    video={videoRef}
                    selected={open}
                    onSelect={setOpen}
                  />
                ) : (
                  <div className="p-4"><Skeleton className="h-28 w-full" /></div>
                )}
              </Panel>

              <Panel
                title="Blocks"
                action={
                  <span className="text-xs text-muted">
                    Click one to hear its takes
                  </span>
                }
              >
                {blocks.length ? (
                  <BlockGrid tiles={tiles} onOpen={setOpen} selected={open} />
                ) : (
                  <EmptyState
                    title="No block record"
                    body="This job finished before per-block details were kept. Run it again to be able to review and fix single blocks."
                  />
                )}
              </Panel>
            </div>

            <div className="space-y-5">
              <Panel title="Quality checks">
                {qc ? (
                  <QCPanel report={qc} onSeek={seek} />
                ) : (
                  <SkeletonRows rows={5} />
                )}
              </Panel>

              <Panel title="Versions">
                <ul className="divide-y divide-border">
                  {versions.map((entry) => (
                    <li
                      key={entry.version}
                      className="flex items-start gap-3 px-4 py-3"
                    >
                      <Button
                        size="sm"
                        variant={entry.version === version ? 'primary' : 'ghost'}
                        onClick={() => setVersion(entry.version)}
                        data-testid={`version-${entry.version}`}
                      >
                        v{entry.version}
                      </Button>
                      <div className="min-w-0 flex-1">
                        <p className="text-base">{entry.note}</p>
                        <p className="tnum text-xs text-faint">
                          {when(entry.created)} · {clock(entry.duration)}
                          {entry.qc_passed === false && ' · checks warned'}
                        </p>
                      </div>
                      {versions.length > 1 && entry.version > 1 && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setRemoving(entry.version)}
                          aria-label={`Delete version ${entry.version}`}
                        >
                          Delete
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
                {!isLatest && (
                  <p className="border-t border-border px-4 py-2 text-xs text-muted">
                    Showing an earlier version. Changes always build on the
                    newest one.
                  </p>
                )}
              </Panel>

              <Panel title="Details">
                <dl className="divide-y divide-border text-base">
                  {[
                    ['Created', when(job.created)],
                    ['Finished', job.finished ? when(job.finished) : '—'],
                    ['Processing time',
                     job.elapsed > 0 ? seconds(job.elapsed) : '—'],
                    ['Engine', String(job.params.engine ?? '—')],
                    ['Quality', job.params.draft ? 'Draft' : 'Final'],
                  ].map(([label, value]) => (
                    <div key={label}
                         className="flex items-center justify-between gap-3 px-4 py-2">
                      <dt className="text-muted">{label}</dt>
                      <dd className="tnum truncate text-right">{value}</dd>
                    </div>
                  ))}
                </dl>
              </Panel>

              <Panel title="Log">
                <JobLog id={job.id} />
              </Panel>
            </div>
          </div>

          <Drawer
            open={openBlock !== null}
            title={openBlock ? `Block ${openBlock.block + 1}` : ''}
            onClose={() => setOpen(null)}
          >
            {openBlock && (
              <BlockReview
                jobId={job.id}
                block={openBlock}
                takesRecorded={takesRecorded}
                canRevise={isLatest && !active}
                onRevised={() => { setOpen(null); loadResults() }}
              />
            )}
          </Drawer>
        </>
      )}

      <ConfirmDialog
        open={confirmCancel}
        title="Cancel this job?"
        body="Processing stops and the model is released. Narration already generated stays cached, so resuming later is quick."
        confirmLabel="Cancel job"
        destructive
        onConfirm={() => {
          setConfirmCancel(false)
          api.cancelJob(job.id)
            .then(() => toast.show('Job cancelled.'))
            .catch((e: ApiError) => toast.show(e.message, 'error'))
        }}
        onCancel={() => setConfirmCancel(false)}
      />

      <ConfirmDialog
        open={removing !== null}
        title={`Delete version ${removing}?`}
        body="Its video, subtitles and report are removed. The other versions are untouched."
        confirmLabel="Delete version"
        destructive
        onConfirm={() => {
          const target = removing
          setRemoving(null)
          if (target === null) return
          api.deleteVersion(job.id, target)
            .then((r) => { toast.show(r.message, 'success'); loadResults() })
            .catch((e: ApiError) => toast.show(e.message, 'error'))
        }}
        onCancel={() => setRemoving(null)}
      />
    </div>
  )
}

/** The finished job's log, read back from the file rather than the stream. */
function JobLog({ id }: { id: string }) {
  const [lines, setLines] = useState<null | Awaited<
    ReturnType<typeof api.jobLog>
  >['lines']>(null)

  useEffect(() => {
    api.jobLog(id).then((r) => setLines(r.lines)).catch(() => setLines([]))
  }, [id])

  if (lines === null) return <SkeletonRows rows={2} />
  if (!lines.length) {
    return (
      <p className="p-4 text-base text-muted">
        No log was kept for this job.
      </p>
    )
  }
  return <LogConsole lines={lines} />
}
