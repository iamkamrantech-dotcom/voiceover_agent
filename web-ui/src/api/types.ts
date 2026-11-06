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
  version: number
}

export type StageState = 'waiting' | 'running' | 'done' | 'failed'

export interface StageRun {
  stage: string
  state: StageState
  seconds: number
}

export interface StageDef {
  key: string
  label: string
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
  kind: 'video' | 'revise' | 'voice'
  version: number
  cached_takes: number
  stages: StageRun[]
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

export type SettingType = 'str' | 'int' | 'float' | 'bool' | 'password'

export interface SettingItem {
  key: string
  label: string
  help: string
  type: SettingType
  value: string | number | boolean | null
  default: string | number | boolean
  differs: boolean
  choices: string[]
  minimum: number | null
  maximum: number | null
  unit: string
  /** False while a job is running, even for a setting that is editable. */
  writable: boolean
  /** Whether this setting can ever be changed from here. */
  editable: boolean
  restart_required: boolean
}

export interface SettingGroup {
  name: string
  help: string
  settings: SettingItem[]
}

export interface Settings {
  groups: SettingGroup[]
  locked: boolean
  locked_reason: string
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

export interface SubtitleCue {
  index: number
  start: number
  end: number
  text: string
}

export interface Timeline {
  source_duration: number
  output_duration: number
  total_extension: number
  fps: number
  blocks: TimelineBlock[]
  cues: SubtitleCue[]
}

export interface Take {
  seed: number
  number: number
  duration: number
  similarity: number | null
  words_ok: boolean
  transcript: string
  chosen: boolean
  playable: boolean
}

export interface BlockDetail {
  block: number
  text: string
  scene_ids: number[]
  start: number
  end: number
  silent: boolean
  duration: number
  similarity: number | null
  similarity_reliable: boolean
  words_ok: boolean
  transcript: string
  seed: number | null
  take_number: number
  takes: Take[]
}

export interface BlocksResponse {
  blocks: BlockDetail[]
  /** False for jobs made before every take was kept. */
  takes_recorded: boolean
}

export type ReviseAction = 'choose_take' | 'more_takes' | 'edit_text'

export interface ReviseRequest {
  block: number
  action: ReviseAction
  seed?: number
  extra?: number
  text?: string
}

export interface RevisionPlan {
  block: number
  action: ReviseAction
  note: string
  takes_to_make: number
  words: number
  estimate_seconds: number
  steps: string[]
  version: number
}

export interface JobVersion {
  version: number
  created: string
  note: string
  changes: Record<string, unknown>[]
  video: string | null
  subtitles: string | null
  timeline: string | null
  report: string | null
  waveform: string | null
  qc_passed: boolean | null
  duration: number
}

export interface QCReport {
  passed: boolean
  duration_expected: number
  duration_actual: number
  duration_ok: boolean
  loudness_i: number | null
  loudness_tp: number | null
  loudness_ok: boolean
  clipping: boolean
  peak_db: number | null
  drift_applicable: boolean
  drift_median: number | null
  drift_p95: number | null
  drift_max: number | null
  drift_ok: boolean
  matched_words: number
  total_words: number
  long_silences: { start: number; end: number; block: number }[]
  listen_to: { block: number; at: number | null; why: string }[]
  problems: string[]
}

export interface Waveform {
  version: number
  per_second: number
  duration: number
  /** Base64 of one byte per bucket: 0-255 peak amplitude. */
  peaks: string
}

export type LogLevel = 'debug' | 'progress' | 'warning' | 'error'

export interface LogLine {
  t: string
  level: LogLevel
  text: string
}

export interface SSEMessage extends QueueSnapshot {
  type: 'snapshot' | 'job' | 'log'
  job?: Job | null
  job_id?: string
  line?: LogLine
  log?: LogLine[]
}
