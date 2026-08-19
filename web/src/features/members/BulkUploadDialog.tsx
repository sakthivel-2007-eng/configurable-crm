import { useState } from 'react'

import { ApiError } from '@/api/client'
import type { BulkUploadReport } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useBulkUpload } from '@/features/members/api'

/**
 * Excel member upload, dry run first.
 *
 * The preview is not optional in the UI even though the API allows skipping
 * it: importing forty people with the wrong template is not something you want
 * to discover afterwards, and M1 has no undo (changesets arrive in M5).
 */
interface Props {
  readonly workspaceId: string
  readonly open: boolean
  readonly onClose: () => void
}

export function BulkUploadDialog({ workspaceId, open, onClose }: Props) {
  const upload = useBulkUpload(workspaceId)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<BulkUploadReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  function reset() {
    setFile(null)
    setPreview(null)
    setError(null)
    onClose()
  }

  async function run(dryRun: boolean) {
    if (!file) return
    setError(null)
    try {
      const report = await upload.mutateAsync({ file, dryRun })
      if (dryRun) {
        setPreview(report)
      } else {
        reset()
      }
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Could not read that file.')
    }
  }

  return (
    <Dialog
      open={open}
      onClose={reset}
      title="Upload members"
      description="An .xlsx with columns: email, full_name, template, and optionally manager_email and license."
      footer={
        <>
          <Button variant="ghost" onClick={reset}>
            Cancel
          </Button>
          {preview ? (
            <Button
              onClick={() => void run(false)}
              disabled={upload.isPending || preview.created === 0}
            >
              {upload.isPending ? 'Importing…' : `Import ${preview.created} members`}
            </Button>
          ) : (
            <Button onClick={() => void run(true)} disabled={!file || upload.isPending}>
              {upload.isPending ? 'Checking…' : 'Preview'}
            </Button>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <Label htmlFor="members-file">Workbook</Label>
        <Input
          id="members-file"
          type="file"
          accept=".xlsx"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null)
            setPreview(null)
          }}
        />
      </div>

      {preview ? (
        <div className="flex flex-col gap-3">
          <div className="flex gap-2">
            <Badge variant="success">{preview.created} to create</Badge>
            {preview.skipped > 0 ? (
              <Badge variant="secondary">{preview.skipped} already members</Badge>
            ) : null}
            {preview.errored > 0 ? (
              <Badge variant="destructive">{preview.errored} with errors</Badge>
            ) : null}
          </div>

          <div className="max-h-64 overflow-y-auto rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 sticky top-0">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Row</th>
                  <th className="px-3 py-2 text-left font-medium">Email</th>
                  <th className="px-3 py-2 text-left font-medium">Result</th>
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row) => (
                  <tr key={row.row_number} className="border-t">
                    <td className="px-3 py-1.5">{row.row_number}</td>
                    <td className="px-3 py-1.5">{row.email ?? '—'}</td>
                    <td className="px-3 py-1.5">
                      {row.status === 'error' ? (
                        <span className="text-destructive">{row.message}</span>
                      ) : (
                        <span className="text-muted-foreground">{row.status}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      ) : null}
    </Dialog>
  )
}
