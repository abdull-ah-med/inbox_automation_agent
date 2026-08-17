"use client"

import type { CSSProperties, ReactNode } from "react"

import { cn } from "@/lib/utils"

type StarBorderProps = {
  children: ReactNode
  className?: string
  color?: string
  speed?: string
  thickness?: number
}

export const StarBorder = ({
  children,
  className,
  color = "#95d5b2",
  speed = "6s",
  thickness = 1,
}: StarBorderProps) => {
  const motionStyle = {
    "--star-color": color,
    "--star-speed": speed,
  } as CSSProperties

  return (
    <div
      className={cn("relative inline-block overflow-hidden rounded-[22px]", className)}
      style={{ padding: `${thickness}px 0`, ...motionStyle }}
    >
      <div
        aria-hidden="true"
        className="star-border-beam star-border-beam-bottom"
      />
      <div
        aria-hidden="true"
        className="star-border-beam star-border-beam-top"
      />
      <div className="relative z-10">{children}</div>
    </div>
  )
}
