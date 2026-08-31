import { describe, expect, it, beforeEach } from "vitest"

import {
  chatSessionStorageKey,
  clearStoredSessionId,
  readStoredSessionId,
  writeStoredSessionId,
} from "@/lib/chat-session-storage"

describe("chatSessionStorage", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it("stores and reads a session id per mailbox key", () => {
    expect(chatSessionStorageKey("")).toBe("inboxassistant_session_all")
    expect(chatSessionStorageKey("sales@example.com")).toBe("inboxassistant_session_sales@example.com")
    writeStoredSessionId("sales@example.com", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    expect(readStoredSessionId("sales@example.com")).toBe("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    clearStoredSessionId("sales@example.com")
    expect(readStoredSessionId("sales@example.com")).toBeNull()
  })
})
