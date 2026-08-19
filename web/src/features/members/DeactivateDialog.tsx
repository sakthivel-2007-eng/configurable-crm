import { useState } from 'react'

import { ApiError } from '@/api/client'
import type { MemberDetail } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { useDeactivateMember } from '@/features/members/api'

/**
 * Deactivation, including the reassignment step.
 *
 * The server decides whether a target is required — it is the only party that
 * knows how much open pipeline the member holds. So this dialog submits
 * without one first, and only asks for a target once the API answers
 * `409 reassignment_required` with the count. That way the common case (a
 * member holding nothing) is one click, and the case that matters cannot be
 * skipped.
 */
interface Props {
  readonly workspaceId: string
  readonly member: MemberDetail | null
  readonly candidates: readonly MemberDetail[]
  readonly onClose: () => void
}

export function DeactivateDialog({ workspaceId, member, candidates, onClose }: Props) {
  const deactivate = useDeactivateMember(workspaceId)
  const [reassignTo, setReassignTo] = useState('')
  const [openLeadCount, setOpenLeadCount] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const targets = candidates.filter((c) => c.id !== member?.id && c.is_active)

  function reset() {
    setReassignTo('')
    setOpenLeadCount(null)
    setError(null)
    onClose()
  }

  async function submit() {
    if (!member) return
    setError(null)

    try {
      await deactivate.mutateAsync({
        membershipId: member.id,
        reassignTo: reassignTo || null,
      })
      reset()
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'reassignment_required') {
        const count = cause.detail.open_lead_count
        setOpenLeadCount(typeof count === 'number' ? count : 0)
        return
      }
      setError(cause instanceof ApiError ? cause.message : 'Could not deactivate this member.')
    }
  }

  return (
    <Dialog
      open={member !== null}
      onClose={reset}
      title={`Deactivate ${member?.user.full_name ?? ''}`}
      description="They lose their licensed seat and can no longer sign in."
      footer={
        <>
          <Button variant="ghost" onClick={reset}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => void submit()}
            disabled={deactivate.isPending || (openLeadCount !== null && !reassignTo)}
          >
            {deactivate.isPending ? 'Deactivating…' : 'Deactivate'}
          </Button>
        </>
      }
    >
      {openLeadCount !== null ? (
        <div className="flex flex-col gap-2">
          <p className="text-sm">
            This member holds <strong>{openLeadCount}</strong> open leads. Choose who takes them
            over.
          </p>
          <Label htmlFor="reassign-to">Reassign leads to</Label>
          <Select
            id="reassign-to"
            value={reassignTo}
            onChange={(event) => setReassignTo(event.target.value)}
          >
            <option value="">Select a member…</option>
            {targets.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.user.full_name} ({candidate.template_name})
              </option>
            ))}
          </Select>
        </div>
      ) : (
        <p className="text-muted-foreground text-sm">
          Any open leads they hold must be reassigned first. You will be asked to choose someone if
          there are any.
        </p>
      )}

      {error ? (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      ) : null}
    </Dialog>
  )
}
