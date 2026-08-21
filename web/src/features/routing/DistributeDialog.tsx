/**
 * Redistributing a selected set of leads (M8).
 *
 * The whole run is one changeset, so it undoes from the edit report in a single
 * click — which is what makes it safe enough to offer on a 500-lead selection
 * at all. The dialog says so where the decision is made rather than leaving the
 * operator to hope.
 *
 * Deliberately narrower than an assignment rule: no conditions, because the
 * selection *is* the condition. Somebody has already chosen these leads.
 */

import { useState } from 'react'

import type { AssignmentStrategy, MemberDetail, SalesGroup } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Label as FieldLabel } from '@/components/ui/label'
import { Select } from '@/components/ui/select'

export interface DistributeDialogProps {
  readonly open: boolean
  readonly count: number
  readonly members: readonly MemberDetail[]
  readonly groups: readonly SalesGroup[]
  readonly pending: boolean
  readonly error: string | null
  readonly onClose: () => void
  readonly onApply: (body: {
    strategy: AssignmentStrategy
    config: Record<string, unknown>
    skip_unavailable: boolean
  }) => void
}

export function DistributeDialog({
  open,
  count,
  members,
  groups,
  pending,
  error,
  onClose,
  onApply,
}: DistributeDialogProps) {
  const [strategy, setStrategy] = useState<AssignmentStrategy>('ROUND_ROBIN')
  const [chosen, setChosen] = useState<string[]>([])
  const [groupId, setGroupId] = useState('')
  const [skipUnavailable, setSkipUnavailable] = useState(true)

  const [lastOpen, setLastOpen] = useState(open)
  if (open !== lastOpen) {
    setLastOpen(open)
    if (open) {
      setStrategy('ROUND_ROBIN')
      setChosen([])
      setGroupId('')
      setSkipUnavailable(true)
    }
  }

  const needsMembers = strategy === 'ROUND_ROBIN'
  const needsOne = strategy === 'FIXED'
  const needsGroup = strategy === 'SALES_GROUP'

  const ready =
    (needsMembers && chosen.length > 0) ||
    (needsOne && chosen.length === 1) ||
    (needsGroup && groupId !== '') ||
    strategy === 'UNASSIGNED'

  function config(): Record<string, unknown> {
    if (strategy === 'ROUND_ROBIN') return { membership_ids: chosen }
    if (strategy === 'FIXED') return { membership_id: chosen[0] }
    if (strategy === 'SALES_GROUP') return { group_id: groupId }
    return {}
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`Distribute ${count} lead${count === 1 ? '' : 's'}`}
      description="Applied as one change, so it can be undone from the edit report in one go."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!ready || pending}
            onClick={() =>
              onApply({ strategy, config: config(), skip_unavailable: skipUnavailable })
            }
          >
            Distribute {count}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : null}

        <div className="space-y-1.5">
          <FieldLabel htmlFor="distribute-strategy">How</FieldLabel>
          <Select
            id="distribute-strategy"
            value={strategy}
            onChange={(event) => {
              setStrategy(event.target.value as AssignmentStrategy)
              setChosen([])
            }}
          >
            <option value="ROUND_ROBIN">Share evenly between members</option>
            <option value="SALES_GROUP">Share across a sales group</option>
            <option value="FIXED">Give all of them to one person</option>
            <option value="UNASSIGNED">Unassign all of them</option>
          </Select>
        </div>

        {needsGroup ? (
          <div className="space-y-1.5">
            <FieldLabel htmlFor="distribute-group">Group</FieldLabel>
            <Select
              id="distribute-group"
              value={groupId}
              onChange={(event) => setGroupId(event.target.value)}
            >
              <option value="">Choose a group</option>
              {groups.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                </option>
              ))}
            </Select>
          </div>
        ) : null}

        {needsMembers || needsOne ? (
          <fieldset className="space-y-1.5">
            <legend className="text-sm font-medium">
              {needsOne ? 'Who gets them' : 'Share between'}
            </legend>
            <div className="max-h-48 space-y-1 overflow-y-auto rounded-md border p-2">
              {members.map((member) => (
                <label key={member.id} className="flex items-center gap-2 text-sm">
                  <input
                    type={needsOne ? 'radio' : 'checkbox'}
                    name="distribute-members"
                    checked={chosen.includes(member.id)}
                    onChange={(event) => {
                      if (needsOne) {
                        setChosen([member.id])
                      } else {
                        setChosen((current) =>
                          event.target.checked
                            ? [...current, member.id]
                            : current.filter((id) => id !== member.id),
                        )
                      }
                    }}
                  />
                  {member.user.full_name}
                </label>
              ))}
            </div>
          </fieldset>
        ) : null}

        {strategy !== 'UNASSIGNED' ? (
          <label className="flex items-start gap-2 rounded-md border p-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={skipUnavailable}
              onChange={(event) => setSkipUnavailable(event.target.checked)}
            />
            <span>
              Skip members who are on leave
              <span className="text-muted-foreground block text-xs">
                Members without a licence are always skipped.
              </span>
            </span>
          </label>
        ) : null}
      </div>
    </Dialog>
  )
}
