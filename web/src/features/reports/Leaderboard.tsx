/**
 * The leaderboard (M9).
 *
 * A table, not a chart: several metrics per person is more than seven classes
 * that all carry meaning, which the form table sends to a table.
 *
 * The columns are whatever the workspace turned on. Nothing here decides what a
 * top performer is — `leaderboard_metrics` does, and a team ranking on calls
 * made and one ranking on deals won are both right about their own business.
 */

import type { LeaderboardRow } from '@/api/types'

const LABELS: Record<string, string> = {
  leads: 'Leads',
  won: 'Won',
  calls: 'Calls',
  average_rating: 'Avg rating',
}

export interface LeaderboardProps {
  readonly rows: readonly LeaderboardRow[]
  readonly loading?: boolean
  readonly error?: string | null
}

export function Leaderboard({ rows, loading = false, error = null }: LeaderboardProps) {
  if (error) {
    return (
      <p role="alert" className="text-destructive py-6 text-center text-sm">
        {error}
      </p>
    )
  }
  if (rows.length === 0 && !loading) {
    return (
      <p className="text-muted-foreground py-6 text-center text-sm">
        Nobody has anything recorded in this range.
      </p>
    )
  }

  // Only the metrics that actually appear, in a stable order.
  const columns = Object.keys(LABELS).filter((key) => rows.some((row) => key in row.metrics))

  return (
    <table className="w-full text-sm">
      <thead className="text-muted-foreground border-b text-left text-xs uppercase">
        <tr>
          <th className="pb-2 font-medium">Member</th>
          {columns.map((key) => (
            <th key={key} className="pb-2 text-right font-medium">
              {LABELS[key]}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr
            key={row.membership_id}
            data-testid="leaderboard-row"
            className="border-b last:border-0"
          >
            <td className="py-2">{row.name}</td>
            {columns.map((key) => (
              // Tabular figures: these are columns that must align.
              <td key={key} className="py-2 text-right tabular-nums">
                {(row.metrics[key] ?? 0).toLocaleString()}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
