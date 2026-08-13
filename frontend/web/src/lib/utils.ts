import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Reset native button chrome and keep a 1px underline that matches the text. */
export const textLinkClass =
  "inline-flex appearance-none items-center border-0 bg-transparent p-0 text-blue-600 no-underline decoration-blue-600/40 decoration-1 underline-offset-2 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring dark:text-blue-400 dark:decoration-blue-400/40"

export const textActionClass =
  "inline-flex appearance-none items-center border-0 bg-transparent p-0 no-underline outline-none focus-visible:ring-2 focus-visible:ring-ring"
