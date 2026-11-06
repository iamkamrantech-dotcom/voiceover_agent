import { useEffect, useRef, useState } from 'react'
import type { Job, QueueSnapshot, SSEMessage } from './types'

/**
 * Live job updates over Server-Sent Events.
 *
 * The server publishes on every status.json write, so the stream cannot
 * report something that never reached disk. EventSource reconnects by
 * itself, and each stream opens with a snapshot, so a dropped connection
 * resynchronises instead of leaving stale numbers on screen.
 */
export function useLive(jobId?: string) {
  const [queue, setQueue] = useState<QueueSnapshot>({
    running: null,
    queued: 0,
    queue: [],
  })
  const [job, setJob] = useState<Job | null>(null)
  const [connected, setConnected] = useState(false)
  const jobRef = useRef(jobId)
  jobRef.current = jobId

  useEffect(() => {
    const url = jobId ? `/api/jobs/${jobId}/events` : '/api/events'
    const source = new EventSource(url)

    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    source.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as SSEMessage
        setQueue({
          running: message.running ?? null,
          queued: message.queued ?? 0,
          queue: message.queue ?? [],
        })
        if (message.job !== undefined && message.job !== null) {
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

  return { queue, job, connected }
}
