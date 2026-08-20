/**
 * Create / edit a lead field (docs/03-configuration-model.md §1.2).
 *
 * The type picker is populated from the registry, so this drawer offers exactly
 * the types the backend declares — no more, no fewer, and with the backend's
 * own labels and descriptions.
 *
 * On edit the type is shown but locked: changing it would invalidate every
 * value already stored under the field's key. The key itself is displayed
 * read-only for the same reason, so an admin renaming a field can see that the
 * stored data stays put.
 */

import { useEffect, useState } from 'react'

import { ApiError } from '@/api/client'
import type { FieldTypeSpec, LeadField } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useCreateField, useUpdateField } from '@/features/fields/api'
import { hasRendererFor } from '@/features/fields/renderers'
import { OptionEditor } from '@/features/fields/OptionEditor'

interface FieldDrawerProps {
  readonly workspaceId: string
  readonly open: boolean
  readonly onOpenChange: (open: boolean) => void
  /** Absent when creating. */
  readonly field: LeadField | null
  readonly fieldTypes: readonly FieldTypeSpec[]
  readonly allFields: readonly LeadField[]
}

/** The four properties from §1.4, with the effect each has spelled out. */
const PROPERTIES = [
  { key: 'show_in_import', label: 'Show in import', hint: 'Offered as a mappable CSV column' },
  { key: 'show_in_quick_add', label: 'Show in quick add', hint: 'Appears on the fast create form' },
  {
    key: 'lock_after_create',
    label: 'Lock after create',
    hint: 'Read-only once the lead exists',
  },
  {
    key: 'can_use_variable',
    label: 'Can use variable',
    hint: 'May contain {{placeholders}}, resolved when saved',
  },
] as const

type PropertyKey = (typeof PROPERTIES)[number]['key']

export function FieldDrawer({
  workspaceId,
  open,
  onOpenChange,
  field,
  fieldTypes,
  allFields,
}: FieldDrawerProps) {
  const isEdit = field !== null

  const [label, setLabel] = useState('')
  const [typeKey, setTypeKey] = useState('')
  const [description, setDescription] = useState('')
  const [required, setRequired] = useState(false)
  const [properties, setProperties] = useState<Record<PropertyKey, boolean>>({
    show_in_import: true,
    show_in_quick_add: false,
    lock_after_create: false,
    can_use_variable: false,
  })
  const [error, setError] = useState<string | null>(null)

  const createField = useCreateField(workspaceId)
  const updateField = useUpdateField(workspaceId)

  useEffect(() => {
    if (!open) return
    setError(null)
    setLabel(field?.label ?? '')
    setTypeKey(field?.field_type ?? fieldTypes[0]?.key ?? '')
    setDescription(field?.description ?? '')
    setRequired(field?.is_required ?? false)
    setProperties({
      show_in_import: field?.show_in_import ?? true,
      show_in_quick_add: field?.show_in_quick_add ?? false,
      lock_after_create: field?.lock_after_create ?? false,
      can_use_variable: field?.can_use_variable ?? false,
    })
  }, [open, field, fieldTypes])

  const selectedType = fieldTypes.find((candidate) => candidate.key === typeKey)

  const submit = async () => {
    setError(null)
    try {
      if (isEdit) {
        await updateField.mutateAsync({
          fieldId: field.id,
          label: label.trim(),
          description: description.trim() || null,
          is_required: required,
          ...properties,
        })
      } else {
        await createField.mutateAsync({
          label: label.trim(),
          field_type: typeKey,
          description: description.trim() || null,
          is_required: required,
          ...properties,
        })
        onOpenChange(false)
      }
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Could not save the field.')
    }
  }

  return (
    <Dialog
      open={open}
      onClose={() => onOpenChange(false)}
      className="max-h-[85vh] overflow-y-auto sm:max-w-2xl"
      title={isEdit ? `Edit ${field.label}` : 'Add a new field'}
      description={
        isEdit
          ? 'The type and key are fixed — every value already stored is filed under them.'
          : 'Types come from the server registry, so this list is whatever the API supports.'
      }
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button
            disabled={label.trim() === '' || createField.isPending || updateField.isPending}
            onClick={() => void submit()}
          >
            {isEdit ? 'Save changes' : 'Create field'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="field-label">Name</Label>
          <Input
            id="field-label"
            maxLength={40}
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
          <p className="text-muted-foreground text-xs">{label.length}/40</p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="field-type">Type</Label>
          <Select
            id="field-type"
            value={typeKey}
            disabled={isEdit}
            onChange={(event) => setTypeKey(event.target.value)}
          >
            {fieldTypes.map((spec) => (
              <option key={spec.key} value={spec.key}>
                {spec.label}
              </option>
            ))}
          </Select>
          {selectedType ? (
            <p className="text-muted-foreground text-xs">
              {selectedType.description}
              {hasRendererFor(selectedType.renderer.widget) ? null : (
                <span className="text-destructive">
                  {' '}
                  — this build has no renderer for {selectedType.renderer.widget}.
                </span>
              )}
            </p>
          ) : null}
          {isEdit ? (
            <p className="text-muted-foreground text-xs">
              Stored under key <code>{field.key}</code>, which never changes.
            </p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="field-description">Description</Label>
          <Textarea
            id="field-description"
            rows={2}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </div>

        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Properties</legend>
          <label className="flex items-start gap-2 text-sm">
            <Checkbox
              className="mt-0.5"
              checked={required}
              onChange={(event) => setRequired(event.target.checked)}
            />
            <span>
              Required
              <span className="text-muted-foreground block text-xs">
                Must have a value when a lead is created
              </span>
            </span>
          </label>
          {PROPERTIES.map((property) => (
            <label key={property.key} className="flex items-start gap-2 text-sm">
              <Checkbox
                className="mt-0.5"
                checked={properties[property.key]}
                onChange={(event) =>
                  setProperties((current) => ({
                    ...current,
                    [property.key]: event.target.checked,
                  }))
                }
              />
              <span>
                {property.label}
                <span className="text-muted-foreground block text-xs">{property.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>

        {isEdit && selectedType?.uses_options ? (
          <div className="border-t pt-4">
            <OptionEditor
              workspaceId={workspaceId}
              field={field}
              isCascade={selectedType.renderer.optionsAreTree === true}
              copySources={allFields.filter(
                (candidate) => candidate.id !== field.id && candidate.options.length > 0,
              )}
            />
          </div>
        ) : null}

        {!isEdit && selectedType?.uses_options ? (
          <p className="text-muted-foreground rounded-md border border-dashed p-3 text-sm">
            Save the field first, then reopen it to add options.
          </p>
        ) : null}

        {isEdit && field.is_builtin ? (
          <Badge variant="secondary">Built-in — renameable, never deleted</Badge>
        ) : null}

        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : null}
      </div>
    </Dialog>
  )
}
