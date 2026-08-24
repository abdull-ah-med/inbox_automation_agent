import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { RecurrenceBanner } from "@/components/recurrence-banner"
import type { ActivityEntry } from "@/lib/types"

const urgencyFeedbackMock = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    threads: {
      urgencyFeedback: (...args: unknown[]) => urgencyFeedbackMock(...args),
    },
  },
}))

const THREAD_ID = "a1111111-1111-1111-1111-111111111111"

const escalated: ActivityEntry = {
  title: "Urgency raised for recurring alert",
  body: "Urgency bumped automatically: 3 similar alerts in 48h (same sender and subject). Urgency raised to CRITICAL.",
  actor_kind: "agent",
  event_type: "thread.urgency.recurrence_escalated",
  timestamp: "2026-08-21T16:00:00Z",
}

const markedWrong: ActivityEntry = {
  title: "Automatic urgency bump marked wrong",
  body: "We reverted this thread and will not auto-bump this alert fingerprint again.",
  actor_kind: "elise",
  event_type: "thread.urgency.recurrence_wrong",
  timestamp: "2026-08-21T16:05:00Z",
}

const renderBanner = (activity: ActivityEntry[]) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <RecurrenceBanner threadId={THREAD_ID} activity={activity} />
    </QueryClientProvider>,
  )
}

describe("RecurrenceBanner", () => {
  beforeEach(() => {
    urgencyFeedbackMock.mockReset()
  })

  it("explains the automatic bump and posts Mark as wrong", async () => {
    urgencyFeedbackMock.mockResolvedValue({
      state: "DRAFTED",
      action: "wrong_escalation",
    })
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderBanner([escalated])

    expect(screen.getByRole("status")).toHaveTextContent(/similar alerts/i)
    expect(screen.getByRole("status")).toHaveTextContent(/automatically/i)
    expect(screen.getByRole("status")).toHaveTextContent(/CRITICAL/)

    await user.click(
      screen.getByRole("button", { name: "Mark automatic urgency bump as wrong" }),
    )

    await waitFor(() => {
      expect(urgencyFeedbackMock).toHaveBeenCalledWith(THREAD_ID, {
        action: "wrong_escalation",
      })
    })
    expect(screen.getByRole("status")).toHaveTextContent(/marked wrong/i)
  })

  it("does not show after a later marked-wrong activity", () => {
    renderBanner([escalated, markedWrong])
    expect(
      screen.queryByRole("button", { name: "Mark automatic urgency bump as wrong" }),
    ).toBeNull()
    expect(screen.queryByText(/similar alerts/i)).toBeNull()
  })

  it("dismiss hides the banner without calling the API", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderBanner([escalated])
    await user.click(screen.getByRole("button", { name: "Dismiss automatic urgency banner" }))
    expect(screen.queryByRole("status")).toBeNull()
    expect(urgencyFeedbackMock).not.toHaveBeenCalled()
  })
})
