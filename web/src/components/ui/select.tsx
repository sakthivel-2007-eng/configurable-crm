import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * A native `<select>`, styled to match the rest of the kit.
 *
 * Deliberately not Radix: M1 needs a plain picker for templates, managers and
 * availability. The searchable, virtualised combobox the lead filters want
 * arrives with M6, when there is something to search.
 */
function Select({ className, ...props }: React.ComponentProps<'select'>) {
  return (
    <select
      data-slot="select"
      className={cn(
        'border-input bg-background flex h-9 w-full rounded-md border px-3 py-1 text-sm shadow-xs transition-[color,box-shadow] outline-none',
        'focus-visible:ring-ring/50 focus-visible:border-ring focus-visible:ring-[3px]',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  )
}

export { Select }
