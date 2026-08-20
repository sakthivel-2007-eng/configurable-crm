/**
 * Rendering stored field values as text.
 *
 * Lead values are `unknown` by design — the schema belongs to the customer, so
 * a value may be a string, a number, a list of option codes, or one of the
 * three composite shapes. `String(value)` on a composite produces
 * `[object Object]`, which is why every display path goes through here instead.
 *
 * The composite shapes are recognised by the keys the backend's renderer
 * contract declares, not by the field's type: this function is handed a value,
 * not a field, and that keeps it usable from the timeline where only the
 * payload survives.
 */
export function toDisplayString(value: unknown): string {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number') return String(value)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'

  if (Array.isArray(value)) {
    return value.map(toDisplayString).filter(Boolean).join(', ')
  }

  if (typeof value === 'object') {
    const record = value as Record<string, unknown>

    // MONEY — currency first, the way an amount is spoken.
    if ('amount' in record && 'currency' in record) {
      return `${toDisplayString(record.currency)} ${toDisplayString(record.amount)}`.trim()
    }
    // DEPENDENT_DROPDOWN — the leaf is what was chosen.
    if ('value' in record) return toDisplayString(record.value)
    // RECURRING_DATE — the next occurrence is the useful one.
    if ('start' in record) return toDisplayString(record.next ?? record.start)
    // LOCATION and anything else composite: the populated text parts, with
    // coordinates dropped because they are not what a human reads.
    return Object.entries(record)
      .filter(([key, entry]) => key !== 'lat' && key !== 'lng' && entry !== null && entry !== '')
      .map(([, entry]) => toDisplayString(entry))
      .filter(Boolean)
      .join(', ')
  }

  return ''
}

/** The same, but with a placeholder when there is nothing to show. */
export function toDisplayStringOr(value: unknown, fallback = '—'): string {
  return toDisplayString(value) || fallback
}
