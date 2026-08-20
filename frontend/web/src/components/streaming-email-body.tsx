"use client"

import { EmailBody } from "@/components/email-body"
import { useSmoothStreamText } from "@/hooks/use-smooth-stream-text"
import type { ChatCitation } from "@/lib/types"
import { cn } from "@/lib/utils"

type StreamingEmailBodyProps = {
  text: string
  streaming?: boolean
  className?: string
  citations?: ChatCitation[]
}

/**
 * Renders an InboxAssistant answer with ChatGPT-style smooth character reveal
 * while tokens are still arriving (or catching up after the stream ends).
 * Citation markers like [1] link to the matching thread when citations are passed.
 */
export const StreamingEmailBody = ({
  text,
  streaming = false,
  className,
  citations,
}: StreamingEmailBodyProps) => {
  const { visible, animating } = useSmoothStreamText(text, streaming)

  return (
    <EmailBody
      text={visible}
      citations={citations}
      className={cn("wrap-anywhere text-foreground dark:text-foreground", className)}
      trailing={
        animating ? (
          <span
            aria-label="Generating answer"
            className="ml-0.5 inline-block h-[1.05em] w-0.5 translate-y-0.5 bg-foreground align-text-bottom motion-safe:animate-pulse"
          />
        ) : null
      }
    />
  )
}
