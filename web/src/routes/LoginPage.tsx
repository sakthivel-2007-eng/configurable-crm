import { useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuth } from '@/features/auth/context'

/**
 * Messages for the codes login can answer with.
 *
 * The API deliberately gives the same reply for a wrong password and an
 * unknown address; the copy here must not undo that by being more specific
 * than the server was.
 */
const MESSAGES: Record<string, string> = {
  invalid_credentials: 'Email or password is incorrect.',
  no_license: 'Your account has no licensed seat. Ask a workspace administrator to assign you one.',
  member_inactive:
    'This account has been deactivated. Ask a workspace administrator to restore it.',
  rate_limited: 'Too many attempts. Wait a few minutes and try again.',
  network_error: 'Could not reach the server. Check your connection and try again.',
}

export function LoginPage() {
  const { login, status } = useAuth()
  const navigate = useNavigate()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (status === 'authenticated') {
    return <Navigate to="/" replace />
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email, password)
      void navigate('/', { replace: true })
    } catch (cause) {
      const code = cause instanceof ApiError ? cause.code : 'unknown_error'
      setError(MESSAGES[code] ?? 'Sign in failed. Try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="bg-muted/30 flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Sign in</CardTitle>
          <CardDescription>Access your workspace</CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="flex flex-col gap-4"
            onSubmit={(event) => void handleSubmit(event)}
            noValidate
          >
            <div className="flex flex-col gap-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                name="email"
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>

            {error ? (
              <p role="alert" className="text-destructive text-sm">
                {error}
              </p>
            ) : null}

            <Button type="submit" disabled={submitting}>
              {submitting ? 'Signing in…' : 'Sign in'}
            </Button>

            {/* Without this the reset flow exists and nobody can reach it —
                and since the workspace model is invite-only, an invitee whose
                link expired has no other way back in. */}
            <Link
              to="/forgot-password"
              className="text-muted-foreground text-center text-xs underline"
            >
              Forgotten your password?
            </Link>
          </form>
        </CardContent>
      </Card>
    </main>
  )
}
