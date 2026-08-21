/**
 * The undo confirmation, and the conflict path it exists for (M7).
 *
 * The handoff is blunt about this one: *"a lead edited after the changeset is a
 * conflict. Report it and let the operator decide. Never silently clobber a
 * later edit."* This dialog is where that decision gets made, so it has to show
 * enough to make it with.
 *
 * For every conflicted lead it names the value three ways — what the batch set,
 * what the lead holds now, and what reverting would put back — because
 * "3 leads changed since" is not enough to choose on. The destructive option is
 * available, never preselected, and says exactly how many other people's edits
 * it would discard.
 */

import { useState } from 'react'

import type { LeadUndoPlan, Reversal, UndoPreview } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { toDisplayStringOr } from '@/lib/format'

export interface UndoDialogProps {
  readonly open: boolean
  readonly preview: UndoPreview | undefined
  readonly loading: boolean
  readonly pending: boolean
  readonly error: string | null
  readonly onClose: () => void
  readonly onConfirm: (skipConflicts: boolean) => void
}

function ReversalRow({ reversal }: { readonly reversal: Reversal }) {
  return (
    <li className="text-sm">
      <span className="font-medium">{reversal.label}</span>{' '}
      {reversal.conflicted ? (
        <>
          <span className="text-muted-foreground">is now</span>{' '}
          <span className="text-destructive">{toDisplayStringOr(reversal.current, 'empty')}</span>,{' '}
          <span className="text-muted-foreground">not the</span>{' '}
          <span>{toDisplayStringOr(reversal.expected, 'empty')}</span>{' '}
          <span className="text-muted-foreground">this edit set</span>
        </>
      ) : (
        <>
          <span className="text-muted-foreground">back to</span>{' '}
          <span>{toDisplayStringOr(reversal.revert_to, 'empty')}</span>
        </>
      )}
    </li>
  )
}

function ConflictedLead({ plan }: { readonly plan: LeadUndoPlan }) {
  return (
    <li className="rounded-md border p-2">
      <div className="flex items-center gap-2">
        <span className="font-medium">{plan.identity_value}</span>
        <Badge variant="outline" className="border-destructive text-destructive">
          changed since
        </Badge>
      </div>
      <ul className="mt-1 space-y-0.5 pl-1">
        {plan.reversals
          .filter((reversal) => reversal.conflicted)
          .map((reversal) => (
            <ReversalRow key={reversal.target} reversal={reversal} />
          ))}
      </ul>
    </li>
  )
}

export function UndoDialog({
  open,
  preview,
  loading,
  pending,
  error,
  onClose,
  onConfirm,
}: UndoDialogProps) {
  // Never preselected. Choosing to discard somebody else's edit has to be an
  // act, not a default the operator clicked past.
  const [skipConflicts, setSkipConflicts] = useState(false)

  const [lastOpen, setLastOpen] = useState(open)
  if (open !== lastOpen) {
    setLastOpen(open)
    if (open) setSkipConflicts(false)
  }

  const counts = preview?.counts
  const conflicted = (preview?.leads ?? []).filter((plan) => plan.outcome === 'CONFLICTED')
  const blocked = (counts?.conflicted ?? 0) > 0 && !skipConflicts
  const nothingToDo = (counts?.reversible ?? 0) === 0

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Undo this change"
      className="max-w-2xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={loading || pending || blocked || nothingToDo}
            onClick={() => onConfirm(skipConflicts)}
          >
            {skipConflicts && (counts?.conflicted ?? 0) > 0
              ? `Undo the other ${counts?.reversible ?? 0}`
              : `Undo ${counts?.reversible ?? 0} lead${counts?.reversible === 1 ? '' : 's'}`}
          </Button>
        </>
      }
    >
      {loading ? <p className="text-muted-foreground text-sm">Checking…</p> : null}

      {error ? (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      ) : null}

      {preview ? (
        <div className="space-y-3">
          <p className="text-sm">
            <span className="text-muted-foreground">Reversing</span>{' '}
            <span className="font-medium">{preview.summary}</span>
          </p>

          {preview.is_undone ? (
            <p className="text-muted-foreground text-sm">This change has already been undone.</p>
          ) : null}

          <dl className="flex flex-wrap gap-4 text-sm">
            <div>
              <dt className="text-muted-foreground text-xs uppercase">Will revert</dt>
              <dd className="font-medium">{counts?.reversible ?? 0}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs uppercase">Changed since</dt>
              <dd className="font-medium">{counts?.conflicted ?? 0}</dd>
            </div>
            {(counts?.deleted ?? 0) > 0 ? (
              <div>
                <dt className="text-muted-foreground text-xs uppercase">Deleted</dt>
                <dd className="font-medium">{counts?.deleted}</dd>
              </div>
            ) : null}
          </dl>

          {conflicted.length > 0 ? (
            <div className="space-y-2">
              <p className="text-sm">
                {conflicted.length === 1
                  ? 'One lead has been edited since this change.'
                  : `${conflicted.length} leads have been edited since this change.`}{' '}
                <span className="text-muted-foreground">Undoing would discard those edits.</span>
              </p>

              <ul className="max-h-64 space-y-2 overflow-y-auto">
                {conflicted.slice(0, 25).map((plan) => (
                  <ConflictedLead key={plan.lead_id} plan={plan} />
                ))}
              </ul>
              {conflicted.length > 25 ? (
                <p className="text-muted-foreground text-xs">and {conflicted.length - 25} more</p>
              ) : null}

              <label className="flex items-start gap-2 rounded-md border p-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={skipConflicts}
                  onChange={(event) => setSkipConflicts(event.target.checked)}
                  aria-label="Skip the leads that changed"
                />
                <span>
                  Leave those {conflicted.length} alone and undo the rest.
                  <span className="text-muted-foreground block text-xs">
                    Their later edits are kept.
                  </span>
                </span>
              </label>
            </div>
          ) : null}

          {nothingToDo && !loading ? (
            <p className="text-muted-foreground text-sm">
              There is nothing left to revert in this change.
            </p>
          ) : null}
        </div>
      ) : null}
    </Dialog>
  )
}
