/**
 * The one bar chart (M9).
 *
 * Every bar widget in the product is this component with different data —
 * leads by stage, breakdown by any field, activity by action kind, and the
 * funnel. PROMPTS.md: *"If you write a second similar component, stop and
 * generalise the first."*
 *
 * Built to the dataviz mark specs, and the reasons are worth keeping:
 *
 * - **Horizontal**, because the categories are long-named — stage labels and
 *   arbitrary field values both are — and rotated axis text is unreadable.
 * - **One hue.** A single series whose job is magnitude, so the colour job is
 *   sequential-one-hue. Length carries the number; colour carries nothing, and
 *   is therefore not asked to.
 * - **No legend.** One series needs none; a box with a single swatch just
 *   restates the title.
 * - **Value at the tip, no x-axis.** Direct labels before gridlines. With every
 *   bar labelled there is nothing left for an axis to say.
 * - **A 2px surface gap** separates adjacent bars — the gap does the
 *   separating, never a border, which would add ink that is not data.
 * - **A table view**, because identity must never depend on the chart
 *   rendering at all.
 */

import { useId, useState } from 'react'

import type { Bucket } from '@/api/types'
import { Button } from '@/components/ui/button'

export interface BarChartProps {
  readonly title: string
  readonly subtitle?: string
  readonly buckets: readonly Bucket[]
  readonly loading?: boolean
  readonly error?: string | null
  readonly empty?: string
  /** Click a bar to drill through. The chart's number and the list behind it
   *  have to agree, so this hands back the bucket rather than a label. */
  readonly onSelect?: (bucket: Bucket) => void
}

/** ≤24px, per the mark spec — never fill the slot; the leftover is air. */
const BAR_THICKNESS = 20
const BAR_GAP = 12

export function BarChart({
  title,
  subtitle,
  buckets,
  loading = false,
  error = null,
  empty = 'Nothing to show yet.',
  onSelect,
}: BarChartProps) {
  const [showTable, setShowTable] = useState(false)
  const tableId = useId()

  const max = Math.max(1, ...buckets.map((bucket) => bucket.count))
  const total = buckets.reduce((sum, bucket) => sum + bucket.count, 0)

  return (
    <figure className="viz-root m-0 space-y-3">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-sm font-medium">{title}</h3>
          {subtitle ? <p className="text-muted-foreground text-xs">{subtitle}</p> : null}
        </div>
        {/* Labelled "View as …" rather than "Table". Sitting at the top right
            it lands directly above the value column, which has no header of
            its own — and a bare "Table" there reads as one. Caught by looking
            at the rendered chart, which is the step the validator cannot do. */}
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground -my-1 h-auto py-1 text-xs font-normal"
          aria-expanded={showTable}
          aria-controls={tableId}
          onClick={() => setShowTable((open) => !open)}
        >
          {showTable ? 'View as chart' : 'View as table'}
        </Button>
      </figcaption>

      {error ? (
        <p role="alert" className="text-destructive py-6 text-center text-sm">
          {error}
        </p>
      ) : buckets.length === 0 && !loading ? (
        <p className="text-muted-foreground py-6 text-center text-sm">{empty}</p>
      ) : showTable ? (
        <table id={tableId} className="w-full text-sm">
          <thead className="text-muted-foreground border-b text-left text-xs uppercase">
            <tr>
              <th className="pb-1 font-medium">{title}</th>
              <th className="pb-1 text-right font-medium">Leads</th>
            </tr>
          </thead>
          <tbody>
            {buckets.map((bucket) => (
              <tr key={bucket.key} className="border-b last:border-0">
                <td className="py-1.5">{bucket.label}</td>
                {/* Tabular figures so the column aligns, per the typeface note. */}
                <td className="py-1.5 text-right tabular-nums">{bucket.count.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <ul id={tableId} className="space-y-0" style={{ margin: 0, padding: 0 }}>
          {buckets.map((bucket) => {
            const share = (bucket.count / max) * 100
            const row = (
              <>
                <span className="text-muted-foreground w-28 shrink-0 truncate text-xs sm:w-36">
                  {bucket.label}
                </span>
                <span className="relative min-w-0 flex-1" style={{ height: BAR_THICKNESS }}>
                  <span
                    aria-hidden
                    style={{
                      display: 'block',
                      width: `${Math.max(share, bucket.count > 0 ? 1.5 : 0)}%`,
                      height: '100%',
                      background: 'var(--viz-series)',
                      // Square at the baseline, 4px rounded at the data end.
                      borderRadius: '0 4px 4px 0',
                    }}
                  />
                </span>
                <span className="w-14 shrink-0 text-right text-xs tabular-nums">
                  {bucket.count.toLocaleString()}
                </span>
              </>
            )
            return (
              <li
                key={bucket.key}
                // The 2px surface gap between adjacent bars, done with spacing
                // rather than a stroke around the mark.
                style={{ marginBottom: BAR_GAP, listStyle: 'none' }}
                title={`${bucket.label}: ${bucket.count.toLocaleString()}${
                  total ? ` (${Math.round((bucket.count / total) * 100)}%)` : ''
                }`}
              >
                {onSelect ? (
                  <button
                    type="button"
                    data-testid="bar"
                    // Explicit, because the accessibility tree showed these
                    // buttons with no name at all: the bar itself is
                    // aria-hidden and the surrounding spans did not compose one.
                    // A screen reader announced seven identical "button"s.
                    aria-label={`${bucket.label}: ${bucket.count.toLocaleString()} — show these leads`}
                    onClick={() => onSelect(bucket)}
                    // The hit target is the whole row, not the mark — a short
                    // bar is otherwise almost unclickable.
                    className="hover:bg-muted/60 flex w-full items-center gap-3 rounded-sm px-1 py-0.5 text-left"
                  >
                    {row}
                  </button>
                ) : (
                  <span data-testid="bar" className="flex w-full items-center gap-3 px-1 py-0.5">
                    {row}
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </figure>
  )
}
