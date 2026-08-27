"use client"

import { Maximize2Icon, Minimize2Icon, RotateCwIcon, X } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

type InboxAssistantPanelHeaderProps = {
  canShrink: boolean
  canGrow: boolean
  isAsking: boolean
  onShrink: () => void
  onGrow: () => void
  onReset: () => void
  onClose: () => void
}

export const InboxAssistantPanelHeader = ({
  canShrink,
  canGrow,
  isAsking,
  onShrink,
  onGrow,
  onReset,
  onClose,
}: InboxAssistantPanelHeaderProps) => (
  <header className="border-border flex shrink-0 items-start gap-3 border-b px-4 py-3">
    <div className="min-w-0 flex-1">
      <h2 className="text-foreground text-sm font-semibold tracking-tight">InboxAssistant</h2>
      <p className="text-muted-foreground text-xs">Read-only · cites matching threads</p>
    </div>
    <div className="flex shrink-0 items-center gap-1">
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Make InboxAssistant smaller"
              disabled={!canShrink}
              onClick={onShrink}
            >
              <Minimize2Icon />
            </Button>
          }
        />
        <TooltipContent>
          <p>Smaller</p>
        </TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Make InboxAssistant larger"
              disabled={!canGrow}
              onClick={onGrow}
            >
              <Maximize2Icon />
            </Button>
          }
        />
        <TooltipContent>
          <p>Larger</p>
        </TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="outline"
              size="icon-sm"
              aria-label="Reset conversation"
              disabled={isAsking}
              onClick={onReset}
            >
              <RotateCwIcon />
            </Button>
          }
        />
        <TooltipContent>
          <p>Reset</p>
        </TooltipContent>
      </Tooltip>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="Close InboxAssistant"
        onClick={onClose}
      >
        <X className="size-4" aria-hidden="true" />
      </Button>
    </div>
  </header>
)
