import { useEffect, useState } from 'react'
import { Popover } from './ui'

/**
 * The finished video, with the shortcuts an editor expects.
 *
 * J/K/L is the standard shuttle: J back, K stop, L forward, and pressing
 * the same key again doubles the speed. Arrow keys step one frame, which
 * is the only way to check a cut properly.
 *
 * Every shortcut is ignored while a text field has focus - the block text
 * editor is on the same page, and a space in the middle of a sentence must
 * be a space.
 */

const KEYS: [string, string][] = [
  ['Space', 'Play or pause'],
  ['K', 'Pause'],
  ['J / L', 'Back or forward, faster each press'],
  ['← / →', 'One frame'],
  ['Shift + ← / →', 'One second'],
  ['M', 'Mute'],
  ['Home / End', 'Start or end'],
]

function isTyping(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  if (!element) return false
  const tag = element.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
    || element.isContentEditable
}

export function VideoPlayer({
  src,
  fps,
  videoRef,
}: {
  src: string
  fps: number
  videoRef: React.RefObject<HTMLVideoElement>
}) {
  const [rate, setRate] = useState(1)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const video = videoRef.current
      if (!video || isTyping(e.target) || e.metaKey || e.ctrlKey) return
      const frame = 1 / (fps || 25)

      switch (e.key) {
        case ' ':
          e.preventDefault()
          if (video.paused) void video.play()
          else video.pause()
          break
        case 'k':
        case 'K':
          e.preventDefault()
          video.pause()
          video.playbackRate = 1
          setRate(1)
          break
        case 'l':
        case 'L': {
          e.preventDefault()
          const next = video.paused || video.playbackRate < 1
            ? 1 : Math.min(8, video.playbackRate * 2)
          video.playbackRate = next
          setRate(next)
          void video.play()
          break
        }
        case 'j':
        case 'J': {
          // HTML video will not play backwards, so J steps back instead,
          // further each press - the same gesture, honestly implemented.
          e.preventDefault()
          video.pause()
          const step = video.playbackRate >= 2 ? 2 : 1
          video.playbackRate = Math.min(8, Math.max(1, video.playbackRate * 2))
          setRate(video.playbackRate)
          video.currentTime = Math.max(0, video.currentTime - step)
          break
        }
        case 'ArrowLeft':
          e.preventDefault()
          video.pause()
          video.currentTime = Math.max(0, video.currentTime - (e.shiftKey ? 1 : frame))
          break
        case 'ArrowRight':
          e.preventDefault()
          video.pause()
          video.currentTime += e.shiftKey ? 1 : frame
          break
        case 'm':
        case 'M':
          e.preventDefault()
          video.muted = !video.muted
          break
        case 'Home':
          e.preventDefault()
          video.currentTime = 0
          break
        case 'End':
          e.preventDefault()
          video.currentTime = video.duration || 0
          break
        default:
          break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [fps, videoRef])

  return (
    <video
      ref={videoRef}
      controls
      playsInline
      preload="metadata"
      src={src}
      className="w-full rounded-md bg-black"
      style={{ aspectRatio: '16 / 9' }}
      data-testid="player"
      aria-label={rate !== 1 ? `Playing at ${rate} times speed` : undefined}
    />
  )
}

/** The shortcut list, shown beside the downloads rather than floating
 *  under the player on a line of its own. */
export function PlayerKeys() {
  return (
    <Popover label="Keyboard shortcuts" title="While the player has focus">
      <dl className="space-y-1.5">
        {KEYS.map(([key, what]) => (
          <div key={key} className="flex justify-between gap-3 text-xs">
            <dt className="font-mono text-muted">{key}</dt>
            <dd className="text-right text-faint">{what}</dd>
          </div>
        ))}
      </dl>
    </Popover>
  )
}
