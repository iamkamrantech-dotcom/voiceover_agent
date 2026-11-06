// Types mirroring the FastAPI responses. Hand-written on purpose: the
// surface is small, and a generated client would hide how little of it
// the dashboard actually depends on.

export type JobState =
  | 'queued'
  | 'running'
  | 'done'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export interface JobResultFiles {
  video: string | null
  subtitles: string | null
  timeline: string | null
  report: string | null
  qc_passed: boolean | null
  listen_to: { block: number; at: number | null; why: string }[]
  warnings: string[]
}

export interface Job {
  id: string
  name: string
  state: JobState
  stage: string
  detail: string
  created: string
  started: string
  finished: string
  position: number
  block: number
  blocks: number
  take: number
  takes: number
  done_takes: number
  total_takes: number
  estimate_seconds: number
  elapsed: number
  remaining: number
  error: string
  params: Record<string, unknown>
  result: Partial<JobResultFiles>
}

export interface QueueSnapshot {
  running: string | null
  queued: number
  queue: string[]
}

export interface JobsResponse extends QueueSnapshot {
  jobs: Job[]
}

export interface Stats {
  videos: number
  minutes: number
  avg_similarity: number | null
  avg_drift: number | null
  qc_passed: number
  qc_failed: number
}

export interface MachineInfo {
  device: string
  accelerator: string
  has_gpu: boolean
  cpu_count: number | null
  threads: number
  ram_total_gb: number
  ram_free_gb: number
  disk_free_gb: number | null
  ffmpeg: string | null
  python: string
  engine: string
  models: { name: string; gb: number }[]
}

export interface VideoMeta {
  width?: number
  height?: number
  fps?: number
  duration?: number
}

export interface UploadResult {
  id: string
  video?: string
  name?: string
  meta?: VideoMeta
  script?: string
  script_text?: string
  music?: string
  music_name?: string
}

export interface StoryboardScene {
  index: number
  start: number
  end: number
  duration: number
  hold_end: number
  transition_in: string
  transition_out: string
  keyframe: string
  text: string
}

export interface Storyboard {
  video: string
  method: string
  warnings: string[]
  duration: number
  fps: number
  scenes: StoryboardScene[]
}

export type FitState = 'fits' | 'tight' | 'over' | 'empty'

export interface FitRow {
  scene: number
  words: number
  estimated: number
  available: number
  extension: number
  cut_words: number
  state: FitState
}

export interface FitResponse {
  rows: FitRow[]
  words: number
  takes: number
  total_takes: number
  estimate_seconds: number
}

export interface VoiceCandidate {
  name: string
  path: string
  start: number
  end: number
  score: number
  similarity: number | null
  sample: string | null
}

export interface VoiceProfile {
  name: string
  created: string
  source: string
  candidates: VoiceCandidate[]
  best: string | null
}

export interface VoiceState {
  active: {
    profile: string
    candidate: string
    reference: string
    source: string | null
    set_at: string
  } | null
  fallback: string
  profiles: VoiceProfile[]
}

export interface Settings {
  writable: Record<string, string | number | boolean>
  readonly: Record<string, string | number>
}

export interface TimelineBlock {
  block_index: number
  silent: boolean
  out_start: number
  out_end: number
  voice_start: number
  voice_end: number
  extension: number
  freeze_seconds: number
  stretch: number
  video_slowdown: number
  scene_ids: number[]
}

export interface Timeline {
  source_duration: number
  output_duration: number
  total_extension: number
  fps: number
  blocks: TimelineBlock[]
}

export interface SSEMessage extends QueueSnapshot {
  type: 'snapshot' | 'job'
  job?: Job | null
}
