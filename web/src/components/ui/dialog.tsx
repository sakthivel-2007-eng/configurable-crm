import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * A minimal modal built on `<dialog>`.
 *
 * The browser element gives focus trapping, Escape-to-close and the top layer
 * for free — all of which a hand-rolled overlay gets wrong.
 */
interface DialogProps {
  readonly open: boolean
  readonly onClose: () => void
  readonly title: string
  readonly description?: string
  readonly children: React.ReactNode
  readonly footer?: React.ReactNode
  /**
   * Extra classes for the dialog element. The field drawer and the permission
   * matrix need more than the default `max-w-lg`; everything else is happy
   * with it, so the width stays a caller's decision rather than a new variant.
   */
  readonly className?: string
}

function Dialog({ open, onClose, title, description, children, footer, className }: DialogProps) {
  const ref = React.useRef<HTMLDialogElement>(null)

  React.useEffect(() => {
    const element = ref.current
    if (!element) return

    if (open && !element.open) {
      element.showModal()
    } else if (!open && element.open) {
      element.close()
    }
  }, [open])

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      className={cn(
        'bg-background text-foreground w-full max-w-lg rounded-lg border p-0 shadow-lg',
        'open:animate-in backdrop:bg-black/50',
        className,
      )}
    >
      <div className="flex flex-col gap-4 p-6">
        <header className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold">{title}</h2>
          {description ? <p className="text-muted-foreground text-sm">{description}</p> : null}
        </header>
        <div className="flex flex-col gap-4">{children}</div>
        {footer ? <footer className="flex justify-end gap-2">{footer}</footer> : null}
      </div>
    </dialog>
  )
}

export { Dialog }
