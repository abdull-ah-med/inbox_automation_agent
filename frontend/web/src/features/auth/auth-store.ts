/**
 * In-memory access-token store. Never persists to localStorage/sessionStorage.
 */

import type { UserMe } from "@/lib/types"

type Listener = () => void

export interface AuthState {
  accessToken: string | null
  expiresAt: number | null
  user: UserMe | null
}

let state: AuthState = {
  accessToken: null,
  expiresAt: null,
  user: null,
}

const listeners = new Set<Listener>()

function emit() {
  for (const listener of listeners) listener()
}

export function getAuthState(): AuthState {
  return state
}

export function subscribeAuth(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function setAuthSession(params: {
  accessToken: string
  expiresIn: number
  user: UserMe
}): void {
  state = {
    accessToken: params.accessToken,
    expiresAt: Date.now() + params.expiresIn * 1000,
    user: params.user,
  }
  emit()
}

export function clearAuthSession(): void {
  state = { accessToken: null, expiresAt: null, user: null }
  emit()
}

export function getAccessToken(): string | null {
  return state.accessToken
}
