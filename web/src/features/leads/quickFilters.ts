/**
 * The shape of the one-click filters above the grid.
 *
 * Split from the component for the same reason `columns.ts` is: a module that
 * exports both constants and components breaks React Fast Refresh.
 *
 * These are deliberately *not* filter-DSL nodes. §6.1 defines a field rule as
 * referencing `lead_fields.key`, and stage and assignee are columns on `leads`
 * rather than fields any workspace defined — so they travel as query
 * parameters beside the filter document, which is how the API contract splits
 * them too.
 */

export interface QuickFilters {
  /** One stage at a time: chips are a shortcut, not a second filter builder. */
  readonly stageId: string | null
  readonly mine: boolean
  readonly unassigned: boolean
}

export const NO_QUICK_FILTERS: QuickFilters = {
  stageId: null,
  mine: false,
  unassigned: false,
}
