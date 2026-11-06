import {
  useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
} from 'react'
import type { BlockDetail, Timeline as TimelineData } from '../../api/types'
import type { Waveform } from '../../api/types'
import { api } from '../../api/client'
import { clock, percent, secs1 } from '../../lib/format'
import { Button } from '../ui'
import { WaveTrack } from './WaveTrack'

/**
 * The finished video, laid out in time.
 *
 *   scenes    thumbnails where they really fall, extended parts hatched,
 *             freezes marked
 *   voice     the blocks over a real waveform
 *   subtitles the cues
 *
 * Two things make it smooth on a ten minute video. The playhead is one
 * absolutely positioned element written straight from a requestAnimationFrame
 * loop - never through React state, so playing does not re-render anything.
 * And only what is on screen is built: thumbnails outside the viewport are
 * not rendered, and the waveform canvas is viewport-sized, not timeline-sized.
 */

const ROW = { scenes: 44, voice: 40, cues: 22 }
const MIN_PPS = 2
const MAX_WIDTH = 32_000   // a canvas and a DOM row both dislike more

export function Timeline({
  jobId,
  data,
  wave,
  blocks,
  video,
  selected,
  onSelect,
}: {
  jobId: string
  data: TimelineData
  wave: Waveform | null
  blocks: BlockDetail[]
  video: React.RefObject<HTMLVideoElement>
  selected: number | null
  onSelect: (block: number) => void
}) {
  const duration = data.output_duration || 1
  const viewport = useRef<HTMLDivElement>(null)
  const playhead = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const [pps, setPps] = useState(0)
  const [scroll, setScroll] = useState(0)
  const [hover, setHover] = useState<number | null>(null)
  const [theme, setTheme] = useState('')

  // Fit the whole thing on screen to begin with: the first question is
  // always "where does the narration sit overall".
  useLayoutEffect(() => {
    const element = viewport.current
    if (!element) return
    const measure = () => {
      const next = element.clientWidth
      setWidth(next)
      setPps((current) => (current > 0 ? current : next / duration))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [duration])

  // The canvas reads its colour from the tokens, so it has to be told when
  // they change.
  useEffect(() => {
    const read = () =>
      setTheme(document.documentElement.dataset.theme ?? 'system')
    read()
    const observer = new MutationObserver(read)
    observer.observe(document.documentElement, {
      attributes: true, attributeFilter: ['data-theme'],
    })
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    media.addEventListener('change', read)
    return () => {
      observer.disconnect()
      media.removeEventListener('change', read)
    }
  }, [])

  const contentWidth = Math.min(MAX_WIDTH, Math.max(width, duration * pps))
  const scale = contentWidth / duration          // pixels per second, actual
  const toX = useCallback((t: number) => t * scale, [scale])

  // ---- the playhead, outside React ------------------------------------
  useEffect(() => {
    let frame = 0
    const tick = () => {
      const element = video.current
      const bar = playhead.current
      if (element && bar) {
        const x = element.currentTime * scale
        bar.style.transform = `translateX(${x}px)`
        bar.style.opacity = '1'
        // Keep it in view while playing, without fighting a manual scroll.
        const box = viewport.current
        if (box && !element.paused) {
          const left = box.scrollLeft
          if (x < left + 24 || x > left + box.clientWidth - 24) {
            box.scrollLeft = Math.max(0, x - box.clientWidth / 2)
          }
        }
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [scale, video])

  // ---- seeking ---------------------------------------------------------
  const seekFromEvent = useCallback(
    (clientX: number) => {
      const box = viewport.current
      const element = video.current
      if (!box || !element) return
      const rect = box.getBoundingClientRect()
      const x = clientX - rect.left + box.scrollLeft
      element.currentTime = Math.max(0, Math.min(duration, x / scale))
    },
    [duration, scale, video],
  )

  const onPointerDown = (e: React.PointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId)
    seekFromEvent(e.clientX)
  }
  const onPointerMove = (e: React.PointerEvent) => {
    if (e.buttons === 1) seekFromEvent(e.clientX)
  }

  // ---- zoom ------------------------------------------------------------
  const fitPps = width / duration
  const zoom = (factor: number) => {
    const box = viewport.current
    if (!box) return
    const centreTime = (box.scrollLeft + box.clientWidth / 2) / scale
    const next = Math.max(MIN_PPS, Math.min(MAX_WIDTH / duration, pps * factor))
    setPps(next)
    requestAnimationFrame(() => {
      const after = Math.min(MAX_WIDTH, Math.max(width, duration * next)) / duration
      box.scrollLeft = Math.max(0, centreTime * after - box.clientWidth / 2)
    })
  }

  // ---- what is on screen ----------------------------------------------
  const view = useMemo(() => {
    const from = scroll / scale - 1
    const to = (scroll + width) / scale + 1
    return { from, to }
  }, [scroll, width, scale])

  const visible = <T extends { start: number; end: number }>(items: T[]) =>
    items.filter((i) => i.end >= view.from && i.start <= view.to)

  const sceneSpans = useMemo(() => {
    // Scenes are not stored on the timeline, but each block knows which
    // scenes it covers and where it sits in the finished video.
    return data.blocks.map((block) => ({
      block: block.block_index,
      scene: block.scene_ids[0] ?? 0,
      start: block.out_start,
      end: block.out_end,
      extension: block.extension,
      freeze: block.freeze_seconds,
      stretch: block.stretch,
    }))
  }, [data.blocks])

  const blockById = useMemo(
    () => new Map(blocks.map((b) => [b.block, b])),
    [blocks],
  )

  return (
    <div className="flex flex-col gap-2 p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted">
          Click to jump. Hatching is picture held longer than it was shot for.
        </p>
        <div className="flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={() => zoom(1 / 1.6)}
                  aria-label="Zoom out" disabled={pps <= fitPps}>
            −
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setPps(fitPps)}>
            Fit
          </Button>
          <Button size="sm" variant="ghost" onClick={() => zoom(1.6)}
                  aria-label="Zoom in"
                  disabled={contentWidth >= MAX_WIDTH}>
            +
          </Button>
        </div>
      </div>

      <div
        ref={viewport}
        className="relative overflow-x-auto overflow-y-hidden rounded-md border border-border bg-raised"
        onScroll={(e) => setScroll(e.currentTarget.scrollLeft)}
        data-testid="timeline"
      >
        <div
          className="relative select-none"
          style={{ width: contentWidth }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          role="slider"
          tabIndex={0}
          aria-label="Video position"
          aria-valuemin={0}
          aria-valuemax={Math.round(duration)}
          aria-valuenow={Math.round(video.current?.currentTime ?? 0)}
          onKeyDown={(e) => {
            const element = video.current
            if (!element) return
            if (e.key === 'ArrowLeft') element.currentTime -= 1
            if (e.key === 'ArrowRight') element.currentTime += 1
          }}
        >
          {/* scenes */}
          <div className="relative border-b border-border"
               style={{ height: ROW.scenes }}>
            {visible(sceneSpans).map((span) => {
              const left = toX(span.start)
              const w = Math.max(2, toX(span.end) - left)
              const heldFrom = toX(span.end - span.extension)
              return (
                <div
                  key={`scene-${span.block}`}
                  className="absolute top-0 overflow-hidden border-r border-border"
                  style={{ left, width: w, height: ROW.scenes }}
                >
                  {w > 26 && (
                    <img
                      src={api.keyframeUrl(jobId, span.scene)}
                      alt=""
                      decoding="async"
                      className="h-full w-full object-cover"
                      style={{ opacity: 0.85 }}
                    />
                  )}
                  {span.extension > 0.01 && (
                    <span
                      className="absolute top-0 h-full"
                      style={{
                        left: heldFrom - left,
                        width: Math.max(2, toX(span.end) - heldFrom),
                        background:
                          'repeating-linear-gradient(45deg, var(--warning-wash) 0 5px, transparent 5px 10px)',
                        borderLeft: span.freeze > 0.01
                          ? '1px solid var(--warning)' : undefined,
                      }}
                      title={`${secs1(span.extension)} of extra picture`}
                    />
                  )}
                </div>
              )
            })}
          </div>

          {/* voice over the waveform */}
          <div className="relative border-b border-border"
               style={{ height: ROW.voice }}>
            <div className="pointer-events-none sticky left-0 top-0"
                 style={{ width, height: ROW.voice }}>
              <WaveTrack
                wave={wave}
                width={width}
                height={ROW.voice}
                startTime={scroll / scale}
                secondsPerPixel={1 / scale}
                theme={theme}
              />
            </div>
            {visible(
              data.blocks
                .filter((b) => !b.silent)
                .map((b) => ({
                  ...b, start: b.voice_start, end: b.voice_end,
                })),
            ).map((block) => {
              const left = toX(block.voice_start)
              const w = Math.max(3, toX(block.voice_end) - left)
              const detail = blockById.get(block.block_index)
              const isSelected = selected === block.block_index
              return (
                <button
                  key={`voice-${block.block_index}`}
                  type="button"
                  className="absolute rounded-sm border text-left"
                  style={{
                    left,
                    width: w,
                    top: 3,
                    height: ROW.voice - 6,
                    borderColor: isSelected ? 'var(--accent)' : 'transparent',
                    background: isSelected
                      ? 'var(--accent-wash)'
                      : 'rgb(0 0 0 / 0.04)',
                  }}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => onSelect(block.block_index)}
                  onMouseEnter={() => setHover(block.block_index)}
                  onMouseLeave={() => setHover(null)}
                  data-testid={`timeline-block-${block.block_index}`}
                  aria-label={`Block ${block.block_index + 1}`}
                >
                  {w > 30 && (
                    <span className="pointer-events-none absolute left-1 top-0.5 text-2xs text-muted">
                      {block.block_index + 1}
                    </span>
                  )}
                  {hover === block.block_index && detail && (
                    <span
                      className="pointer-events-none absolute bottom-full z-20 mb-1 block w-60 rounded-md border border-border bg-surface p-2 text-xs"
                      style={{ boxShadow: 'var(--shadow-pop)' }}
                    >
                      <span className="block font-medium text-text">
                        Block {block.block_index + 1}
                      </span>
                      <span className="mt-1 block text-muted line-clamp-3">
                        {detail.text}
                      </span>
                      <span className="tnum mt-1 block text-faint">
                        {block.stretch > 1.001
                          ? `${((block.stretch - 1) * 100).toFixed(0)}% faster · `
                          : ''}
                        {block.freeze_seconds > 0.01
                          ? `${secs1(block.freeze_seconds)} frozen · `
                          : ''}
                        {detail.similarity !== null
                          ? `${percent(detail.similarity)} match`
                          : 'no score'}
                      </span>
                    </span>
                  )}
                </button>
              )
            })}
          </div>

          {/* subtitles */}
          <div className="relative" style={{ height: ROW.cues }}>
            {visible(data.cues ?? []).map((cue) => {
              const left = toX(cue.start)
              const w = Math.max(2, toX(cue.end) - left)
              return (
                <span
                  key={`cue-${cue.index}`}
                  className="absolute top-[5px] overflow-hidden rounded-sm bg-border-strong"
                  style={{ left, width: w, height: ROW.cues - 10 }}
                  title={cue.text}
                />
              )
            })}
          </div>

          <div
            ref={playhead}
            className="pointer-events-none absolute top-0 z-10 w-px opacity-0"
            style={{
              height: ROW.scenes + ROW.voice + ROW.cues,
              background: 'var(--danger)',
            }}
            aria-hidden="true"
          />
        </div>
      </div>

      <div className="tnum flex justify-between text-2xs text-faint">
        <span>0:00</span>
        <span>
          {clock(data.source_duration)} of pictures became{' '}
          {clock(data.output_duration)}
        </span>
        <span>{clock(duration)}</span>
      </div>
    </div>
  )
}
