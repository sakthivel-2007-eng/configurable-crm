/**
 * The field renderer — one input per *widget*, dispatched from the registry.
 *
 * The distinction that matters: this file knows how to draw a **widget**, which
 * is a product concept ("a cascading pair of selects", "a date plus a
 * recurrence rule"). It does not know which **field types** exist. That list
 * comes from `GET /settings/field-types`, and each entry names the widget to
 * use plus the keys its composite value is built from.
 *
 * So a workspace with a field called "Territory" of type `DEPENDENT_DROPDOWN`
 * renders through `cascader` because the *backend* said so — nothing here
 * mentions a territory, a course, or any other customer's vocabulary.
 *
 * An unknown widget degrades to a JSON textarea rather than throwing: a
 * frontend one deploy behind a backend that added a type should still let an
 * operator see and edit the value.
 */

import type { FieldOption, FieldRendererContract, LeadField } from '@/api/types'
import type { SupportedWidget } from '@/features/fields/renderers'
import { toDisplayString } from '@/lib/format'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'

export interface FieldInputProps {
  readonly field: LeadField
  readonly renderer: FieldRendererContract
  readonly value: unknown
  readonly disabled?: boolean
  /**
   * DOM id for the primary control, so a caller's `<Label htmlFor>` actually
   * points at something. Composite widgets draw several controls and label each
   * with `aria-label` instead — there is no single input for a label to own.
   */
  readonly inputId?: string
  readonly onChange: (value: unknown) => void
}

function liveOptions(field: LeadField): readonly FieldOption[] {
  // Archived options stay readable on existing leads but are not offered for
  // new selections — that is the whole point of archiving rather than deleting.
  return field.options.filter((option) => !option.is_archived)
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function TextWidget({ renderer, value, disabled, inputId, onChange }: FieldInputProps) {
  const text = typeof value === 'string' ? value : ''
  if (renderer.multiline) {
    return (
      <Textarea
        id={inputId}
        value={text}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
    )
  }
  return (
    <Input
      id={inputId}
      value={text}
      disabled={disabled}
      inputMode={renderer.inputMode as React.HTMLAttributes<HTMLInputElement>['inputMode']}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

function NumberWidget({ value, disabled, inputId, onChange }: FieldInputProps) {
  return (
    <Input
      id={inputId}
      type="number"
      value={toDisplayString(value)}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value === '' ? null : Number(event.target.value))}
    />
  )
}

function CheckboxWidget({ field, value, disabled, inputId, onChange }: FieldInputProps) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <Checkbox
        id={inputId}
        checked={value === true}
        disabled={disabled}
        aria-label={field.label}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="text-muted-foreground">{value === true ? 'Yes' : 'No'}</span>
    </label>
  )
}

function DateWidget({ value, disabled, inputId, onChange }: FieldInputProps) {
  return (
    <Input
      id={inputId}
      type="date"
      value={typeof value === 'string' ? value.slice(0, 10) : ''}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value || null)}
    />
  )
}

function SelectWidget({ field, renderer, value, disabled, inputId, onChange }: FieldInputProps) {
  const options = liveOptions(field)

  // TAGS: the same widget with `multiple`, per the registry's contract.
  if (renderer.multiple) {
    const selected = Array.isArray(value) ? (value as string[]) : []
    return (
      <div className="flex flex-wrap gap-2">
        {options.length === 0 ? (
          <p className="text-muted-foreground text-sm">No options yet.</p>
        ) : null}
        {options.map((option) => {
          const checked = selected.includes(option.code)
          return (
            <label
              key={option.id}
              className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-sm"
              style={option.color ? { borderColor: option.color } : undefined}
            >
              <Checkbox
                checked={checked}
                disabled={disabled}
                aria-label={option.label}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? [...selected, option.code]
                      : selected.filter((code) => code !== option.code),
                  )
                }
              />
              {option.label}
            </label>
          )
        })}
      </div>
    )
  }

  return (
    <Select
      id={inputId}
      value={typeof value === 'string' ? value : ''}
      disabled={disabled}
      aria-label={field.label}
      onChange={(event) => onChange(event.target.value || null)}
    >
      <option value="">Select…</option>
      {options.map((option) => (
        <option key={option.id} value={option.code}>
          {option.label}
        </option>
      ))}
    </Select>
  )
}

function MoneyWidget({ renderer, value, disabled, onChange }: FieldInputProps) {
  const record = asRecord(value)
  const amountKey = renderer.amountKey ?? 'amount'
  const currencyKey = renderer.currencyKey ?? 'currency'

  return (
    <div className="flex gap-2">
      <Input
        className="w-24"
        placeholder="INR"
        aria-label="Currency"
        value={typeof record[currencyKey] === 'string' ? record[currencyKey] : ''}
        disabled={disabled}
        onChange={(event) =>
          onChange({ ...record, [currencyKey]: event.target.value.toUpperCase() })
        }
      />
      <Input
        type="number"
        step="0.01"
        placeholder="0.00"
        aria-label="Amount"
        value={toDisplayString(record[amountKey])}
        disabled={disabled}
        onChange={(event) =>
          onChange(
            event.target.value === '' ? null : { ...record, [amountKey]: event.target.value },
          )
        }
      />
    </div>
  )
}

/**
 * DEPENDENT_DROPDOWN — two selects, the second filtered by the first.
 *
 * The option tree comes from `parent_option_id` on each option, so the cascade
 * is data. Choosing a parent clears the child, because a child that no longer
 * belongs to the selected parent would be rejected by the server anyway and is
 * confusing on screen before it gets there.
 */
function CascaderWidget({ field, renderer, value, disabled, onChange }: FieldInputProps) {
  const options = liveOptions(field)
  const parents = options.filter((option) => option.parent_option_id === null)
  const record = asRecord(value)

  const valueKey = renderer.valueKey ?? 'value'
  const parentKey = renderer.parentKey ?? 'parent'
  const selectedChildCode = typeof record[valueKey] === 'string' ? record[valueKey] : ''

  const selectedChild = options.find((option) => option.code === selectedChildCode)
  const selectedParentId =
    selectedChild?.parent_option_id ??
    parents.find((parent) => parent.code === record[parentKey])?.id ??
    ''

  const children = options.filter((option) => option.parent_option_id === selectedParentId)

  return (
    <div className="grid gap-2 sm:grid-cols-2">
      <Select
        aria-label={`${field.label} — parent`}
        value={selectedParentId}
        disabled={disabled}
        onChange={(event) => {
          const parent = parents.find((candidate) => candidate.id === event.target.value)
          // Clearing the child is deliberate: the previous one belongs to a
          // different branch of the tree.
          onChange(parent ? { [parentKey]: parent.code, [valueKey]: null } : null)
        }}
      >
        <option value="">Select…</option>
        {parents.map((parent) => (
          <option key={parent.id} value={parent.id}>
            {parent.label}
          </option>
        ))}
      </Select>

      <Select
        aria-label={`${field.label} — child`}
        value={selectedChildCode}
        disabled={disabled || selectedParentId === ''}
        onChange={(event) => {
          const child = children.find((candidate) => candidate.code === event.target.value)
          onChange(
            child
              ? {
                  [parentKey]:
                    parents.find((parent) => parent.id === child.parent_option_id)?.code ?? null,
                  [valueKey]: child.code,
                }
              : null,
          )
        }}
      >
        <option value="">{selectedParentId === '' ? 'Choose above first' : 'Select…'}</option>
        {children.map((child) => (
          <option key={child.id} value={child.code}>
            {child.label}
          </option>
        ))}
      </Select>
    </div>
  )
}

/**
 * RECURRING_DATE — a start date plus a recurrence rule.
 *
 * The frequency list comes from the registry, not from a constant here. `next`
 * is derived server-side and shown read-only: it is the value filters and
 * reminders index, so letting an operator type it would let them lie to the
 * scheduler.
 */
function RecurringDateWidget({ field, renderer, value, disabled, onChange }: FieldInputProps) {
  const record = asRecord(value)
  const startKey = renderer.startKey ?? 'start'
  const frequencyKey = renderer.frequencyKey ?? 'frequency'
  const intervalKey = renderer.intervalKey ?? 'interval'
  const derivedKey = renderer.derivedKey ?? 'next'
  const frequencies = renderer.frequencies ?? []

  const start = typeof record[startKey] === 'string' ? record[startKey] : ''
  const next = typeof record[derivedKey] === 'string' ? record[derivedKey] : null

  const update = (patch: Record<string, unknown>) => {
    const merged = { ...record, ...patch }
    onChange(merged[startKey] ? merged : null)
  }

  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-3">
        <Input
          type="date"
          aria-label={`${field.label} — start`}
          value={start}
          disabled={disabled}
          onChange={(event) => update({ [startKey]: event.target.value || null })}
        />
        <Select
          aria-label={`${field.label} — frequency`}
          value={typeof record[frequencyKey] === 'string' ? record[frequencyKey] : ''}
          disabled={disabled}
          onChange={(event) => update({ [frequencyKey]: event.target.value })}
        >
          <option value="">Frequency…</option>
          {frequencies.map((frequency) => (
            <option key={frequency} value={frequency}>
              {frequency.charAt(0) + frequency.slice(1).toLowerCase()}
            </option>
          ))}
        </Select>
        <Input
          type="number"
          min={1}
          aria-label={`${field.label} — interval`}
          placeholder="Every 1"
          value={toDisplayString(record[intervalKey])}
          disabled={disabled}
          onChange={(event) =>
            update({ [intervalKey]: event.target.value === '' ? 1 : Number(event.target.value) })
          }
        />
      </div>
      {next ? (
        <p className="text-muted-foreground text-xs">
          Next occurrence <span className="font-medium">{next}</span> — derived when saved.
        </p>
      ) : null}
    </div>
  )
}

/**
 * LOCATION — structured address plus optional coordinates.
 *
 * The text parts come from the registry's `textKeys`, so the shape is the
 * backend's to define. Coordinates are entered together or not at all, matching
 * the server's rule that half a pair is invalid.
 */
function LocationWidget({ field, renderer, value, disabled, onChange }: FieldInputProps) {
  const record = asRecord(value)
  const textKeys = renderer.textKeys ?? []
  const latKey = renderer.latKey ?? 'lat'
  const lngKey = renderer.lngKey ?? 'lng'

  const update = (patch: Record<string, unknown>) => {
    const merged = { ...record, ...patch }
    const populated = Object.entries(merged).filter(
      ([, entry]) => entry !== '' && entry !== null && entry !== undefined,
    )
    onChange(populated.length === 0 ? null : Object.fromEntries(populated))
  }

  const humanise = (key: string) =>
    key.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())

  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-2">
        {textKeys.map((key) => (
          <Input
            key={key}
            placeholder={humanise(key)}
            aria-label={`${field.label} — ${humanise(key)}`}
            value={typeof record[key] === 'string' ? record[key] : ''}
            disabled={disabled}
            onChange={(event) => update({ [key]: event.target.value })}
          />
        ))}
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <Input
          type="number"
          step="any"
          placeholder="Latitude"
          aria-label={`${field.label} — latitude`}
          value={toDisplayString(record[latKey])}
          disabled={disabled}
          onChange={(event) =>
            update({ [latKey]: event.target.value === '' ? null : Number(event.target.value) })
          }
        />
        <Input
          type="number"
          step="any"
          placeholder="Longitude"
          aria-label={`${field.label} — longitude`}
          value={toDisplayString(record[lngKey])}
          disabled={disabled}
          onChange={(event) =>
            update({ [lngKey]: event.target.value === '' ? null : Number(event.target.value) })
          }
        />
      </div>
    </div>
  )
}

/** Last resort for a widget this build does not know about. */
function UnknownWidget({ renderer, value, disabled, onChange }: FieldInputProps) {
  return (
    <div className="space-y-1">
      <Textarea
        rows={2}
        value={value === null || value === undefined ? '' : JSON.stringify(value)}
        disabled={disabled}
        onChange={(event) => {
          try {
            onChange(event.target.value === '' ? null : JSON.parse(event.target.value))
          } catch {
            // Keep the keystroke; the server validates on save either way.
            onChange(event.target.value)
          }
        }}
      />
      <p className="text-muted-foreground text-xs">
        This build has no renderer for <code>{renderer.widget}</code>. Editing as JSON.
      </p>
    </div>
  )
}

/**
 * Widget name -> component.
 *
 * Every key here is a *drawing* concern the backend named in its renderer
 * contract. If you are about to add a key that reads like a customer's
 * vocabulary rather than a shape of input, it belongs in the backend registry
 * instead.
 */
const WIDGETS: Record<SupportedWidget, (props: FieldInputProps) => React.JSX.Element> = {
  text: TextWidget,
  email: TextWidget,
  phone: TextWidget,
  url: TextWidget,
  'media-link': TextWidget,
  number: NumberWidget,
  checkbox: CheckboxWidget,
  date: DateWidget,
  select: SelectWidget,
  money: MoneyWidget,
  cascader: CascaderWidget,
  'recurring-date': RecurringDateWidget,
  location: LocationWidget,
}

export function FieldInput(props: FieldInputProps) {
  const Widget = WIDGETS[props.renderer.widget as SupportedWidget] ?? UnknownWidget
  return <Widget {...props} />
}
