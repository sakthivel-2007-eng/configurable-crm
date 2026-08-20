/**
 * Which widgets this build can draw.
 *
 * Separate from `FieldInput` for two reasons: that module then exports only
 * components, which is what React Fast Refresh needs, and the settings screen
 * can ask "can we draw this?" without importing a component to find out.
 *
 * `FieldInput`'s widget map is typed against `SupportedWidget`, so adding a
 * renderer without listing it here — or listing one without writing it — is a
 * compile error rather than a silent fallback.
 */

/**
 * Widget names this frontend implements.
 *
 * These are *drawing* concerns the backend registry names, never customer
 * vocabulary. The list of field **types** is the backend's and is fetched at
 * runtime; this is only the set of shapes we know how to put on screen.
 */
export const SUPPORTED_WIDGETS = [
  'text',
  'email',
  'phone',
  'url',
  'media-link',
  'number',
  'checkbox',
  'date',
  'select',
  'money',
  'cascader',
  'recurring-date',
  'location',
] as const

export type SupportedWidget = (typeof SUPPORTED_WIDGETS)[number]

/**
 * Whether a renderer exists for a widget the backend registry named.
 *
 * A `false` is not an error: it means the API declares a field type this build
 * has not caught up with. The settings screen flags it, and `FieldInput` falls
 * back to a JSON editor so the value stays reachable.
 */
export function hasRendererFor(widget: string): boolean {
  return (SUPPORTED_WIDGETS as readonly string[]).includes(widget)
}

/**
 * HTML input type for a widget, for the simple single-control widgets.
 *
 * Used by the custom-action form, whose fields come from the *action* registry
 * — a different, smaller type set than the lead schema's. Branching on the
 * widget the server named keeps that form as type-agnostic as the lead form:
 * neither file mentions a field type.
 */
export function inputTypeForWidget(widget: string): string {
  if (widget === 'number') return 'number'
  if (widget === 'date') return 'date'
  if (widget === 'email') return 'email'
  if (widget === 'url' || widget === 'media-link') return 'url'
  return 'text'
}
