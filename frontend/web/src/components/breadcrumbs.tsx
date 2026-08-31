"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"

import { cn, textLinkClass } from "@/lib/utils"

export type BreadcrumbItem = {
  label: string
  href?: string
}

export const Breadcrumbs = ({ items }: { items: BreadcrumbItem[] }) => {
  const router = useRouter()

  if (items.length === 0) return null

  const parent = items
    .slice(0, -1)
    .toReversed()
    .find((item) => Boolean(item.href))

  const handleBack = () => {
    if (parent?.href) {
      router.push(parent.href)
      return
    }
    router.back()
  }

  const handleBackKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleBack()
    }
  }

  return (
    <div className="mb-4 flex flex-wrap items-center gap-3">
      {parent?.href || items.length > 1 ? (
        <button
          type="button"
          tabIndex={0}
          aria-label={parent ? `Back to ${parent.label}` : "Go back"}
          className={cn(textLinkClass, "text-sm")}
          onClick={handleBack}
          onKeyDown={handleBackKeyDown}
        >
          ← Back
        </button>
      ) : null}
      <nav aria-label="Breadcrumb" className="min-w-0">
        <ol className="flex flex-wrap items-center gap-1.5 text-sm">
          {items.map((item, index) => {
            const isLast = index === items.length - 1
            const key = `${item.label}-${index}`

            return (
              <li key={key} className="flex min-w-0 items-center gap-1.5">
                {index > 0 ? (
                  <span aria-hidden="true" className="text-gray-300 dark:text-gray-600">
                    /
                  </span>
                ) : null}
                {item.href && !isLast ? (
                  <Link
                    href={item.href}
                    tabIndex={0}
                    aria-label={item.label}
                    className={cn(textLinkClass, "truncate")}
                  >
                    {item.label}
                  </Link>
                ) : (
                  <span
                    aria-current={isLast ? "page" : undefined}
                    className={
                      isLast
                        ? "truncate font-medium text-gray-900 dark:text-gray-100"
                        : "truncate text-gray-500 dark:text-gray-400"
                    }
                    title={item.label}
                  >
                    {item.label}
                  </span>
                )}
              </li>
            )
          })}
        </ol>
      </nav>
    </div>
  )
}
