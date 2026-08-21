/**
 * Editing many leads at once (M7).
 *
 * The whole run is one changeset, so whatever this does can be undone from the
 * edit report in a single click. That property is what makes a bulk edit safe
 * enough to offer at all, so the dialog says so where the decision is made
 * rather than leaving the operator to hope.
 *
 * Only fields the caller can *edit* are offered. The server refuses the rest by
 * name — it never silently drops them — so offering a field here that the
 * commit would reject would just be a slower way to show an error.
 */

import { useState } from 'react'

import type { FieldTypeSpec, LeadField, MemberDetail, Stage } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Label as FieldLabel } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { FieldInput } from '@/features/fields/FieldInput'

export interface BulkEditDialogProps {
  readonly open: boolean
  readonly count: number
  readonly fields: readonly LeadField[]
  readonly fieldTypes: readonly FieldTypeSpec[]
  readonly stages: readonly Stage[]
  readonly members: readonly MemberDetail[]
  readonly pending: boolean
  readonly error: string | null
  readonly onClose: () => void
  readonly onApply: (body: Record<string, unknown>) => void
}

export function BulkEditDialog({
  open,
  count,
  fields,
  fieldTypes,
  stages,
  members,
  pending,
  error,
  onClose,
  onApply,
}: BulkEditDialogProps) {
  const [fieldKey, setFieldKey] = useState('')
  const [value, setValue] = useState<unknown>(null)
  const [stageId, setStageId] = useState('')
  const [assigneeId, setAssigneeId] = useState('')

  const [lastOpen, setLastOpen] = useState(open)
  if (open !== lastOpen) {
    setLastOpen(open)
    if (open) {
      setFieldKey('')
      setValue(null)
      setStageId('')
      setAssigneeId('')
    }
  }

  const field = fields.find((candidate) => candidate.key === fieldKey)
  const spec = fieldTypes.find((candidate) => candidate.key === field?.field_type)
  const nothingChosen = !fieldKey && !stageId && !assigneeId

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`Edit ${count} lead${count === 1 ? '' : 's'}`}
      description="Applied as one change, so it can be undone from the edit report in one go."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={nothingChosen || pending}
            onClick={() =>
              onApply({
                ...(fieldKey ? { values: { [fieldKey]: value } } : {}),
                ...(stageId ? { stage_id: stageId } : {}),
                ...(assigneeId ? { assignee_id: assigneeId } : {}),
              })
            }
          >
            Apply to {count}
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
          <FieldLabel htmlFor="bulk-stage">Stage</FieldLabel>
          <Select
            id="bulk-stage"
            value={stageId}
            onChange={(event) => setStageId(event.target.value)}
          >
            <option value="">Leave unchanged</option>
            {stages.map((stage) => (
              <option key={stage.id} value={stage.id}>
                {stage.label}
              </option>
            ))}
          </Select>
        </div>

        <div className="space-y-1.5">
          <FieldLabel htmlFor="bulk-assignee">Assignee</FieldLabel>
          <Select
            id="bulk-assignee"
            value={assigneeId}
            onChange={(event) => setAssigneeId(event.target.value)}
          >
            <option value="">Leave unchanged</option>
            {members.map((member) => (
              <option key={member.id} value={member.id}>
                {member.user.full_name}
              </option>
            ))}
          </Select>
        </div>

        <div className="space-y-1.5 border-t pt-3">
          <FieldLabel htmlFor="bulk-field">Set a field</FieldLabel>
          <Select
            id="bulk-field"
            value={fieldKey}
            onChange={(event) => {
              setFieldKey(event.target.value)
              setValue(null)
            }}
          >
            <option value="">Leave fields unchanged</option>
            {fields
              .filter((candidate) => !candidate.is_hidden)
              .map((candidate) => (
                <option key={candidate.key} value={candidate.key}>
                  {candidate.label}
                </option>
              ))}
          </Select>
        </div>

        {field && spec ? (
          <div className="space-y-1.5">
            <FieldLabel htmlFor={`bulk-value-${field.key}`}>{field.label}</FieldLabel>
            {/* The same renderer the lead form uses, so a DROPDOWN offers the
                workspace's own options rather than a free-text box. */}
            <FieldInput
              field={field}
              renderer={spec.renderer}
              inputId={`bulk-value-${field.key}`}
              value={value}
              onChange={setValue}
            />
          </div>
        ) : null}
      </div>
    </Dialog>
  )
}
