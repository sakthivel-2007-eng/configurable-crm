/**
 * The lead list and detail overlay (M5).
 *
 * The list renders the workspace's own H1/H2 headline fields rather than fixed
 * columns, because which field is a lead's headline is a per-workspace setting.
 * Detail opens as an overlay over the list so filter context survives, per the
 * milestone's frontend note.
 *
 * The create form offers `show_in_quick_add` fields plus anything required —
 * a required field missing from quick-add would make every creation fail with a
 * validation error the form never gave you a way to satisfy.
 */

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api, ApiError } from '@/api/client'
import type { LeadField, MemberDetail, Page, WorkspaceDetail } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuth } from '@/features/auth/context'
import { toDisplayStringOr } from '@/lib/format'
import { useFieldTypes, useLeadFields } from '@/features/fields/api'
import { FieldInput } from '@/features/fields/FieldInput'
import { useCreateLead, useLeads, useMessageTemplates } from '@/features/leads/api'
import { LeadDetail } from '@/features/leads/LeadDetail'
import {
  useCustomActions,
  useDispositions,
  useLostReasons,
  useStages,
} from '@/features/pipeline/api'

function message(cause: unknown): string {
  if (!(cause instanceof ApiError)) return 'That action failed.'
  const fields = cause.detail.fields
  if (fields && typeof fields === 'object' && !Array.isArray(fields)) {
    return Object.entries(fields as Record<string, string>)
      .map(([key, problem]) => `${key}: ${problem}`)
      .join(' · ')
  }
  if (cause.code === 'duplicate_identity') return 'A lead with that identifier already exists.'
  if (cause.code === 'identity_required') return 'The workspace identity field needs a value.'
  return cause.message
}

export function LeadsPage() {
  const { activeWorkspaceId } = useAuth()
  const workspaceId = activeWorkspaceId as string

  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const [error, setError] = useState<string | null>(null)

  const leads = useLeads(workspaceId, search)
  const fields = useLeadFields(workspaceId)
  const fieldTypes = useFieldTypes(workspaceId)
  const stages = useStages(workspaceId)
  const lostReasons = useLostReasons(workspaceId)
  const dispositions = useDispositions(workspaceId)
  const templates = useMessageTemplates(workspaceId)
  const createLead = useCreateLead(workspaceId)

  const workspace = useQuery({
    queryKey: ['workspace', workspaceId],
    queryFn: () => api.get<WorkspaceDetail>(`/workspaces/${workspaceId}`),
  })
  const members = useQuery({
    queryKey: ['members', workspaceId],
    queryFn: () =>
      api.get<Page<MemberDetail>>(`/workspaces/${workspaceId}/members`, {
        query: { limit: 100 },
      }),
  })

  // Custom actions may be switched off; the query is allowed to fail and the
  // logger simply does not render.
  const customActions = useCustomActions(workspaceId)

  const allStages = useMemo(() => {
    const pipeline = stages.data
    if (!pipeline) return []
    return [pipeline.initial, ...pipeline.active, pipeline.won, pipeline.lost].filter(
      (stage): stage is NonNullable<typeof stage> => stage !== null,
    )
  }, [stages.data])

  const fieldList = fields.data ?? []
  const quickAddFields = fieldList.filter(
    (field) => !field.is_hidden && (field.show_in_quick_add || field.is_required),
  )

  const rendererFor = (field: LeadField) =>
    fieldTypes.data?.find((spec) => spec.key === field.field_type)?.renderer

  const h1 = fieldList.find((field) => field.id === workspace.data?.primary_field_1_id)
  const h2 = fieldList.find((field) => field.id === workspace.data?.primary_field_2_id)

  const selected = (leads.data?.items ?? []).find((lead) => lead.id === selectedId) ?? null

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Leads</h1>
          <p className="text-muted-foreground text-sm">
            {leads.data?.total ?? 0} lead{leads.data?.total === 1 ? '' : 's'}
          </p>
        </div>
        <div className="flex gap-2">
          <Input
            className="max-w-56"
            placeholder="Search by identifier"
            aria-label="Search leads"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <Button onClick={() => setCreateOpen(true)}>Add lead</Button>
        </div>
      </header>

      {error ? (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">All leads</CardTitle>
        </CardHeader>
        <CardContent>
          <table className="w-full text-sm">
            <thead className="text-muted-foreground border-b text-left text-xs uppercase">
              <tr>
                <th className="pb-2 font-medium">{h1?.label ?? 'Lead'}</th>
                <th className="pb-2 font-medium">{h2?.label ?? 'Identifier'}</th>
                <th className="pb-2 font-medium">Stage</th>
                <th className="pb-2 font-medium">Assignee</th>
                <th className="pb-2 font-medium">Score</th>
              </tr>
            </thead>
            <tbody>
              {(leads.data?.items ?? []).map((lead) => {
                const stage = allStages.find((entry) => entry.id === lead.stage_id)
                const assignee = members.data?.items.find((m) => m.id === lead.assignee_id)
                return (
                  <tr
                    key={lead.id}
                    className="hover:bg-muted/50 cursor-pointer border-b last:border-0"
                    data-testid="lead-row"
                    onClick={() => setSelectedId(lead.id)}
                  >
                    <td className="py-2 font-medium">
                      {toDisplayStringOr(lead.primary.h1, lead.identity_value)}
                    </td>
                    <td className="text-muted-foreground py-2">
                      {toDisplayStringOr(lead.primary.h2)}
                    </td>
                    <td className="py-2">
                      {stage ? (
                        <Badge
                          variant="outline"
                          style={{ borderColor: stage.color, color: stage.color }}
                        >
                          {stage.label}
                        </Badge>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="text-muted-foreground py-2">
                      {assignee?.user.full_name ?? 'Unassigned'}
                    </td>
                    <td className="py-2">{lead.score}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {(leads.data?.items ?? []).length === 0 && !leads.isLoading ? (
            <p className="text-muted-foreground py-6 text-center text-sm">
              No leads yet. Add one to see the timeline and changeset machinery work.
            </p>
          ) : null}
        </CardContent>
      </Card>

      {selected ? (
        <LeadDetail
          workspaceId={workspaceId}
          lead={selected}
          fields={fieldList}
          fieldTypes={fieldTypes.data ?? []}
          stages={allStages}
          lostReasons={lostReasons.data ?? []}
          members={members.data?.items ?? []}
          dispositions={dispositions.data ?? []}
          actionTypes={customActions.data ?? []}
          templates={templates.data ?? []}
          connectedCallMinSeconds={workspace.data?.connected_call_min_seconds ?? 1}
          onClose={() => setSelectedId(null)}
        />
      ) : null}

      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Add a lead"
        description="Quick-add fields plus anything required, rendered from this workspace's own schema."
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={createLead.isPending}
              onClick={() => {
                void (async () => {
                  setError(null)
                  try {
                    await createLead.mutateAsync({ values: draft })
                    setDraft({})
                    setCreateOpen(false)
                  } catch (cause) {
                    setError(message(cause))
                  }
                })()
              }}
            >
              Create lead
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {quickAddFields.map((field) => {
            const renderer = rendererFor(field)
            if (!renderer) return null
            return (
              <div key={field.id} className="space-y-1.5">
                <Label htmlFor={`new-${field.key}`}>
                  {field.label}
                  {field.is_required ? <span className="text-destructive"> *</span> : null}
                </Label>
                <FieldInput
                  field={field}
                  renderer={renderer}
                  inputId={`new-${field.key}`}
                  value={draft[field.key]}
                  onChange={(next) => setDraft((current) => ({ ...current, [field.key]: next }))}
                />
              </div>
            )
          })}
          {quickAddFields.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No fields are marked &ldquo;show in quick add&rdquo;. Turn one on in Field settings.
            </p>
          ) : null}
        </div>
      </Dialog>
    </div>
  )
}
