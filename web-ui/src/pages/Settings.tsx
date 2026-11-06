import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { Settings as SettingsData } from '../api/types'
import { useToast } from '../components/Toast'
import {
  Button,
  Checkbox,
  ErrorState,
  Field,
  Panel,
  Skeleton,
  inputClass,
} from '../components/ui'

const LABELS: Record<string, { label: string; hint: string }> = {
  TTS_ENGINE: {
    label: 'Voice engine',
    hint: 'standard sounds closest to the client. turbo is faster but less accurate. mock is a tone generator for testing.',
  },
  TTS_BEST_OF_N: {
    label: 'Takes per block (final)',
    hint: 'More takes means a better match and proportionally more time.',
  },
  TTS_BEST_OF_N_DRAFT: {
    label: 'Takes per block (draft)',
    hint: 'Used when Draft quality is selected.',
  },
  MUSIC_DB: {
    label: 'Music level (dB)',
    hint: 'Background music is ducked under the narration.',
  },
  HOLD_MOTION: {
    label: 'Motion on held frames',
    hint: 'zoom adds a slow push-in when an image is frozen. none holds it still.',
  },
  MIN_SIMILARITY: {
    label: 'Minimum voice match (%)',
    hint: 'Blocks below this are flagged for you to listen to.',
  },
  NARRATION_WPS: {
    label: 'Speaking pace (words per second)',
    hint: 'Used to estimate whether a script fits before any audio is made.',
  },
  PREVIEW_SUBTITLES: {
    label: 'Subtitles on previews',
    hint: 'Burned into draft renders so sync can be checked by eye.',
  },
}

const CHOICES: Record<string, string[]> = {
  TTS_ENGINE: ['standard', 'turbo', 'mock'],
  HOLD_MOTION: ['zoom', 'none'],
}

export default function Settings() {
  const [data, setData] = useState<SettingsData | null>(null)
  const [values, setValues] = useState<Record<string, string | number | boolean>>({})
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const toast = useToast()

  const load = useCallback(() => {
    setError(null)
    api
      .settings()
      .then((result) => {
        setData(result)
        setValues(result.writable)
      })
      .catch((e: ApiError) => setError(e.message))
  }, [])

  useEffect(load, [load])

  async function save() {
    setSaving(true)
    try {
      const result = await api.saveSettings(values)
      setData(result)
      setValues(result.writable)
      toast.show('Settings saved', 'success')
    } catch (e) {
      toast.show((e as ApiError).message, 'error')
    } finally {
      setSaving(false)
    }
  }

  if (error) return <ErrorState message={error} onRetry={load} />

  const dirty =
    data !== null &&
    JSON.stringify(values) !== JSON.stringify(data.writable)

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Settings</h1>
        <Button variant="primary" onClick={save} disabled={!dirty || saving}>
          {saving ? 'Saving…' : 'Save changes'}
        </Button>
      </div>

      <Panel title="Generation">
        {data === null ? (
          <div className="space-y-3 p-4">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : (
          <div className="grid gap-4 p-4 sm:grid-cols-2">
            {Object.entries(values).map(([key, value]) => {
              const meta = LABELS[key] ?? { label: key, hint: '' }
              if (typeof value === 'boolean') {
                return (
                  <div key={key} className="sm:col-span-2">
                    <Checkbox
                      id={key}
                      label={meta.label}
                      description={meta.hint}
                      checked={value}
                      onChange={(next) =>
                        setValues((v) => ({ ...v, [key]: next }))
                      }
                    />
                  </div>
                )
              }
              const options = CHOICES[key]
              return (
                <Field
                  key={key}
                  label={meta.label}
                  hint={meta.hint}
                  htmlFor={key}
                >
                  {options ? (
                    <select
                      id={key}
                      className={inputClass}
                      value={String(value)}
                      onChange={(e) =>
                        setValues((v) => ({ ...v, [key]: e.target.value }))
                      }
                    >
                      {options.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      id={key}
                      type="number"
                      step="any"
                      className={`${inputClass} tnum`}
                      value={String(value)}
                      onChange={(e) =>
                        setValues((v) => ({ ...v, [key]: e.target.value }))
                      }
                    />
                  )}
                </Field>
              )
            })}
          </div>
        )}
      </Panel>

      <Panel title="Fixed for this install">
        {data === null ? (
          <div className="p-4">
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <dl className="divide-y divide-border text-base">
            {Object.entries(data.readonly).map(([key, value]) => (
              <div
                key={key}
                className="flex items-center justify-between gap-4 px-4 py-2"
              >
                <dt className="text-muted">{key.replace(/_/g, ' ')}</dt>
                <dd className="tnum min-w-0 truncate text-right">
                  {String(value)}
                </dd>
              </div>
            ))}
          </dl>
        )}
        <p className="border-t border-border px-4 py-3 text-xs text-faint">
          These come from .env and are not editable here, because changing
          them mid-run would affect a job already in the queue.
        </p>
      </Panel>
    </div>
  )
}
