import { useState } from 'react'
import { api, ApiError } from '../api/client'
import { Button, Field, Panel, inputClass } from '../components/ui'

/** Shown only when APP_PASSWORD is set in .env. */
export default function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.signIn(password)
      onSignedIn()
    } catch (e) {
      setError((e as ApiError).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <h1 className="mb-4 text-lg font-semibold">Voiceover Agent</h1>
        <Panel>
          <form onSubmit={submit} className="space-y-4 p-4">
            <Field label="Password" htmlFor="password">
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                autoFocus
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={inputClass}
              />
            </Field>
            {error && <p className="text-base text-danger">{error}</p>}
            <Button type="submit" variant="primary" disabled={busy}>
              {busy ? 'Checking…' : 'Sign in'}
            </Button>
          </form>
        </Panel>
      </div>
    </div>
  )
}
