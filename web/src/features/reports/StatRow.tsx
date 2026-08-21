/**
 * Follow-up counts (M9).
 *
 * **Not a chart.** Three headline numbers are a KPI row of stat tiles; a
 * three-bar bar chart of them would be a chart drawn because charts are
 * available. The dataviz form table names this case explicitly.
 *
 * The numbers use proportional figures — `tabular-nums` is for columns that
 * must align vertically, which these do not.
 */

import type { FollowUpCounts } from '@/api/types'
import { Card, CardContent } from '@/components/ui/card'

export interface StatRowProps {
  readonly counts: FollowUpCounts | undefined
  readonly loading?: boolean
  readonly error?: string | null
}

export function StatRow({ counts, loading = false, error = null }: StatRowProps) {
  if (error) {
    return (
      <p role="alert" className="text-destructive text-sm">
        {error}
      </p>
    )
  }

  const tiles = [
    {
      key: 'late',
      label: 'Late',
      value: counts?.late ?? 0,
      hint: 'Overdue follow-ups, right now',
      // Status is carried by an icon *and* a label, never by colour alone —
      // and only when there is actually something wrong.
      urgent: (counts?.late ?? 0) > 0,
    },
    {
      key: 'upcoming',
      label: 'Upcoming',
      value: counts?.upcoming ?? 0,
      hint: 'Scheduled and not yet due',
      urgent: false,
    },
    {
      key: 'never',
      label: 'Never contacted',
      value: counts?.never_contacted ?? 0,
      hint: 'No timeline entry at all',
      urgent: false,
    },
  ]

  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {tiles.map((tile) => (
        <Card key={tile.key} data-testid="stat-tile">
          <CardContent className="pt-6">
            <div className="text-muted-foreground flex items-center gap-1.5 text-xs uppercase">
              {tile.urgent ? (
                <span className="text-destructive" aria-hidden>
                  ▲
                </span>
              ) : null}
              {tile.label}
            </div>
            <div className="mt-1 text-3xl font-semibold">
              {loading ? '—' : tile.value.toLocaleString()}
            </div>
            <p className="text-muted-foreground mt-1 text-xs">{tile.hint}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
