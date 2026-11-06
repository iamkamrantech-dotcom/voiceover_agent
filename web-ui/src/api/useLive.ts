import { useCallback, useEffect, useRef, useState } from 'react'
import type { Job, LogLine, QueueSnapshot, SSEMessage } from './types'

/** How many log lines the console keeps. The whole log is on the server. */
const LOG_LIMIT = 600

/**
 * Live job updates over Server-Sent Events.
 *
 * The server publishes on every status.json write, so the stream cannot
 * report something that never reached disk. EventSource reconnects by
 * itself, and each stream opens with a snapshot, so a dropped connection
 * resynchronises instead of leaving stale numbers on screen.
 *
 * Frames are tagged, and only the queue counts are common to all of them -
 * a log line carries no job, so reading its fields blindly would wipe the
 * top bar every time the console scrolled.
 */
export function useLive(jobId?: string) {
  const [queue, setQueue] = useState<QueueSnapshot>({
    running: null,
    queued: 0,
    queue: [],
  })
  const [job, setJob] = useState<Job | null>(null)
  const [log, setLog] = useState<LogLine[]>([])
  const [connected, setConnected] = useState(false)
  const jobRef = useRef(jobId)
  jobRef.current = jobId

  const clearLog = useCallback(() => setLog([]), [])

  useEffect(() => {
    const url = jobId ? `/api/jobs/${jobId}/events` : '/api/events'
    const source = new EventSource(url)

    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    source.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as SSEMessage

        if (message.type === 'log') {
          if (message.line && message.job_id === jobRef.current) {
            const line = message.line
            setLog((previous) => [...previous, line].slice(-LOG_LIMIT))
          }
          return
        }

        setQueue({
          running: message.running ?? null,
          queued: message.queued ?? 0,
          queue: message.queue ?? [],
        })
        // A stream opened mid-run catches up on what it missed.
        if (message.log?.length) setLog(message.log.slice(-LOG_LIMIT))
        if (message.job) {
          if (!jobRef.current || message.job.id === jobRef.current) {
            setJob(message.job)
          }
        }
      } catch {
        /* a malformed frame should never take the dashboard down */
      }
    }

    return () => source.close()
  }, [jobId])

  return { queue, job, log, connected, clearLog }
}
