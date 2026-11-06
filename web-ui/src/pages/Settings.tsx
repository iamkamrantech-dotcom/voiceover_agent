import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useLive } from '../api/useLive'
import type { SettingItem, Settings as SettingsData } from '../api/types'
import { useToast } from '../components/Toast'
import {
  Button, Checkbox, ErrorState, Panel, SkeletonRows, inputClass,
} from '../components/ui'

/**
 * Every setting, grouped, with what it does and what it was.
 *
 * The explanations and defaults come from the server, which reads them
 * from one table beside the pipeline - so a setting cannot be described
 * here in a way that disagrees with what the code does with it.
 *
 * Settings lock while a job is running. A job reads them as it goes, so
 * changing one underneath it would make a video built half one way and
 * half another.
 */

type Draft = Record<string, string | number | boolean>

function displayValue(item: SettingItem): string | number | boolean {
  if (item.type === 'password') return ''
  return (item.value ?? '') as string | number | boolean
}

function Row({
  item,
  draft,
  onChange,
  onReset,
}: {
  item: SettingItem
  draft: Draft
  onChange: (key: string, value: string | number | boolean) => void
  onReset: (key: string) => void
}) {
  const id = `set-${item.key}`
  const value = item.key in draft ? draft[item.key]! : displayValue(item)
  const changed = item.key in draft
  const disabled = !item.writable

  return (
    <div className="flex flex-col gap-2 border-b border-border px-4 py-3 last:border-b-0 sm:flex-row sm:items-start sm:gap-4">
      <div className="min-w-0 sm:w-[46%]">
        <label htmlFor={id} className="flex flex-wrap items-center gap-2 text-base">
          {item.label}
          {item.differs && !changed && (
            <span className="rounded-sm bg-accent-wash px-1.5 py-px text-2xs text-accent">
              changed
            </span>
          )}
          {changed && (
            <span className="rounded-sm bg-warning-wash px-1.5 py-px text-2xs text-warning">
              unsaved
            </span>
          )}
          {item.restart_required && (
            <span className="text-2xs text-faint">needs a restart</span>
          )}
        </label>
        <p className="mt-0.5 text-xs text-muted">{item.help}</p>
      </div>

      <div className="flex min-w-0 flex-1 items-center gap-2">
        {item.type === 'bool' ? (
          <Checkbox
            id={id}
            label={Boolean(value) ? 'On' : 'Off'}
            checked={Boolean(value)}
            disabled={disabled}
            onChange={(next) => onChange(item.key, next)}
          />
        ) : item.choices.length > 0 ? (
          <select
            id={id}
            className={inputClass}
            value={String(value)}
            disabled={disabled}
            onChange={(e) => onChange(item.key, e.target.value)}
          >
            {item.choices.map((choice) => (
              <option key={choice} value={choice}>{choice}</option>
            ))}
          </select>
        ) : item.type === 'password' ? (
          <input
            id={id}
            type="password"
            className={inputClass}
            value={String(value)}
            disabled={disabled}
            autoComplete="new-password"
            placeholder={item.value ? 'A password is set' : 'No password'}
            onChange={(e) => onChange(item.key, e.target.value)}
          />
        ) : item.type === 'str' ? (
          <input
            id={id}
            className={`${inputClass} ${disabled ? 'text-muted' : ''}`}
            value={String(value)}
            disabled={disabled}
            onChange={(e) => onChange(item.key, e.target.value)}
          />
        ) : (
          <input
            id={id}
            type="number"
            step={item.type === 'int' ? 1 : 'any'}
            min={item.minimum ?? undefined}
            max={item.maximum ?? undefined}
            className={`${inputClass} tnum`}
            value={String(value)}
            disabled={disabled}
            onChange={(e) => onChange(item.key, e.target.value)}
          />
        )}
        {item.unit && (
          <span className="shrink-0 text-xs text-faint">{item.unit}</span>
        )}
      </div>

      <div className="flex w-full shrink-0 items-center justify-between gap-2 sm:w-[168px] sm:justify-end">
        <span className="tnum truncate whitespace-nowrap text-xs text-faint"
              title={`Default: ${String(item.default)}`}>
          {item.type === 'password'
            ? (item.value ? 'set' : 'not set')
            : `was ${String(item.default)}`}
        </span>
        {item.editable && (item.differs || changed) && (
          <Button
            size="sm"
            variant="ghost"
            disabled={disabled}
            onClick={() => onReset(item.key)}
            aria-label={`Reset ${item.label}`}
            data-testid={`reset-${item.key}`}
          >
            Reset
          </Button>
        )}
      </div>
    </div>
  )
}

export default function Settings() {
  const [data, setData] = useState<SettingsData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft>({})
  const [resets, setResets] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const toast = useToast()
  const live = useLive()

  const load = useCallback(() => {
    api
      .settings()
      .then((next) => { setData(next); setError(null) })
      .catch((e: ApiError) => setError(e.message))
  }, [])

  useEffect(load, [load])

  // The lock is decided on the server when the page loads, so without this
  // a page opened during a job stays locked after the job ends and the only
  // way out is a reload nobody is told to do. The queue is already on the
  // stream; reload the settings the moment it empties or fills.
  const busy = Boolean(live.queue.running) || live.queue.queued > 0
  const wasBusy = useRef(busy)
  useEffect(() => {
    if (wasBusy.current !== busy) {
      wasBusy.current = busy
      load()
    }
  }, [busy, load])

  function change(key: string, value: string | number | boolean) {
    setDraft((previous) => ({ ...previous, [key]: value }))
    setResets((previous) => previous.filter((k) => k !== key))
  }

  function reset(key: string) {
    setDraft((previous) => {
      const next = { ...previous }
      delete next[key]
      return next
    })
    setResets((previous) =>
      previous.includes(key) ? previous : [...previous, key])
  }

  const dirty = Object.keys(draft).length > 0 || resets.length > 0

  async function save() {
    setSaving(true)
    try {
      const next = await api.saveSettings(draft, resets)
      setData(next)
      setDraft({})
      setResets([])
      toast.show('Settings saved.', 'success')
    } catch (e) {
      toast.show((e as ApiError).message, 'error')
    } finally {
      setSaving(false)
    }
  }

  if (error) return <ErrorState message={error} onRetry={load} />

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Settings</h1>
          <p className="text-base text-muted">
            Written to .env, and used by the next job that starts.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {dirty && (
            <Button onClick={() => { setDraft({}); setResets([]) }}>
              Discard
            </Button>
          )}
          <Button
            variant="primary"
            onClick={save}
            disabled={!dirty || saving || Boolean(data?.locked)}
            data-testid="save-settings"
          >
            {saving ? 'Saving…' : 'Save changes'}
          </Button>
        </div>
      </header>

      {data?.locked && (
        <p
          className="rounded-md border border-border px-3 py-2 text-base"
          style={{ background: 'var(--warning-wash)', color: 'var(--warning)' }}
          data-testid="settings-locked"
        >
          {data.locked_reason}
        </p>
      )}

      {resets.length > 0 && (
        <p className="text-xs text-muted">
          {resets.length} setting{resets.length === 1 ? '' : 's'} will go back
          to the default when you save.
        </p>
      )}

      {!data ? (
        <SkeletonRows rows={8} />
      ) : (
        data.groups.map((group) => (
          <Panel key={group.name} title={group.name}>
            <p className="border-b border-border px-4 py-2 text-xs text-muted">
              {group.help}
            </p>
            {group.settings.map((item) => (
              <Row
                key={item.key}
                item={resets.includes(item.key)
                  ? { ...item, value: item.default, differs: false }
                  : item}
                draft={draft}
                onChange={change}
                onReset={reset}
              />
            ))}
          </Panel>
        ))
      )}
    </div>
  )
}
