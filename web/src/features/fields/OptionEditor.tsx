/**
 * The option editor (docs/03-configuration-model.md §1.5).
 *
 * One row per option — colour swatch, label, remove — plus "Add multiple" and
 * "Copy options". Reordering is up/down buttons rather than drag: the same
 * ordering, keyboard-reachable, and without a drag library for a list that is
 * usually under twenty rows.
 *
 * For a dependent dropdown the same editor grows a parent picker, because the
 * cascade is just `parent_option_id` on an ordinary option.
 */

import { useState } from 'react'

import { ApiError } from '@/api/client'
import type { FieldOption, LeadField } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import {
  useAddOption,
  useAddOptionsBulk,
  useArchiveOption,
  useCopyOptions,
  useReorderOptions,
  useUpdateOption,
} from '@/features/fields/api'

interface OptionEditorProps {
  readonly workspaceId: string
  readonly field: LeadField
  /** Other option-bearing fields, for "Copy options". */
  readonly copySources: readonly LeadField[]
  readonly isCascade: boolean
}

export function OptionEditor({ workspaceId, field, copySources, isCascade }: OptionEditorProps) {
  const [label, setLabel] = useState('')
  const [color, setColor] = useState('#6b7280')
  const [parentId, setParentId] = useState('')
  const [bulkText, setBulkText] = useState('')
  const [showBulk, setShowBulk] = useState(false)
  const [copyFrom, setCopyFrom] = useState('')
  const [error, setError] = useState<string | null>(null)

  const addOption = useAddOption(workspaceId)
  const addBulk = useAddOptionsBulk(workspaceId)
  const copyOptions = useCopyOptions(workspaceId)
  const updateOption = useUpdateOption(workspaceId)
  const archiveOption = useArchiveOption(workspaceId)
  const reorder = useReorderOptions(workspaceId)

  const options = field.options
  const parents = options.filter((option) => option.parent_option_id === null)

  const run = async (work: Promise<unknown>) => {
    setError(null)
    try {
      await work
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'That action failed.')
    }
  }

  const move = async (option: FieldOption, delta: number) => {
    const ordered = [...options].sort((a, b) => a.sort_order - b.sort_order)
    const index = ordered.findIndex((candidate) => candidate.id === option.id)
    const target = index + delta
    if (target < 0 || target >= ordered.length) return

    // Rebuild the order rather than mutating in place: the index access is
    // bounds-checked above, but TypeScript cannot see that through a swap.
    const swapped = ordered.map((entry, position) => {
      if (position === index) return ordered[target] as FieldOption
      if (position === target) return ordered[index] as FieldOption
      return entry
    })
    await run(
      reorder.mutateAsync({ fieldId: field.id, orderedIds: swapped.map((entry) => entry.id) }),
    )
  }

  return (
    <div className="space-y-3" data-testid="option-editor">
      <div className="flex items-center justify-between">
        <Label>Options</Label>
        <div className="flex gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={() => setShowBulk((on) => !on)}>
            Add multiple
          </Button>
        </div>
      </div>

      {options.length === 0 ? (
        <p className="text-muted-foreground text-sm">No options yet.</p>
      ) : (
        <ul className="space-y-1">
          {[...options]
            .sort((a, b) => a.sort_order - b.sort_order)
            .map((option) => (
              <li
                key={option.id}
                className="flex items-center gap-2 rounded-md border px-2 py-1.5"
                data-testid="option-row"
              >
                <input
                  type="color"
                  className="size-6 shrink-0 cursor-pointer rounded border-0 bg-transparent p-0"
                  aria-label={`Colour for ${option.label}`}
                  value={option.color ?? '#6b7280'}
                  onChange={(event) =>
                    void run(
                      updateOption.mutateAsync({
                        fieldId: field.id,
                        optionId: option.id,
                        color: event.target.value,
                      }),
                    )
                  }
                />
                <Input
                  className="h-7 flex-1"
                  aria-label={`Label for ${option.label}`}
                  defaultValue={option.label}
                  onBlur={(event) => {
                    if (event.target.value !== option.label) {
                      void run(
                        updateOption.mutateAsync({
                          fieldId: field.id,
                          optionId: option.id,
                          label: event.target.value,
                        }),
                      )
                    }
                  }}
                />
                {option.parent_option_id ? (
                  <Badge variant="outline" className="shrink-0">
                    under {parents.find((p) => p.id === option.parent_option_id)?.label ?? '—'}
                  </Badge>
                ) : null}
                {option.is_archived ? (
                  <Badge variant="secondary" className="shrink-0">
                    Archived
                  </Badge>
                ) : null}
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={`Move ${option.label} up`}
                  onClick={() => void move(option, -1)}
                >
                  ↑
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={`Move ${option.label} down`}
                  onClick={() => void move(option, 1)}
                >
                  ↓
                </Button>
                {option.is_archived ? null : (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label={`Archive ${option.label}`}
                    onClick={() =>
                      void run(
                        archiveOption.mutateAsync({ fieldId: field.id, optionId: option.id }),
                      )
                    }
                  >
                    ✕
                  </Button>
                )}
              </li>
            ))}
        </ul>
      )}

      <div className="flex flex-wrap items-end gap-2">
        <input
          type="color"
          className="size-9 shrink-0 cursor-pointer rounded border-0 bg-transparent p-0"
          aria-label="New option colour"
          value={color}
          onChange={(event) => setColor(event.target.value)}
        />
        <Input
          className="min-w-40 flex-1"
          placeholder="New option label"
          aria-label="New option label"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
        />
        {isCascade ? (
          <Select
            className="w-44"
            aria-label="Parent option"
            value={parentId}
            onChange={(event) => setParentId(event.target.value)}
          >
            <option value="">Top level</option>
            {parents.map((parent) => (
              <option key={parent.id} value={parent.id}>
                under {parent.label}
              </option>
            ))}
          </Select>
        ) : null}
        <Button
          type="button"
          size="sm"
          disabled={label.trim() === '' || addOption.isPending}
          onClick={() => {
            void (async () => {
              await run(
                addOption.mutateAsync({
                  fieldId: field.id,
                  label: label.trim(),
                  color,
                  parent_option_id: parentId === '' ? null : parentId,
                }),
              )
              setLabel('')
            })()
          }}
        >
          Add option
        </Button>
      </div>

      {showBulk ? (
        <div className="space-y-2 rounded-md border p-3">
          <Label htmlFor="bulk-options">One option per line</Label>
          <Textarea
            id="bulk-options"
            rows={4}
            value={bulkText}
            onChange={(event) => setBulkText(event.target.value)}
          />
          <p className="text-muted-foreground text-xs">
            Blank lines and labels that already exist are skipped — pasting a spreadsheet column
            reliably includes both.
          </p>
          <Button
            type="button"
            size="sm"
            disabled={bulkText.trim() === '' || addBulk.isPending}
            onClick={() => {
              void (async () => {
                await run(addBulk.mutateAsync({ fieldId: field.id, labels: bulkText.split('\n') }))
                setBulkText('')
                setShowBulk(false)
              })()
            }}
          >
            Add them
          </Button>
        </div>
      ) : null}

      {copySources.length > 0 ? (
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <Label htmlFor="copy-from">Copy options from</Label>
            <Select
              id="copy-from"
              value={copyFrom}
              onChange={(event) => setCopyFrom(event.target.value)}
            >
              <option value="">Select a field…</option>
              {copySources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.label}
                </option>
              ))}
            </Select>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={copyFrom === '' || copyOptions.isPending}
            onClick={() => {
              void (async () => {
                await run(copyOptions.mutateAsync({ fieldId: field.id, sourceFieldId: copyFrom }))
                setCopyFrom('')
              })()
            }}
          >
            Copy
          </Button>
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      ) : null}
    </div>
  )
}
