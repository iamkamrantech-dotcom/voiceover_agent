import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { FitResponse, Storyboard, UploadResult } from '../api/types'
import { FitBar } from '../components/FitBar'
import { useToast } from '../components/Toast'
import {
  Button,
  Checkbox,
  Field,
  Panel,
  Skeleton,
  inputClass,
} from '../components/ui'
import { seconds, timeRange } from '../lib/format'

type Step = 1 | 2 | 3

const STEPS: { n: Step; label: string }[] = [
  { n: 1, label: 'Upload' },
  { n: 2, label: 'Storyboard' },
  { n: 3, label: 'Options' },
]

function Stepper({ step }: { step: Step }) {
  return (
    <ol className="flex items-center gap-2" aria-label="Progress">
      {STEPS.map((item, i) => {
        const state =
          item.n === step ? 'current' : item.n < step ? 'done' : 'todo'
        return (
          <li key={item.n} className="flex items-center gap-2">
            <span
              className={`flex items-center gap-2 rounded-md px-2.5 py-1 text-sm ${
                state === 'current'
                  ? 'bg-accent-wash font-medium text-accent'
                  : state === 'done'
                    ? 'text-text'
                    : 'text-faint'
              }`}
              aria-current={state === 'current' ? 'step' : undefined}
            >
              <span className="tnum">{item.n}</span>
              {item.label}
            </span>
            {i < STEPS.length - 1 && (
              <span className="h-px w-5 bg-border" aria-hidden="true" />
            )}
          </li>
        )
      })}
    </ol>
  )
}

function DropZone({
  label,
  hint,
  accept,
  file,
  onFile,
  id,
}: {
  label: string
  hint: string
  accept: string
  file: File | null
  onFile: (file: File) => void
  id: string
}) {
  const [over, setOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        const dropped = e.dataTransfer.files[0]
        if (dropped) onFile(dropped)
      }}
      className={`rounded-lg border border-dashed p-5 text-center transition-colors ${
        over ? 'border-accent bg-accent-wash' : 'border-border bg-bg'
      }`}
    >
      <input
        ref={inputRef}
        id={id}
        type="file"
        accept={accept}
        className="sr-only"
        onChange={(e) => {
          const picked = e.target.files?.[0]
          if (picked) onFile(picked)
        }}
      />
      <p className="text-base font-medium">{file ? file.name : label}</p>
      <p className="mt-0.5 text-xs text-faint">
        {file ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : hint}
      </p>
      <Button
        className="mt-3"
        size="sm"
        onClick={() => inputRef.current?.click()}
      >
        {file ? 'Choose a different file' : 'Choose file'}
      </Button>
    </div>
  )
}

export default function NewVideo() {
  const navigate = useNavigate()
  const toast = useToast()

  const [step, setStep] = useState<Step>(1)
  const [videoFile, setVideoFile] = useState<File | null>(null)
  const [musicFile, setMusicFile] = useState<File | null>(null)
  const [upload, setUpload] = useState<UploadResult | null>(null)
  const [uploading, setUploading] = useState(false)

  const [pasted, setPasted] = useState('')
  const [board, setBoard] = useState<Storyboard | null>(null)
  const [texts, setTexts] = useState<string[]>([])
  const [fit, setFit] = useState<FitResponse | null>(null)
  const [analysing, setAnalysing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [draft, setDraft] = useState(true)
  const [keepAudio, setKeepAudio] = useState(false)
  const [burnSubs, setBurnSubs] = useState(false)
  const [starting, setStarting] = useState(false)

  const abortRef = useRef<AbortController | null>(null)

  /* ---------------------------------------------------- step 1: upload */

  async function doUpload() {
    if (!videoFile) return
    setUploading(true)
    setError(null)
    try {
      const result = await api.upload({
        video: videoFile,
        ...(musicFile ? { music: musicFile } : {}),
      })
      setUpload(result)
      await buildStoryboard(result.video as string, pasted)
      setStep(2)
    } catch (e) {
      setError((e as ApiError).message)
    } finally {
      setUploading(false)
    }
  }

  async function buildStoryboard(video: string, scriptText: string) {
    setAnalysing(true)
    try {
      const result = await api.storyboard(video, scriptText)
      setBoard(result)
      setTexts(result.scenes.map((s) => s.text))
    } catch (e) {
      setError((e as ApiError).message)
    } finally {
      setAnalysing(false)
    }
  }

  /* ------------------------------------------- step 2: live fit check */

  const recheck = useCallback(
    (nextTexts: string[]) => {
      if (!upload?.video) return
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller
      api
        .checkFit(
          upload.video,
          nextTexts.map((text, scene) => ({ scene, text })),
          draft,
          controller.signal,
        )
        .then(setFit)
        .catch((e: ApiError) => {
          if (e.status !== 0) setError(e.message)
        })
    },
    [upload?.video, draft],
  )

  // Debounced: the fit check is cheap but not free, and firing it on every
  // keystroke would make typing feel heavy.
  useEffect(() => {
    if (step !== 2 && step !== 3) return
    if (texts.length === 0) return
    const timer = window.setTimeout(() => recheck(texts), 400)
    return () => window.clearTimeout(timer)
  }, [texts, step, recheck])

  function setSceneText(index: number, value: string) {
    setTexts((current) => {
      const next = [...current]
      next[index] = value
      return next
    })
  }

  async function applyPastedScript() {
    if (!upload?.video || !pasted.trim()) return
    await buildStoryboard(upload.video, pasted)
    toast.show('Script split across the scenes', 'success')
  }

  /* -------------------------------------------------- step 3: generate */

  async function generate() {
    if (!upload?.video) return
    setStarting(true)
    try {
      const { id } = await api.createJob({
        video: upload.video,
        blocks: texts.map((text, scene) => ({ scene, text })),
        music: upload.music ?? null,
        draft,
        keep_original_audio: keepAudio,
        burn_subs: burnSubs,
        words: fit?.words ?? 0,
        total_takes: fit?.total_takes ?? 0,
        estimate_seconds: fit?.estimate_seconds ?? 0,
      })
      toast.show('Job queued', 'success')
      navigate(`/jobs/${id}`)
    } catch (e) {
      setError((e as ApiError).message)
      setStarting(false)
    }
  }

  /* ------------------------------------------------------------ render */

  const rowFor = (scene: number) => fit?.rows.find((r) => r.scene === scene)
  const problems = fit?.rows.filter((r) => r.state === 'over').length ?? 0
  const written = texts.filter((t) => t.trim()).length

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">New video</h1>
        <Stepper step={step} />
      </div>

      {error && (
        <div className="rounded-md border border-border bg-danger-wash px-3.5 py-2.5 text-base text-danger">
          {error}
        </div>
      )}

      {/* ---------------------------------------------------- step 1 */}
      {step === 1 && (
        <Panel title="Choose your files">
          <div className="space-y-4 p-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <DropZone
                id="video-input"
                label="Drop a video here"
                hint="MP4 or MOV slideshow"
                accept="video/*"
                file={videoFile}
                onFile={setVideoFile}
              />
              <DropZone
                id="music-input"
                label="Background music"
                hint="Optional, ducked under the voice"
                accept="audio/*"
                file={musicFile}
                onFile={setMusicFile}
              />
            </div>

            <Field
              label="Script"
              htmlFor="script-text"
              hint="Optional here. You can also write it scene by scene in the next step."
            >
              <textarea
                id="script-text"
                value={pasted}
                onChange={(e) => setPasted(e.target.value)}
                rows={6}
                placeholder="Paste the full script and it will be split across the scenes."
                className={inputClass}
              />
            </Field>

            <div className="flex items-center gap-3">
              <Button
                variant="primary"
                disabled={!videoFile || uploading}
                onClick={doUpload}
                data-testid="upload-continue"
              >
                {uploading ? 'Analysing…' : 'Continue'}
              </Button>
              {uploading && (
                <span className="text-sm text-muted">
                  Uploading and detecting scenes. No narration is generated yet.
                </span>
              )}
            </div>
          </div>
        </Panel>
      )}

      {/* ---------------------------------------------------- step 2 */}
      {step === 2 && (
        <>
          <Panel
            title="Storyboard"
            action={
              board && (
                <span className="text-sm text-muted">
                  {board.scenes.length} scenes, split by{' '}
                  {board.method === 'none' ? 'hand' : board.method}
                </span>
              )
            }
          >
            <div className="space-y-3 p-4">
              <p className="text-base text-muted">
                Each image gets its own narration. Edit any block and the fit
                updates as you type.
              </p>
              <div className="space-y-2">
                <textarea
                  aria-label="Paste a full script"
                  value={pasted}
                  onChange={(e) => setPasted(e.target.value)}
                  rows={3}
                  placeholder="Paste a full script here to spread it across the scenes again."
                  className={`${inputClass} resize-y`}
                />
                <Button onClick={applyPastedScript} disabled={!pasted.trim()}>
                  Split across scenes
                </Button>
              </div>
            </div>
          </Panel>

          {analysing || !board ? (
            <Panel>
              <div className="space-y-3 p-4">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-28 w-full" />
                ))}
              </div>
            </Panel>
          ) : (
            <div className="space-y-3">
              {board.scenes.map((scene) => {
                const row = rowFor(scene.index)
                return (
                  <Panel key={scene.index}>
                    <div className="flex flex-col gap-4 p-4 sm:flex-row">
                      <div className="sm:w-56 sm:shrink-0">
                        {/* Not lazy: these are small JPEGs from localhost,
                            and a blank frame while scrolling the storyboard
                            defeats the point of having thumbnails at all. */}
                        <img
                          src={scene.keyframe}
                          alt={`Scene ${scene.index + 1}`}
                          decoding="async"
                          className="aspect-video w-full rounded-md border border-border bg-raised object-cover"
                        />
                        <div className="mt-1.5 flex items-center justify-between text-xs">
                          <span className="font-medium">
                            Scene {scene.index + 1}
                          </span>
                          <span className="tnum text-faint">
                            {timeRange(scene.start, scene.end)}
                          </span>
                        </div>
                      </div>

                      <div className="flex min-w-0 flex-1 flex-col gap-2.5">
                        <textarea
                          aria-label={`Narration for scene ${scene.index + 1}`}
                          data-testid={`scene-text-${scene.index}`}
                          value={texts[scene.index] ?? ''}
                          onChange={(e) =>
                            setSceneText(scene.index, e.target.value)
                          }
                          rows={3}
                          placeholder="No narration on this image. The previous block carries over."
                          className={inputClass}
                        />
                        {row ? (
                          <FitBar row={row} />
                        ) : (
                          <Skeleton className="h-6 w-full" />
                        )}
                      </div>
                    </div>
                  </Panel>
                )
              })}
            </div>
          )}

          <Panel>
            <div className="flex flex-wrap items-center justify-between gap-3 p-4">
              <div className="text-base">
                <span className="tnum font-medium">{fit?.words ?? 0}</span>{' '}
                <span className="text-muted">words over</span>{' '}
                <span className="tnum font-medium">{written}</span>{' '}
                <span className="text-muted">blocks</span>
                {problems > 0 && (
                  <span className="ml-3 text-danger">
                    {problems} {problems === 1 ? 'block is' : 'blocks are'} too
                    long
                  </span>
                )}
              </div>
              <div className="flex gap-2">
                <Button onClick={() => setStep(1)}>Back</Button>
                <Button
                  variant="primary"
                  onClick={() => setStep(3)}
                  disabled={written === 0}
                  data-testid="to-options"
                >
                  Continue
                </Button>
              </div>
            </div>
          </Panel>
        </>
      )}

      {/* ---------------------------------------------------- step 3 */}
      {step === 3 && (
        <Panel title="Options">
          <div className="space-y-5 p-4">
            <Field label="Quality" htmlFor="quality">
              <select
                id="quality"
                className={inputClass}
                value={draft ? 'draft' : 'final'}
                onChange={(e) => setDraft(e.target.value === 'draft')}
              >
                <option value="draft">Draft — one take per block, fast</option>
                <option value="final">
                  Final — several takes per block, best match
                </option>
              </select>
            </Field>

            <div className="space-y-3">
              <Checkbox
                id="keep-audio"
                label="Keep the video's own audio"
                description="Used as the bed instead of a music file."
                checked={keepAudio}
                onChange={setKeepAudio}
              />
              <Checkbox
                id="burn-subs"
                label="Burn subtitles into the picture"
                description="A separate .srt file is always written either way."
                checked={burnSubs}
                onChange={setBurnSubs}
              />
            </div>

            <dl className="divide-y divide-border rounded-md border border-border text-base">
              {[
                ['Video', upload?.name ?? '—'],
                [
                  'Resolution',
                  upload?.meta?.width
                    ? `${upload.meta.width}×${upload.meta.height} at ${upload.meta.fps} fps`
                    : '—',
                ],
                ['Length', seconds(upload?.meta?.duration ?? null)],
                ['Music', upload?.music_name ?? 'None'],
                ['Blocks', `${written} with narration`],
                [
                  'Takes',
                  `${fit?.total_takes ?? 0} in total (${fit?.takes ?? 1} per block)`,
                ],
              ].map(([label, value]) => (
                <div
                  key={label}
                  className="flex items-center justify-between gap-3 px-3.5 py-2"
                >
                  <dt className="text-muted">{label}</dt>
                  <dd className="tnum truncate text-right">{value}</dd>
                </div>
              ))}
            </dl>

            <div className="rounded-md border border-border bg-raised px-3.5 py-3">
              <p className="text-base">
                Estimated processing time:{' '}
                <span className="tnum font-semibold">
                  {seconds(fit?.estimate_seconds ?? null)}
                </span>
              </p>
              <p className="mt-0.5 text-xs text-faint">
                Based on this machine's measured speed. The first run of a
                model is slower because it has to load.
              </p>
            </div>

            <div className="flex justify-between gap-2">
              <Button onClick={() => setStep(2)}>Back</Button>
              <Button
                variant="primary"
                onClick={generate}
                disabled={starting}
                data-testid="generate"
              >
                {starting ? 'Starting…' : 'Generate video'}
              </Button>
            </div>
          </div>
        </Panel>
      )}
    </div>
  )
}
