import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { VoiceProfile, VoiceState } from '../api/types'
import { useToast } from '../components/Toast'
import {
  Button,
  EmptyState,
  ErrorState,
  Field,
  Panel,
  Skeleton,
  inputClass,
} from '../components/ui'
import { percent, timeRange, when } from '../lib/format'

function CandidateRow({
  profile,
  candidate,
  isActive,
  onUse,
}: {
  profile: VoiceProfile
  candidate: VoiceProfile['candidates'][number]
  isActive: boolean
  onUse: () => void
}) {
  const best = profile.best === candidate.name
  return (
    <div className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-base font-medium">{candidate.name}</span>
          {best && (
            <span className="rounded-sm bg-accent-wash px-1.5 py-0.5 text-xs font-medium text-accent">
              Best match
            </span>
          )}
          {isActive && (
            <span className="rounded-sm bg-success-wash px-1.5 py-0.5 text-xs font-medium text-success">
              In use
            </span>
          )}
        </div>
        <p className="tnum mt-0.5 text-xs text-faint">
          {timeRange(candidate.start, candidate.end)} of the recording
          {candidate.similarity !== null &&
            ` · ${percent(candidate.similarity)} match`}
        </p>
      </div>
      {candidate.sample && (
        <audio
          controls
          preload="none"
          className="h-8 w-full sm:w-56"
          src={api.voiceAudioUrl(candidate.sample)}
        />
      )}
      <Button size="sm" onClick={onUse} disabled={isActive}>
        {isActive ? 'Active' : 'Use this'}
      </Button>
    </div>
  )
}

export default function Voice() {
  const [state, setState] = useState<VoiceState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [recording, setRecording] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const toast = useToast()

  const load = useCallback(() => {
    setError(null)
    api
      .voice()
      .then(setState)
      .catch((e: ApiError) => setError(e.message))
  }, [])

  useEffect(load, [load])

  async function prepare() {
    if (!recording) return
    setBusy(true)
    try {
      await api.prepareVoice(recording, name)
      toast.show('Candidates ready. Listen and pick one.', 'success')
      setRecording(null)
      setName('')
      load()
    } catch (e) {
      toast.show((e as ApiError).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function activate(profile: string, candidate: string) {
    try {
      await api.activateVoice(profile, candidate)
      toast.show('Active voice updated', 'success')
      load()
    } catch (e) {
      toast.show((e as ApiError).message, 'error')
    }
  }

  if (error) return <ErrorState message={error} onRetry={load} />

  return (
    <div className="space-y-5">
      <h1 className="text-lg font-semibold">Voice</h1>

      <Panel title="Active voice">
        {state === null ? (
          <div className="p-4">
            <Skeleton className="h-12 w-full" />
          </div>
        ) : (
          <div className="space-y-3 p-4">
            <div>
              <p className="text-base font-medium">
                {state.active
                  ? `${state.active.profile} / ${state.active.candidate}`
                  : 'Default reference from settings'}
              </p>
              <p className="mt-0.5 break-all text-xs text-faint">
                {state.active
                  ? `In use since ${when(state.active.set_at)}`
                  : state.fallback}
              </p>
            </div>
            <audio
              controls
              preload="none"
              className="w-full"
              src={api.voiceAudioUrl(
                state.active ? state.active.reference : state.fallback,
              )}
            />
          </div>
        )}
      </Panel>

      <Panel title="Add a new voice">
        <div className="space-y-4 p-4">
          <p className="text-base text-muted">
            Upload a clean recording of the client, at least 20 to 30 seconds.
            The best ten-second sections are cut out, each one speaks a test
            sentence, and the closest match to the full recording is marked.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Recording" htmlFor="voice-file">
              <input
                ref={fileRef}
                id="voice-file"
                type="file"
                accept="audio/*"
                onChange={(e) => setRecording(e.target.files?.[0] ?? null)}
                className={inputClass}
              />
            </Field>
            <Field label="Name" htmlFor="voice-name" hint="Optional">
              <input
                id="voice-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="client_november"
                className={inputClass}
              />
            </Field>
          </div>
          <Button variant="primary" onClick={prepare} disabled={!recording || busy}>
            {busy ? 'Preparing…' : 'Prepare candidates'}
          </Button>
          {busy && (
            <p className="text-sm text-muted">
              This loads the voice model and generates a sample for each
              candidate, so it takes a few minutes on this machine.
            </p>
          )}
        </div>
      </Panel>

      <Panel title="Saved voices">
        {state === null ? (
          <div className="p-4">
            <Skeleton className="h-20 w-full" />
          </div>
        ) : state.profiles.length === 0 ? (
          <EmptyState
            title="No saved voices"
            body="Upload a client recording above and the candidates will be listed here. Older voices are kept so you can switch back."
          />
        ) : (
          <div className="divide-y divide-border">
            {state.profiles.map((profile) => (
              <div key={profile.name}>
                <div className="flex items-baseline justify-between gap-3 bg-raised px-4 py-2">
                  <span className="text-base font-medium">{profile.name}</span>
                  <span className="text-xs text-faint">
                    {when(profile.created)}
                  </span>
                </div>
                <div className="divide-y divide-border">
                  {profile.candidates.map((candidate) => (
                    <CandidateRow
                      key={candidate.name}
                      profile={profile}
                      candidate={candidate}
                      isActive={
                        state.active?.profile === profile.name &&
                        state.active?.candidate === candidate.name
                      }
                      onUse={() => activate(profile.name, candidate.name)}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  )
}
