"use client"

import Link from "next/link"

import { cn, textLinkClass } from "@/lib/utils"

export const associatedThreadHref = (threadId: string, fromThreadId: string) =>
  `/threads/${threadId}?from=${encodeURIComponent(fromThreadId)}`

export const ThreadOriginBanner = ({
  originId,
  originSubject,
}: {
  originId: string
  originSubject: string
}) => {
  const label = `Back to ${originSubject}`
  return (
    <p className="mb-4">
      <Link
        href={`/threads/${originId}`}
        tabIndex={0}
        aria-label={label}
        className={cn(textLinkClass, "text-sm")}
      >
        ← {label}
      </Link>
    </p>
  )
}
