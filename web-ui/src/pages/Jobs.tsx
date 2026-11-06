import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { useLive } from '../api/useLive'
import type { Job, JobState } from '../api/types'
import { useToast } from '../components/Toast'
import {
  Button,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Panel,
  SkeletonRows,
  StatusBadge,
  inputClass,
} from '../components/ui'
import { seconds, when } from '../lib/format'

const FILTERS: { value: JobState | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'done', label: 'Done' },
  { value: 'running', label: 'Running' },
  { value: 'queued', label: 'Queued' },
  { value: 'failed', label: 'Failed' },
  { value: 'interrupted', label: 'Interrupted' },
  { value: 'cancelled', label: 'Cancelled' },
]

export default function Jobs() {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<JobState | 'all'>('all')
  const [pendingDelete, setPendingDelete] = useState<Job | null>(null)
  const toast = useToast()
  const navigate = useNavigate()
  const { queue } = useLive()

  const load = useCallback(() => {
    setError(null)
    api
      .jobs()
      .then((data) => setJobs(data.jobs))
      .catch((e: ApiError) => setError(e.message))
  }, [])

  useEffect(load, [load])
  useEffect(() => {
    // The list changes while jobs move through the queue.
    load()
  }, [queue.running, queue.queued, load])

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return (jobs ?? []).filter((job) => {
      if (filter !== 'all' && job.state !== filter) return false
      if (!needle) return true
      return (
        job.name.toLowerCase().includes(needle) ||
        job.id.toLowerCase().includes(needle)
      )
    })
  }, [jobs, query, filter])

  async function duplicate(job: Job) {
    try {
      const { id } = await api.duplicateJob(job.id)
      toast.show('Queued a copy', 'success')
      navigate(`/jobs/${id}`)
    } catch (e) {
      toast.show((e as ApiError).message, 'error')
    }
  }

  async function confirmDelete() {
    const job = pendingDelete
    setPendingDelete(null)
    if (!job) return
    try {
      await api.deleteJob(job.id)
      toast.show(`Deleted ${job.name}`, 'success')
      load()
    } catch (e) {
      toast.show((e as ApiError).message, 'error')
    }
  }

  if (error) return <ErrorState message={error} onRetry={load} />

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Jobs</h1>
        <Link
          to="/new"
          className="inline-flex h-9 items-center rounded-md bg-accent px-3.5 text-base font-medium text-accent-ink hover:bg-accent-hover"
        >
          New video
        </Link>
      </div>

      <Panel>
        <div className="flex flex-wrap items-center gap-3 border-b border-border p-3">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by name"
            aria-label="Search jobs"
            data-testid="job-search"
            className={`${inputClass} h-9 max-w-xs flex-1`}
          />
          <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by status">
            {FILTERS.map((item) => (
              <button
                key={item.value}
                onClick={() => setFilter(item.value)}
                data-testid={`filter-${item.value}`}
                aria-pressed={filter === item.value}
                className={`h-8 rounded-md px-2.5 text-sm transition-colors ${
                  filter === item.value
                    ? 'bg-accent-wash font-medium text-accent'
                    : 'text-muted hover:bg-raised hover:text-text'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        {jobs === null ? (
          <SkeletonRows rows={5} />
        ) : visible.length === 0 ? (
          <EmptyState
            title={jobs.length === 0 ? 'No jobs yet' : 'Nothing matches'}
            body={
              jobs.length === 0
                ? 'Start a video and it will appear here.'
                : 'Try a different search or status filter.'
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-base">
              <thead>
                <tr className="border-b border-border text-sm text-muted">
                  <th className="px-4 py-2 text-left font-medium">Video</th>
                  <th className="px-2 py-2 text-left font-medium">Status</th>
                  <th className="px-2 py-2 text-right font-medium">Length</th>
                  <th className="px-2 py-2 text-right font-medium">
                    Processing
                  </th>
                  <th className="px-2 py-2 text-left font-medium">QC</th>
                  <th className="px-2 py-2 text-right font-medium">Created</th>
                  <th className="px-4 py-2 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody data-testid="jobs-tbody">
                {visible.map((job) => (
                  <tr
                    key={job.id}
                    className="border-b border-border last:border-b-0 hover:bg-raised"
                  >
                    <td className="max-w-[220px] px-4 py-2.5">
                      <Link
                        to={`/jobs/${job.id}`}
                        className="block truncate font-medium hover:text-accent"
                      >
                        {job.name}
                      </Link>
                    </td>
                    <td className="px-2 py-2.5">
                      <StatusBadge state={job.state} />
                    </td>
                    <td className="tnum px-2 py-2.5 text-right text-muted">
                      {job.state === 'queued' && job.position
                        ? `#${job.position}`
                        : '—'}
                    </td>
                    <td className="tnum px-2 py-2.5 text-right text-muted">
                      {job.elapsed > 0 ? seconds(job.elapsed) : '—'}
                    </td>
                    <td className="px-2 py-2.5">
                      {job.state !== 'done' ? (
                        <span className="text-faint">—</span>
                      ) : job.result.qc_passed ? (
                        <span className="text-success">Pass</span>
                      ) : (
                        <span className="text-warning">Check</span>
                      )}
                    </td>
                    <td className="px-2 py-2.5 text-right text-sm text-muted">
                      {when(job.created)}
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex justify-end gap-1.5">
                        <Link to={`/jobs/${job.id}`}>
                          <Button size="sm">Open</Button>
                        </Link>
                        <Button size="sm" onClick={() => duplicate(job)}>
                          Duplicate
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={() => setPendingDelete(job)}
                          aria-label={`Delete ${job.name}`}
                        >
                          Delete
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete this job?"
        body={`The rendered video, subtitles and QC report for ${pendingDelete?.name ?? ''} will be removed. This cannot be undone.`}
        confirmLabel="Delete"
        destructive
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  )
}
