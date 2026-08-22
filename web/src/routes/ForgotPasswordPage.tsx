/**
 * Asking for a set-password link (M11).
 *
 * The confirmation is deliberately the same whether or not the address has an
 * account — the server answers identically, and a page that said "no such user"
 * would hand back exactly what the server refuses to.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label as FieldLabel } from '@/components/ui/label'

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [pending, setPending] = useState(false)

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-base">
            {sent ? 'Check your email' : 'Reset your password'}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {sent ? (
            <>
              {/* Says nothing about whether the address exists — the server
                  does not either, and undoing that here would make the page a
                  membership oracle. */}
              <p className="text-sm">
                If that address has an account, a link is on its way. It works once and expires in
                an hour.
              </p>
              <Link className="text-sm underline" to="/login">
                Back to sign in
              </Link>
            </>
          ) : (
            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault()
                void (async () => {
                  setPending(true)
                  try {
                    await api.post('/auth/password-reset/request', { email })
                  } finally {
                    // Shown regardless, including on failure: a different
                    // outcome for a failed send would leak the same fact.
                    setSent(true)
                    setPending(false)
                  }
                })()
              }}
            >
              <div className="space-y-1.5">
                <FieldLabel htmlFor="reset-email">Email</FieldLabel>
                <Input
                  id="reset-email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              </div>
              <Button type="submit" className="w-full" disabled={!email || pending}>
                {pending ? 'Sending…' : 'Send a link'}
              </Button>
              <Link
                className="text-muted-foreground block text-center text-xs underline"
                to="/login"
              >
                Back to sign in
              </Link>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
