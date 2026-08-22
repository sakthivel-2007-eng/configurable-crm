/**
 * Setting a password from an emailed link (M11).
 *
 * Where every account in this product begins. The workspace model is
 * invite-only, so this page is the front door — there is no registration form
 * anywhere else.
 *
 * It serves two arrivals that are the same act: an invitation, and a forgotten
 * password. The copy adapts; the mechanics do not.
 *
 * **The token stays in the URL and never in application state beyond this
 * page.** It is a bearer credential for the account, so it is read once, spent,
 * and the person is sent to sign in normally rather than being silently logged
 * in — which would mean a forwarded email logs somebody else in.
 */

import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label as FieldLabel } from '@/components/ui/label'

/** Matches the server's `MIN_PASSWORD_LENGTH`. */
const MIN_LENGTH = 12

function message(cause: unknown): string {
  if (!(cause instanceof ApiError)) return 'That did not work. Try again.'
  if (cause.code === 'invalid_token') return cause.message
  return cause.message
}

export function SetPasswordPage() {
  const [params] = useSearchParams()
  const token = params.get('token')

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [pending, setPending] = useState(false)

  const tooShort = password.length > 0 && password.length < MIN_LENGTH
  const mismatch = confirm.length > 0 && confirm !== password
  const ready = password.length >= MIN_LENGTH && confirm === password && !pending

  if (!token) {
    return (
      <Shell title="That link is incomplete">
        <p className="text-sm">
          The link is missing its token. Ask for a new one from the sign-in page.
        </p>
        <Link className="text-sm underline" to="/login">
          Back to sign in
        </Link>
      </Shell>
    )
  }

  if (done) {
    return (
      <Shell title="Password set">
        {/* Deliberately not signed in automatically: a forwarded email would
            otherwise log the wrong person in. */}
        <p className="text-sm">Sign in with it now.</p>
        <Link className="text-sm underline" to="/login">
          Go to sign in
        </Link>
      </Shell>
    )
  }

  return (
    <Shell title="Choose a password">
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          void (async () => {
            setError(null)
            setPending(true)
            try {
              await api.post('/auth/password-reset/confirm', {
                token,
                new_password: password,
              })
              setDone(true)
            } catch (cause) {
              setError(message(cause))
            } finally {
              setPending(false)
            }
          })()
        }}
      >
        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : null}

        <div className="space-y-1.5">
          <FieldLabel htmlFor="new-password">New password</FieldLabel>
          <Input
            id="new-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <p className="text-muted-foreground text-xs">At least {MIN_LENGTH} characters.</p>
        </div>

        <div className="space-y-1.5">
          <FieldLabel htmlFor="confirm-password">Confirm</FieldLabel>
          <Input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
          {/* Said as it is typed rather than on submit — finding out after a
              round trip that two fields disagree is a needless trip. */}
          {tooShort ? (
            <p className="text-muted-foreground text-xs">
              {MIN_LENGTH - password.length} more to go.
            </p>
          ) : null}
          {mismatch ? <p className="text-destructive text-xs">Those do not match.</p> : null}
        </div>

        <Button type="submit" className="w-full" disabled={!ready}>
          {pending ? 'Setting…' : 'Set password'}
        </Button>
      </form>
    </Shell>
  )
}

function Shell({
  title,
  children,
}: {
  readonly title: string
  readonly children: React.ReactNode
}) {
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-base">{title}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">{children}</CardContent>
      </Card>
    </div>
  )
}
