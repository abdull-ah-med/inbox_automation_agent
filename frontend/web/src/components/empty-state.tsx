import { cn } from "@/lib/utils"

export const EmptyState = ({
  title,
  description,
  className,
}: {
  title: string
  description: string
  className?: string
}) => {
  return (
    <div
      role="status"
      className={cn(
        "rounded-lg border border-dashed border-gray-300 bg-white px-6 py-12 text-center dark:border-gray-700 dark:bg-gray-900",
        className,
      )}
    >
      <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">{title}</p>
      <p className="mx-auto mt-2 max-w-md text-sm text-gray-500 dark:text-gray-400">
        {description}
      </p>
    </div>
  )
}
