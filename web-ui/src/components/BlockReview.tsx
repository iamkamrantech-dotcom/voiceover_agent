import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type {
  BlockDetail, FitRow, RevisionPlan, ReviseRequest, Take,
} from '../api/types'
import { percent, secs1, seconds } from '../lib/format'
import { Button, ConfirmDialog, Field, inputClass } from './ui'
import { FitBar } from './FitBar'
import { useToast } from './Toast'

/**
 * One block, every take it produced, and the three things that can be done
 * about it.
 *
 * The point of the panel is that a bad block costs one block to fix. Each
 * action states its price before it is taken: choosing a take generates
 * nothing at all, the other two generate for this block only, and the
 * rebuild afterwards is the same length whichever you pick.
 */

function Similarity({ take, reliable }: { take: Take; reliable: boolean }) {
  if (take.similarity === null) {
    return <span className="text-faint">no score</span>
  }
  return (
    <span
      className="tnum"
      title={
        reliable
          ? 'How close this take sounds to the client recording'
          : 'Too short for this score to mean much - scoring needs a few '
            + 'seconds of audio'
      }
      style={{ color: reliable ? 'var(--text)' : 'var(--faint)' }}
    >
      {percent(take.similarity)}
      {reliable ? '' : '*'}
    </span>
  )
}

function TakeRow({
  take,
  reliable,
  playing,
  onPlay,
  onChoose,
  busy,
}: {
  take: Take
  reliable: boolean
  playing: boolean
  onPlay: () => void
  onChoose: () => void
  busy: boolean
}) {
  return (
    <li className="flex items-center gap-3 border-b border-border py-2 last:border-b-0">
      <Button
        size="sm"
        variant="ghost"
        onClick={onPlay}
        disabled={!take.playable}
        aria-label={`Play take ${take.number}`}
        data-testid={`play-take-${take.number}`}
      >
        {playing ? 'Stop' : 'Play'}
      </Button>
      <div className="min-w-0 flex-1">
        <span className="flex items-baseline gap-2 text-base">
          Take {take.number}
          {take.chosen && (
            <span className="rounded-sm bg-success-wash px-1.5 py-px text-2xs text-success">
              In use
            </span>
          )}
          {!take.words_ok && (
            <span className="rounded-sm bg-warning-wash px-1.5 py-px text-2xs text-warning">
              Wrong words
            </span>
          )}
        </span>
        <span className="tnum block text-xs text-faint">
          {secs1(take.duration)} · seed {take.seed}
        </span>
      </div>
      <Similarity take={take} reliable={reliable} />
      <Button
        size="sm"
        onClick={onChoose}
        disabled={take.chosen || busy || !take.playable}
        data-testid={`use-take-${take.number}`}
      >
        {take.chosen ? 'In use' : 'Use this'}
      </Button>
    </li>
  )
}

export function BlockReview({
  jobId,
  block,
  takesRecorded,
  canRevise,
  onRevised,
}: {
  jobId: string
  block: BlockDetail
  takesRecorded: boolean
  canRevise: boolean
  onRevised: () => void
}) {
  const [text, setText] = useState(block.text)
  const [extra, setExtra] = useState(2)
  const [plan, setPlan] = useState<RevisionPlan | null>(null)
  const [pending, setPending] = useState<ReviseRequest | null>(null)
  const [busy, setBusy] = useState(false)
  const [fit, setFit] = useState<FitRow | null>(null)
  const [playing, setPlaying] = useState<number | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const abort = useRef<AbortController | null>(null)
  const toast = useToast()

  useEffect(() => {
    setText(block.text)
    setPlan(null)
    setPending(null)
  }, [block.block, block.text])

  useEffect(() => () => audio.current?.pause(), [])

  // Re-check the fit as the text is edited, so the cost of a longer line is
  // visible before any of it is generated. Debounced in an effect keyed on
  // the text, and the in-flight check is aborted, so only the latest answer
  // can land.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      abort.current?.abort()
      const controller = new AbortController()
      abort.current = controller
      api
        .jobFit(jobId, [{ block: block.block, text }], controller.signal)
        .then((r) =>
          setFit(r.rows.find((row) => row.scene === block.block) ?? null))
        .catch(() => undefined)
    }, 400)
    return () => window.clearTimeout(timer)
  }, [jobId, block.block, text])

  function play(url: string, id: number) {
    if (playing === id) {
      audio.current?.pause()
      setPlaying(null)
      return
    }
    audio.current?.pause()
    const element = new Audio(url)
    element.onended = () => setPlaying(null)
    void element.play().catch(() =>
      toast.show('That audio could not be played.', 'error'),
    )
    audio.current = element
    setPlaying(id)
  }

  async function propose(request: ReviseRequest) {
    setBusy(true)
    try {
      const next = await api.planRevision(jobId, request)
      setPlan(next)
      setPending(request)
    } catch (e) {
      toast.show((e as ApiError).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function confirm() {
    if (!pending) return
    setBusy(true)
    try {
      await api.revise(jobId, pending)
      toast.show('Rebuilding the video. You can watch it on this page.',
                 'success')
      setPlan(null)
      setPending(null)
      onRevised()
    } catch (e) {
      toast.show((e as ApiError).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const edited = text.trim() !== block.text.trim()

  return (
    <div className="flex flex-col gap-5 p-4">
      <section>
        <h3 className="mb-1 text-xs font-medium text-muted">
          {block.scene_ids.length > 1
            ? `Scenes ${block.scene_ids.map((s) => s + 1).join(', ')}`
            : `Scene ${(block.scene_ids[0] ?? 0) + 1}`}
          <span className="tnum ml-2 text-faint">
            {secs1(block.start)} – {secs1(block.end)}
          </span>
        </h3>
        <Field label="Narration" htmlFor="block-text"
               hint={canRevise
                 ? 'Change the words and this block alone is generated again.'
                 : 'Editing is available once the job has finished.'}>
          <textarea
            id="block-text"
            rows={4}
            className={inputClass}
            value={text}
            disabled={!canRevise}
            onChange={(e) => setText(e.target.value)}
            data-testid="block-text"
          />
        </Field>
        {fit && (
          <div className="mt-2">
            <FitBar row={fit} />
          </div>
        )}
        {!block.words_ok && (
          <p className="mt-2 rounded-md bg-warning-wash p-2 text-xs text-warning">
            The chosen take does not say exactly what is written. Heard as:
            “{block.transcript}”
          </p>
        )}
      </section>

      <section>
        <h3 className="mb-1 text-xs font-medium text-muted">
          Takes ({block.takes.length})
        </h3>
        {!takesRecorded && (
          <p className="mb-2 text-xs text-faint">
            This job ran before every take was kept, so only the one in use is
            listed. Generating more will add to it.
          </p>
        )}
        <ul className="border-t border-border">
          {block.takes.map((take) => (
            <TakeRow
              key={take.seed}
              take={take}
              reliable={block.similarity_reliable}
              playing={playing === take.seed}
              onPlay={() =>
                play(api.takeUrl(jobId, block.block, take.seed), take.seed)
              }
              onChoose={() =>
                propose({ block: block.block, action: 'choose_take',
                          seed: take.seed })
              }
              busy={busy || !canRevise}
            />
          ))}
        </ul>
        {!block.similarity_reliable && block.takes.length > 0 && (
          <p className="mt-2 text-xs text-faint">
            * This block is under {'≈'}3.5 seconds, which is too short
            for a similarity score to mean much. The number is shown, but
            nothing is judged on it.
          </p>
        )}
        <Button
          size="sm"
          className="mt-3"
          onClick={() => play(api.blockAudioUrl(jobId, block.block), -1)}
        >
          {playing === -1 ? 'Stop' : 'Play it as it is in the video'}
        </Button>
      </section>

      <section className="flex flex-wrap items-end gap-2">
        <Field label="More takes" htmlFor="extra-takes"
               hint="Generated on fresh seeds for this block only.">
          <select
            id="extra-takes"
            className={inputClass}
            value={extra}
            disabled={!canRevise}
            onChange={(e) => setExtra(Number(e.target.value))}
          >
            {[1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </Field>
        <Button
          onClick={() =>
            propose({ block: block.block, action: 'more_takes', extra })
          }
          disabled={busy || !canRevise}
          data-testid="more-takes"
        >
          Generate
        </Button>
        <Button
          variant={edited ? 'primary' : 'secondary'}
          onClick={() =>
            propose({ block: block.block, action: 'edit_text', text })
          }
          disabled={busy || !canRevise || !edited}
          data-testid="regenerate-block"
        >
          Regenerate with this text
        </Button>
      </section>

      <ConfirmDialog
        open={plan !== null}
        title="Rebuild the video?"
        confirmLabel="Rebuild"
        busy={busy}
        onCancel={() => { setPlan(null); setPending(null) }}
        onConfirm={confirm}
        body={
          plan && (
            <div className="space-y-2">
              <p>{plan.note}.</p>
              <ul className="list-disc pl-4">
                {plan.steps.map((step, i) => (
                  <li key={i}>{step}</li>
                ))}
              </ul>
              <p className="tnum">
                {plan.takes_to_make === 0
                  ? 'No narration has to be generated - every take already '
                    + 'exists. '
                  : `${plan.takes_to_make} take${plan.takes_to_make === 1 ? '' : 's'} `
                    + 'will be generated for this block. '}
                About {seconds(plan.estimate_seconds)} in total, and it will be
                saved as version {plan.version}.
              </p>
            </div>
          )
        }
      />
    </div>
  )
}
