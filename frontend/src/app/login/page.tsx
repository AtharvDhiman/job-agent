'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'

import { ErrorNote } from '@/components/ui'
import { login, registerAccount } from '@/lib/api'

export default function LoginPage() {
  const router = useRouter()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mode === 'login') {
        await login(email, password)
      } else {
        await registerAccount(email, password, fullName)
      }
      router.push('/dashboard')
      router.refresh()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-md">
        <h1 className="text-xl font-semibold text-ink">Job Application Agent</h1>
        <p className="mt-1 text-sm text-ink-muted">
          {mode === 'login'
            ? 'Sign in to your local instance.'
            : 'The first account created becomes the owner.'}
        </p>

        <form onSubmit={submit} className="card mt-6 space-y-4">
          {mode === 'register' ? (
            <div>
              <label className="label" htmlFor="full_name">
                Full name
              </label>
              <input
                id="full_name"
                className="input"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                autoComplete="name"
              />
            </div>
          ) : null}

          <div>
            <label className="label" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              className="input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
            />
          </div>

          <div>
            <label className="label" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              minLength={mode === 'register' ? 12 : undefined}
              className="input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            />
            {mode === 'register' ? (
              <p className="mt-1 text-xs text-ink-muted">
                At least 12 characters, using three of: uppercase, lowercase, digit, symbol.
              </p>
            ) : null}
          </div>

          <ErrorNote message={error} />

          <button type="submit" disabled={busy} className="btn-primary w-full">
            {busy ? 'Working...' : mode === 'login' ? 'Sign in' : 'Create account'}
          </button>

          <button
            type="button"
            className="btn-ghost w-full"
            onClick={() => {
              setMode(mode === 'login' ? 'register' : 'login')
              setError(null)
            }}
          >
            {mode === 'login' ? 'Create the first account' : 'I already have an account'}
          </button>
        </form>
      </div>
    </div>
  )
}
