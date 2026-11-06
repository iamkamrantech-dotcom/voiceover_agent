import type {
  BlocksResponse,
  FitResponse,
  FitRow,
  Job,
  JobVersion,
  JobsResponse,
  LogLine,
  MachineInfo,
  QCReport,
  RevisionPlan,
  ReviseRequest,
  Settings,
  StageDef,
  Stats,
  Storyboard,
  Timeline,
  UploadResult,
  VoiceProfile,
  VoiceState,
  Waveform,
} from './types'

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw new ApiError('Cannot reach the server. Is it still running?', 0)
  }
  if (!response.ok) {
    let detail = response.statusText || 'Request failed'
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      /* the body was not JSON; the status text will do */
    }
    throw new ApiError(detail, response.status)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/** Version 1 is the plain path, so links to it stay what they always were. */
function versionQuery(version?: number): string {
  return version && version > 1 ? `?v=${version}` : ''
}

function json(body: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export const api = {
  session: () =>
    request<{ required: boolean; signed_in: boolean }>('/api/session'),

  signIn: (password: string) => {
    const form = new FormData()
    form.append('password', password)
    return request<{ ok: boolean }>('/api/login', { method: 'POST', body: form })
  },

  stats: () => request<Stats>('/api/stats'),
  machine: () => request<MachineInfo>('/api/machine'),

  upload: (files: { video?: File; script?: File; music?: File }) => {
    const form = new FormData()
    if (files.video) form.append('video', files.video)
    if (files.script) form.append('script', files.script)
    if (files.music) form.append('music', files.music)
    return request<UploadResult>('/api/upload', { method: 'POST', body: form })
  },

  storyboard: (video: string, scriptText = '') => {
    const form = new FormData()
    form.append('video', video)
    form.append('script_text', scriptText)
    return request<Storyboard>('/api/storyboard', {
      method: 'POST',
      body: form,
    })
  },

  checkFit: (
    video: string,
    blocks: { scene: number; text: string }[],
    draft: boolean,
    signal?: AbortSignal,
  ) =>
    request<FitResponse>('/api/storyboard/check', {
      ...json({ video, blocks, draft }),
      signal: signal ?? null,
    }),

  jobs: () => request<JobsResponse>('/api/jobs'),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  timeline: (id: string, version?: number) =>
    request<Timeline>(`/api/jobs/${id}/timeline${versionQuery(version)}`),
  qc: (id: string, version?: number) =>
    request<QCReport>(`/api/jobs/${id}/qc${versionQuery(version)}`),
  waveform: (id: string, version?: number) =>
    request<Waveform>(`/api/jobs/${id}/waveform${versionQuery(version)}`),
  blocks: (id: string) => request<BlocksResponse>(`/api/jobs/${id}/blocks`),
  jobFit: (
    id: string,
    blocks: { block: number; text: string }[],
    signal?: AbortSignal,
  ) =>
    request<{ rows: FitRow[] }>(`/api/jobs/${id}/fit`, {
      ...json({ blocks }),
      signal: signal ?? null,
    }),
  jobLog: (id: string) =>
    request<{ lines: LogLine[]; live: LogLine[] }>(`/api/jobs/${id}/log`),
  versions: (id: string) =>
    request<{ versions: JobVersion[] }>(`/api/jobs/${id}/versions`),
  deleteVersion: (id: string, version: number) =>
    request<{ ok: boolean; message: string }>(
      `/api/jobs/${id}/versions/${version}`,
      { method: 'DELETE' },
    ),
  config: () => request<{ stages: StageDef[]; engine: string }>('/api/config'),

  planRevision: (id: string, body: ReviseRequest) =>
    request<RevisionPlan>(`/api/jobs/${id}/revise/plan`, json(body)),
  revise: (id: string, body: ReviseRequest) =>
    request<{ ok: boolean; id: string }>(`/api/jobs/${id}/revise`, json(body)),

  takeUrl: (id: string, block: number, seed: number) =>
    `/api/jobs/${id}/take?block=${block}&seed=${seed}`,
  blockAudioUrl: (id: string, block: number) =>
    `/api/jobs/${id}/block-audio?block=${block}`,
  keyframeUrl: (id: string, scene: number) =>
    `/api/jobs/${id}/keyframe/${scene}`,

  createJob: (body: {
    video: string
    blocks?: { scene: number; text: string }[]
    script?: string
    music?: string | null
    draft: boolean
    keep_original_audio: boolean
    burn_subs: boolean
    engine?: string
    words?: number
    total_takes?: number
    estimate_seconds?: number
  }) => request<{ id: string }>('/api/jobs', json(body)),

  cancelJob: (id: string) =>
    request<{ ok: boolean }>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  resumeJob: (id: string) =>
    request<{ ok: boolean }>(`/api/jobs/${id}/resume`, { method: 'POST' }),
  duplicateJob: (id: string) =>
    request<{ id: string }>(`/api/jobs/${id}/duplicate`, { method: 'POST' }),
  deleteJob: (id: string) =>
    request<{ ok: boolean }>(`/api/jobs/${id}`, { method: 'DELETE' }),

  fileUrl: (
    id: string,
    kind: 'video' | 'subtitles' | 'report' | 'timeline',
    version?: number,
  ) => `/api/jobs/${id}/file/${kind}${versionQuery(version)}`,

  voice: () => request<VoiceState>('/api/voice'),
  voiceAudioUrl: (path: string) =>
    `/api/voice/audio?path=${encodeURIComponent(path)}`,
  /** Queues the work and returns the job that will do it. */
  prepareVoice: (recording: File, name: string) => {
    const form = new FormData()
    form.append('recording', recording)
    form.append('name', name)
    return request<{ id: string; name: string }>('/api/voice/prepare', {
      method: 'POST',
      body: form,
    })
  },
  renameVoice: (profile: string, name: string) =>
    request<VoiceProfile>(`/api/voice/${encodeURIComponent(profile)}`, {
      ...json({ name }),
      method: 'PATCH',
    }),
  deleteVoice: (profile: string) =>
    request<{ ok: boolean }>(`/api/voice/${encodeURIComponent(profile)}`, {
      method: 'DELETE',
    }),
  activateVoice: (profile: string, candidate: string) => {
    const form = new FormData()
    form.append('profile', profile)
    form.append('candidate', candidate)
    return request<unknown>('/api/voice/activate', {
      method: 'POST',
      body: form,
    })
  },

  settings: () => request<Settings>('/api/settings'),
  saveSettings: (
    values: Record<string, string | number | boolean>,
    reset: string[] = [],
  ) =>
    request<Settings>('/api/settings', {
      ...json({ values, reset }),
      method: 'PUT',
    }),
}
