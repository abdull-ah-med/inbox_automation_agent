"use client"

import { motion, useReducedMotion } from "framer-motion"
import type { ReactNode } from "react"

export function PageTransition({ children }: { children: ReactNode }) {
  const reduceMotion = useReducedMotion()
  return (
    <motion.div
      initial={reduceMotion ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={
        reduceMotion
          ? { duration: 0 }
          : { duration: 0.15, ease: "easeOut" }
      }
    >
      {children}
    </motion.div>
  )
}

export function CardMount({
  children,
  index = 0,
}: {
  children: ReactNode
  index?: number
}) {
  const reduceMotion = useReducedMotion()
  return (
    <motion.div
      className="h-full"
      initial={reduceMotion ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={
        reduceMotion
          ? { duration: 0 }
          : { duration: 0.15, delay: index * 0.03, ease: "easeOut" }
      }
    >
      {children}
    </motion.div>
  )
}
