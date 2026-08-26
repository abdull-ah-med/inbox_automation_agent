"use client"

import { useSyncExternalStore } from "react"

const subscribe = () => () => {}

const getClientSnapshot = () => true

const getServerSnapshot = () => false

export const useIsClient = (): boolean =>
  useSyncExternalStore(subscribe, getClientSnapshot, getServerSnapshot)
