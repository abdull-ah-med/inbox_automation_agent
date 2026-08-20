"use client"

import { EmailBody } from "@/components/email-body"
import { useSmoothStreamText } from "@/hooks/use-smooth-stream-text"
import { cn } from "@/lib/utils"

type StreamingEmailBodyProps = {
  text: string
  streaming?: boolean
  className?: string
}

/**
 * Renders an InboxAssistant answer with ChatGPT-style smooth character reveal
 * while tokens are still arriving (or catching up after the stream ends).
 */
export const StreamingEmailBody = ({
  text,
  streaming = false,
  className,
}: StreamingEmailBodyProps) => {
  const { visible, animating } = useSmoothStreamText(text, streaming)

  return (
    <EmailBody
      text={visible}
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
