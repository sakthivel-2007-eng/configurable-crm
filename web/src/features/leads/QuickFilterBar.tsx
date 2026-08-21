/**
 * One-click filters above the grid (M6).
 *
 * These are the two questions everyone asks constantly — "what's in this
 * stage?" and "what's mine?" — and neither is expressible as a filter rule:
 * §6.1 defines a field rule as referencing `lead_fields.key`, while stage and
 * assignee are columns on `leads`. So they ride alongside the DSL as query
 * parameters, which is exactly how the API contract splits them.
 *
 * The stage chips are the *workspace's* stages, read from the pipeline
 * endpoint. Nothing here knows what any of them are called.
 */

import type { Stage } from '@/api/types'
import { NO_QUICK_FILTERS, type QuickFilters } from '@/features/leads/quickFilters'
import { cn } from '@/lib/utils'

export interface QuickFilterBarProps {
  readonly stages: readonly Stage[]
  readonly value: QuickFilters
  readonly onChange: (next: QuickFilters) => void
}

function Chip({
  active,
  label,
  color,
  onClick,
}: {
  readonly active: boolean
  readonly label: string
  readonly color?: string
  readonly onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'rounded-full border px-3 py-1 text-sm transition-colors',
        active ? 'bg-foreground text-background border-transparent' : 'hover:bg-muted',
      )}
      style={!active && color ? { borderColor: color, color } : undefined}
    >
      {label}
    </button>
  )
}

export function QuickFilterBar({ stages, value, onChange }: QuickFilterBarProps) {
  const noneActive = value.stageId === null && !value.mine && !value.unassigned

  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Quick filters">
      <Chip active={noneActive} label="All" onClick={() => onChange(NO_QUICK_FILTERS)} />

      {stages.map((stage) => (
        <Chip
          key={stage.id}
          active={value.stageId === stage.id}
          label={stage.label}
          color={stage.color}
          // Clicking the active chip clears it, so the same control both
          // applies and removes the filter.
          onClick={() =>
            onChange({ ...value, stageId: value.stageId === stage.id ? null : stage.id })
          }
        />
      ))}

      <span className="bg-border mx-1 h-5 w-px" aria-hidden />

      <Chip
        active={value.mine}
        label="Assigned to me"
        // Mutually exclusive with unassigned: a lead cannot be both.
        onClick={() => onChange({ ...value, mine: !value.mine, unassigned: false })}
      />
      <Chip
        active={value.unassigned}
        label="Unassigned"
        onClick={() => onChange({ ...value, unassigned: !value.unassigned, mine: false })}
      />
    </div>
  )
}
