"use client"

import { MessageCircleDashedIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"

export const EXAMPLE_ASKS = [
  "What should I focus on today?",
  "Billing disputes waiting on review",
  "Threads about SampleLab",
] as const

type InboxAssistantEmptyStateProps = {
  onExampleAsk: (prompt: string) => void
}

export const InboxAssistantEmptyState = ({ onExampleAsk }: InboxAssistantEmptyStateProps) => (
  <Empty className="h-full border-0">
    <EmptyHeader>
      <EmptyMedia variant="icon">
        <MessageCircleDashedIcon />
      </EmptyMedia>
      <EmptyTitle>Ask about the inbox</EmptyTitle>
      <EmptyDescription>
        InboxAssistant finds matching threads and cites them so you can jump into review. It never sends
        mail.
      </EmptyDescription>
    </EmptyHeader>
    <EmptyContent>
      <div className="flex flex-wrap justify-center gap-2">
        {EXAMPLE_ASKS.map((prompt) => (
          <Button
            key={prompt}
            type="button"
            variant="outline"
            size="sm"
            className="min-h-8 rounded-full"
            onClick={() => {
              onExampleAsk(prompt)
            }}
          >
            {prompt}
          </Button>
        ))}
      </div>
    </EmptyContent>
  </Empty>
)
