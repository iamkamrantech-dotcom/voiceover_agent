import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useLive } from '../api/useLive'
import type { VoiceProfile, VoiceState } from '../api/types'
import { useToast } from '../components/Toast'
import {
  Button, ConfirmDialog, EmptyState, ErrorState, Field, Panel, Skeleton,
  SkeletonRows, inputClass,
} from '../components/ui'
import { fileName, percent, timeRange, when } from '../lib/format'

/**
 * The client's voice.
 *
 * A profile is one recording plus the reference clips cut from it, each
 * scored by cloning the same test sentence. Which clip is active decides
 * what every future job sounds like, so the page is built around hearing
 * them against each other rather than reading their numbers.
 */

/** Preparing a voice is queued work, so it reports its stages like a job. */
const PREPARE_STAGES: Record<string, string> = {
  candidates: 'Cutting candidate clips',
  samples: 'Cloning the test sentence',
  scoring: 'Scoring against the recording',
}

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

function ProfileCard({
  profile,
  activeProfile,
  activeCandidate,
  onUse,
  onRename,
  onDelete,
}: {
  profile: VoiceProfile
  activeProfile: string | null
  activeCandidate: string | null
  onUse: (candidate: string) => void
  onRename: (name: string) => Promise<void>
  onDelete: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(profile.name)
  const [busy, setBusy] = useState(false)
  const isActive = activeProfile === profile.name

  async function save() {
    setBusy(true)
    try {
      await onRename(draft)
      setEditing(false)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel>
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        {editing ? (
          <>
            <input
              className={`${inputClass} max-w-[240px]`}
              value={draft}
              autoFocus
              aria-label="Voice name"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void save()
                if (e.key === 'Escape') {
                  setDraft(profile.name)
                  setEditing(false)
                }
              }}
            />
            <Button size="sm" variant="primary" onClick={save} disabled={busy}>
              Save
            </Button>
            <Button size="sm" variant="ghost"
                    onClick={() => { setDraft(profile.name); setEditing(false) }}>
              Cancel
            </Button>
          </>
        ) : (
          <>
            <h2 className="text-[14px] font-semibold">{profile.name}</h2>
            {isActive && (
              <span className="rounded-sm bg-success-wash px-1.5 py-0.5 text-xs font-medium text-success">
                Active
              </span>
            )}
            <span className="tnum text-xs text-faint">
              Added {when(profile.created)}
            </span>
            <span className="flex-1" />
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              Rename
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={onDelete}
              disabled={isActive}
              title={isActive
                ? 'Switch to another voice before deleting this one'
                : undefined}
            >
              Delete
            </Button>
          </>
        )}
      </header>

      {profile.source && (
        <div className="flex flex-col gap-2 border-b border-border px-4 py-3 sm:flex-row sm:items-center">
          <span className="min-w-0 flex-1 text-xs text-muted">
            The recording these clips were cut from
          </span>
          <audio
            controls
            preload="none"
            className="h-8 w-full sm:w-56"
            src={api.voiceAudioUrl(profile.source)}
          />
        </div>
      )}

      <div className="divide-y divide-border">
        {profile.candidates.map((candidate) => (
          <CandidateRow
            key={candidate.name}
            profile={profile}
            candidate={candidate}
            isActive={isActive && activeCandidate === candidate.name}
            onUse={() => onUse(candidate.name)}
          />
        ))}
      </div>
    </Panel>
  )
}

export default function Voice() {
  const [state, setState] = useState<VoiceState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [recording, setRecording] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const toast = useToast()
  const live = useLive(jobId ?? undefined)

  const load = useCallback(() => {
    api
      .voice()
      .then((next) => { setState(next); setError(null) })
      .catch((e: ApiError) => setError(e.message))
  }, [])

  useEffect(load, [load])

  // Preparing a voice is a queued job, so the page follows it the same way
  // the job page does and reloads once it is finished.
  const prepareState = jobId ? live.job?.state : undefined
  useEffect(() => {
    if (!jobId || !prepareState) return
    if (prepareState === 'done') {
      toast.show('Voice ready. Listen and pick the clip to use.', 'success')
      setJobId(null)
      load()
    } else if (prepareState === 'failed' || prepareState === 'cancelled') {
      toast.show(live.job?.detail || 'Preparing the voice did not finish.',
                 'error')
      setJobId(null)
    }
  }, [jobId, prepareState, live.job?.detail, load, toast])

  async function prepare() {
    if (!recording) return
    setBusy(true)
    try {
      const started = await api.prepareVoice(recording, name.trim())
      setJobId(started.id)
      setRecording(null)
      setName('')
      if (fileRef.current) fileRef.current.value = ''
    } catch (e) {
      toast.show((e as ApiError).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function activate(profile: string, candidate: string) {
    try {
      await api.activateVoice(profile, candidate)
      toast.show('Every new job will use this voice.', 'success')
      load()
    } catch (e) {
      toast.show((e as ApiError).message, 'error')
    }
  }

  async function rename(profile: string, next: string) {
    try {
      await api.renameVoice(profile, next)
      toast.show('Voice renamed.', 'success')
      load()
    } catch (e) {
      toast.show((e as ApiError).message, 'error')
    }
  }

  if (error) return <ErrorState message={error} onRetry={load} />

  const active = state?.active ?? null
  const preparing = jobId !== null
  const stage = live.job?.stage ?? ''

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold">Voice</h1>
        <p className="text-base text-muted">
          Every job clones the clip marked in use.
        </p>
      </header>

      <Panel title="In use">
        {!state ? (
          <SkeletonRows rows={2} />
        ) : (
          <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
            <div className="min-w-0 flex-1">
              <p className="text-base font-medium">
                {active ? `${active.profile} / ${active.candidate}`
                        : 'The reference clip from settings'}
              </p>
              <p className="tnum text-xs text-faint" title={state.fallback}>
                {active
                  ? `Chosen ${when(active.set_at)}`
                  /* The file name, not the whole path: a dashboard should
                     not put someone's home directory on screen. */
                  : fileName(state.fallback)}
              </p>
            </div>
            <audio
              controls
              preload="none"
              className="h-8 w-full sm:w-64"
              src={api.voiceAudioUrl(active?.reference ?? state.fallback)}
            />
          </div>
        )}
      </Panel>

      <Panel title="Add a voice">
        <div className="space-y-4 p-4">
          {preparing ? (
            <div className="space-y-2" data-testid="voice-preparing">
              <p className="text-base">
                {PREPARE_STAGES[stage] ?? live.job?.detail ?? 'Queued...'}
              </p>
              <p className="text-xs text-muted">
                Candidates are cut from the parts of the recording whose first
                six seconds are clean, continuous speech - that is the only
                part the voice model reads. Each one then clones the same test
                sentence so they can be compared.
              </p>
              <Skeleton className="h-2 w-full" />
            </div>
          ) : (
            <>
              <Field
                label="Recording"
                htmlFor="voice-file"
                hint="A minute or more of the client speaking normally, as clean as you have."
              >
                <input
                  id="voice-file"
                  ref={fileRef}
                  type="file"
                  accept="audio/*"
                  className={inputClass}
                  onChange={(e) => setRecording(e.target.files?.[0] ?? null)}
                />
              </Field>
              <Field label="Name" htmlFor="voice-name"
                     hint="Optional. Used to tell profiles apart later.">
                <input
                  id="voice-name"
                  className={inputClass}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Client name"
                />
              </Field>
              <Button variant="primary" onClick={prepare}
                      disabled={!recording || busy} data-testid="prepare-voice">
                Prepare candidates
              </Button>
            </>
          )}
        </div>
      </Panel>

      {!state ? (
        <SkeletonRows rows={4} />
      ) : state.profiles.length === 0 ? (
        <EmptyState
          title="No voices yet"
          body="Add a recording of the client and candidate clips will be cut and scored from it."
        />
      ) : (
        <div className="space-y-4">
          {state.profiles.map((profile) => (
            <ProfileCard
              key={profile.name}
              profile={profile}
              activeProfile={active?.profile ?? null}
              activeCandidate={active?.candidate ?? null}
              onUse={(candidate) => activate(profile.name, candidate)}
              onRename={(next) => rename(profile.name, next)}
              onDelete={() => setDeleting(profile.name)}
            />
          ))}
        </div>
      )}

      <ConfirmDialog
        open={deleting !== null}
        title={`Delete ${deleting}?`}
        body="The recording and every clip cut from it are removed. Jobs already made with this voice keep their audio, but it cannot be used again."
        confirmLabel="Delete voice"
        destructive
        onConfirm={() => {
          const target = deleting
          setDeleting(null)
          if (!target) return
          api.deleteVoice(target)
            .then(() => { toast.show('Voice deleted.', 'success'); load() })
            .catch((e: ApiError) => toast.show(e.message, 'error'))
        }}
        onCancel={() => setDeleting(null)}
      />
    </div>
  )
}
