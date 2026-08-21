/**
 * Which columns the grid shows, and in what order (M6).
 *
 * The list of *available* columns is the workspace's own fields plus the
 * handful of built-in ones, filtered to what this caller may view — a field
 * absent from the projection is absent here too, so the picker never offers a
 * column that would come back blank.
 *
 * Reordering uses the native HTML5 drag events rather than a drag library. The
 * list is short, the interaction is one-dimensional, and a dependency for it
 * would be the larger cost. Chosen columns are also reorderable from the
 * keyboard, because a drag-only control is unusable for anyone who cannot.
 */

import { useState } from 'react'

import type { LeadField } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog } from '@/components/ui/dialog'
import { BUILTIN_COLUMNS } from '@/features/leads/columns'

export interface ColumnPickerProps {
  readonly open: boolean
  readonly fields: readonly LeadField[]
  readonly selected: readonly string[]
  readonly onClose: () => void
  readonly onApply: (columns: readonly string[]) => void
}

interface Choice {
  readonly id: string
  readonly label: string
  readonly group: string
}

export function ColumnPicker({ open, fields, selected, onClose, onApply }: ColumnPickerProps) {
  const [draft, setDraft] = useState<readonly string[]>(selected)
  const [dragging, setDragging] = useState<number | null>(null)

  // Re-seed whenever the dialog is reopened, so cancelling really cancels.
  const [lastOpen, setLastOpen] = useState(open)
  if (open !== lastOpen) {
    setLastOpen(open)
    if (open) setDraft(selected)
  }

  const choices: readonly Choice[] = [
    ...BUILTIN_COLUMNS.map((column) => ({
      id: column.id,
      label: column.label,
      group: 'Built in',
    })),
    ...fields
      .filter((field) => !field.is_hidden)
      .map((field) => ({ id: field.key, label: field.label, group: 'Fields' })),
  ]

  const chosen = draft
    .map((id) => choices.find((choice) => choice.id === id))
    .filter((choice): choice is Choice => choice !== undefined)

  function toggle(id: string) {
    setDraft((current) =>
      current.includes(id) ? current.filter((entry) => entry !== id) : [...current, id],
    )
  }

  function move(from: number, to: number) {
    if (to < 0 || to >= draft.length) return
    const next = draft.slice()
    const [moved] = next.splice(from, 1)
    if (moved === undefined) return
    next.splice(to, 0, moved)
    setDraft(next)
  }

  return (
    <Dialog open={open} onClose={onClose} title="Columns">
      <p className="text-muted-foreground text-sm">
        Choose what the list shows and drag to reorder. Saved per person, per filter.
      </p>

      <div className="grid gap-4 py-3 sm:grid-cols-2">
        <div>
          <p className="mb-2 text-sm font-medium">Available</p>
          <div className="max-h-72 space-y-1 overflow-y-auto pr-2">
            {['Built in', 'Fields'].map((group) => (
              <div key={group}>
                <p className="text-muted-foreground mt-2 mb-1 text-xs uppercase">{group}</p>
                {choices
                  .filter((choice) => choice.group === group)
                  .map((choice) => (
                    <label
                      key={choice.id}
                      className="hover:bg-muted/60 flex items-center gap-2 rounded px-1 py-1 text-sm"
                    >
                      <Checkbox
                        checked={draft.includes(choice.id)}
                        onChange={() => toggle(choice.id)}
                        aria-label={choice.label}
                      />
                      {choice.label}
                    </label>
                  ))}
              </div>
            ))}
          </div>
        </div>

        <div>
          <p className="mb-2 text-sm font-medium">Shown, in order</p>
          <ol className="max-h-72 space-y-1 overflow-y-auto pr-2">
            {chosen.map((choice, index) => (
              <li
                key={choice.id}
                draggable
                onDragStart={() => setDragging(index)}
                onDragOver={(event) => event.preventDefault()}
                onDrop={() => {
                  if (dragging !== null) move(dragging, index)
                  setDragging(null)
                }}
                className="bg-background flex items-center gap-2 rounded border px-2 py-1 text-sm"
              >
                <span className="text-muted-foreground cursor-grab" aria-hidden>
                  ⋮⋮
                </span>
                <span className="flex-1">{choice.label}</span>
                {/* Keyboard equivalents for the drag above. */}
                <button
                  type="button"
                  className="text-muted-foreground hover:text-foreground px-1"
                  aria-label={`Move ${choice.label} up`}
                  onClick={() => move(index, index - 1)}
                >
                  ↑
                </button>
                <button
                  type="button"
                  className="text-muted-foreground hover:text-foreground px-1"
                  aria-label={`Move ${choice.label} down`}
                  onClick={() => move(index, index + 1)}
                >
                  ↓
                </button>
              </li>
            ))}
            {chosen.length === 0 && (
              <li className="text-muted-foreground px-1 py-2 text-sm">Pick at least one column.</li>
            )}
          </ol>
        </div>
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="button" disabled={chosen.length === 0} onClick={() => onApply(draft)}>
          Save columns
        </Button>
      </div>
    </Dialog>
  )
}
