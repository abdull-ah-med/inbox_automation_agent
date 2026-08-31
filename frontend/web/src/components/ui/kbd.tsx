import type { ComponentProps } from "react"

import { cn } from "@/lib/utils"

function Kbd({ className, ...props }: ComponentProps<"kbd">) {
  return (
    <kbd
      data-slot="kbd"
      className={cn(
        "pointer-events-none inline-flex h-5 min-w-5 items-center justify-center gap-0.5 rounded-sm border border-border bg-muted px-1 font-sans text-[10px] font-medium text-muted-foreground select-none",
        className,
      )}
      {...props}
    />
  )
}

function KbdGroup({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="kbd-group"
      className={cn("inline-flex items-center gap-0.5", className)}
      {...props}
    />
  )
}

export { Kbd, KbdGroup }
