import type { ReactNode } from "react"

import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"

export const EmptyState = ({
  title,
  description,
  action,
  className,
}: {
  title: string
  description: string
  action?: ReactNode
  className?: string
}) => {
  return (
    <Card role="status" className={cn("py-12 text-center", className)}>
      <CardHeader className="px-6">
        <CardTitle className="text-sm font-semibold">{title}</CardTitle>
        <CardDescription className="mx-auto max-w-md">{description}</CardDescription>
      </CardHeader>
      {action ? <div className="px-6">{action}</div> : null}
    </Card>
  )
}
