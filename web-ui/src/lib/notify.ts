import type { Job } from '../api/types'

/**
 * A browser notification when a job ends.
 *
 * A job takes hours on this machine, so nobody watches the page. Permission
 * is asked once, from a click - a prompt on page load is the thing browsers
 * and people both dislike - and a refusal is never asked about again.
 */

const ASKED = 'va-notify-asked'

export function canAsk(): boolean {
  if (!('Notification' in window)) return false
  if (Notification.permission !== 'default') return false
  try {
    return localStorage.getItem(ASKED) !== 'yes'
  } catch {
    return true
  }
}

export async function askToNotify(): Promise<boolean> {
  if (!('Notification' in window)) return false
  try {
    localStorage.setItem(ASKED, 'yes')
  } catch {
    /* a private window still gets to ask, it just asks again next time */
  }
  const result = await Notification.requestPermission()
  return result === 'granted'
}

export function notifyFinished(job: Job): void {
  if (!('Notification' in window) || Notification.permission !== 'granted') {
    return
  }
  const done = job.state === 'done'
  try {
    new Notification(done ? 'Video ready' : 'Job did not finish', {
      body: done
        ? `${job.name} finished.`
        : `${job.name}: ${job.detail || job.state}.`,
      tag: job.id,
    })
  } catch {
    /* some browsers only allow notifications from a service worker */
  }
}
