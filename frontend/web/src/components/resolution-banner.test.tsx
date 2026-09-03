import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ResolutionBanner } from "@/components/resolution-banner"
import type { ActivityEntry, ThreadPresentation } from "@/lib/types"

const resolutionFeedbackMock = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    threads: {
      resolutionFeedback: (...args: unknown[]) => resolutionFeedbackMock(...args),
    },
  },
}))

const resolvedPresentation: ThreadPresentation = {
  is_finished: true,
  open_work: false,
  in_needs_attention: false,
  urgency_assessed: "LOW",
  urgency_active: false,
  badges_now: [],
  triage_history: {
    has_action_items: false,
    needs_context: false,
    is_spam: false,
    spam_reason: null,
    context_reason: null,
  },
  suggest_resolve_default: false,
  show_resolution_banner: true,
}

const renderBanner = ({
  presentation = resolvedPresentation,
  activity = [],
}: {
  presentation?: ThreadPresentation | null
  activity?: ActivityEntry[]
} = {}) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ResolutionBanner
        threadId="a1111111-1111-1111-1111-111111111111"
        presentation={presentation}
        urgencyAssessed="LOW"
        activity={activity}
      />
    </QueryClientProvider>,
  )
}

describe("ResolutionBanner", () => {
  beforeEach(() => {
    resolutionFeedbackMock.mockReset()
  })

  it("confirms reopen inline after Still open succeeds", async () => {
    resolutionFeedbackMock.mockResolvedValue({
      state: "DRAFTED",
      action: "reopen",
    })
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderBanner()

    await user.click(screen.getByRole("button", { name: "Mark thread still open" }))

    const status = await screen.findByRole("status")
    expect(status).toHaveTextContent(/reopened/i)
    expect(status).toHaveTextContent(/Needs Attention/i)
    expect(screen.queryByRole("button", { name: "Mark thread still open" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Wrong auto-resolve reason" })).toBeNull()
  })

  it("keeps the reopen confirmation after the resolved banner flag clears", async () => {
    resolutionFeedbackMock.mockResolvedValue({
      state: "DRAFTED",
      action: "reopen",
    })
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const { rerender } = render(
      <QueryClientProvider client={client}>
        <ResolutionBanner
          threadId="a1111111-1111-1111-1111-111111111111"
          presentation={resolvedPresentation}
          urgencyAssessed="LOW"
          activity={[]}
        />
      </QueryClientProvider>,
    )

    await user.click(screen.getByRole("button", { name: "Mark thread still open" }))
    await screen.findByText(/reopened/i)

    rerender(
      <QueryClientProvider client={client}>
        <ResolutionBanner
          threadId="a1111111-1111-1111-1111-111111111111"
          presentation={{ ...resolvedPresentation, show_resolution_banner: false }}
          urgencyAssessed="LOW"
          activity={[]}
        />
      </QueryClientProvider>,
    )

    expect(screen.getByRole("status")).toHaveTextContent(/reopened/i)
  })

  it("confirms wrong-reason feedback inline after Wrong reason succeeds", async () => {
    resolutionFeedbackMock.mockResolvedValue({
      state: "RESOLVED",
      action: "wrong_reason",
    })
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderBanner()

    await user.click(screen.getByRole("button", { name: "Wrong auto-resolve reason" }))

    const status = await screen.findByRole("status")
    expect(status).toHaveTextContent(/recorded/i)
    expect(status).toHaveTextContent(/reason was wrong/i)
    expect(screen.queryByRole("button", { name: "Wrong auto-resolve reason" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Mark thread still open" })).toBeNull()
  })

  it("shows wrong-reason confirmation from activity after reload without action buttons", () => {
    renderBanner({
      presentation: { ...resolvedPresentation, show_resolution_banner: false },
      activity: [
        {
          title: "Resolution reason corrected",
          body: "Thanks — we recorded that the auto-resolve reason was wrong.",
          actor_kind: "elise",
          event_type: "thread.resolved.wrong_reason",
          timestamp: "2026-08-21T20:00:00Z",
        },
      ],
    })

    const status = screen.getByRole("status")
    expect(status).toHaveTextContent(/recorded/i)
    expect(status).toHaveTextContent(/reason was wrong/i)
    expect(screen.queryByRole("button", { name: "Wrong auto-resolve reason" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Mark thread still open" })).toBeNull()
  })

  it("shows manual resolve copy without wrong-reason feedback", () => {
    renderBanner({
      presentation: { ...resolvedPresentation, resolution_mode: "manual" },
      activity: [
        {
          title: "Marked resolved",
          body: "You marked this thread resolved. Removed from Needs Attention. Assessed urgency was HIGH; it no longer drives priority. Actions taken: normal gmail alert",
          actor_kind: "elise",
          event_type: "thread.resolved.reviewer",
          timestamp: "2026-09-03T11:00:00Z",
        },
      ],
    })

    const status = screen.getByRole("status")
    expect(status).toHaveTextContent(/resolved by elise/i)
    expect(status).toHaveTextContent(/removed from needs attention/i)
    expect(status).toHaveTextContent(/actions taken: normal gmail alert/i)
    expect(screen.queryByRole("button", { name: "Wrong auto-resolve reason" })).toBeNull()
    expect(screen.getByRole("button", { name: "Mark thread still open" })).toBeInTheDocument()
  })

  it("shows an inline error when wrong-reason feedback fails", async () => {
    resolutionFeedbackMock.mockRejectedValue(new Error("Network down"))
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderBanner()

    await user.click(screen.getByRole("button", { name: "Wrong auto-resolve reason" }))

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Network down")
    })
    expect(screen.getByRole("status")).toHaveTextContent(/resolved by draftassistant/i)
  })

  it("shows Resolved by DraftAssistant copy and the stored reason for NO_ACTION threads", () => {
    renderBanner({
      presentation: {
        ...resolvedPresentation,
        disposition: "resolved_draftassistant",
        resolution_mode: "auto",
        resolution_summary: "Courtesy close — sender thanked you; no reply needed.",
      },
    })
    const status = screen.getByRole("status")
    expect(status).toHaveTextContent(/resolved by draftassistant/i)
    expect(status).toHaveTextContent(/courtesy close — sender thanked you; no reply needed/i)
  })

  it("shows Resolved by Elise copy for manual provenance", () => {
    renderBanner({
      presentation: {
        ...resolvedPresentation,
        disposition: "resolved_elise",
        resolution_mode: "manual",
      },
      activity: [
        {
          title: "Marked resolved",
          body: "You marked this thread resolved. Removed from Needs Attention. Actions taken: checked portal",
          actor_kind: "elise",
          event_type: "thread.resolved.reviewer",
          timestamp: "2026-09-03T11:00:00Z",
        },
      ],
    })
    const status = screen.getByRole("status")
    expect(status).toHaveTextContent(/resolved by elise/i)
    expect(status).toHaveTextContent(/checked portal/i)
    expect(screen.queryByRole("button", { name: "Wrong auto-resolve reason" })).toBeNull()
  })
})
