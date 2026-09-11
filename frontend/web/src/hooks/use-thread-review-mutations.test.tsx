import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import { type Dispatch, type ReactNode, type SetStateAction } from "react"
import { describe, expect, it, vi } from "vitest"

import { useThreadReviewMutations } from "@/hooks/use-thread-review-mutations"
import type { DraftView } from "@/lib/types"

const resolveMock = vi.fn()
const rejectMock = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    threads: {
      resolve: (...args: unknown[]) => resolveMock(...args),
    },
    drafts: {
      approve: vi.fn(),
      reject: (...args: unknown[]) => rejectMock(...args),
      markWrong: vi.fn(),
    },
  },
}))

const draft: DraftView = {
  id: "draft-1",
  subject: "Re: Notice",
  body: "Thanks",
  forward_to: null,
  teaching_note: "",
  urgency: "NORMAL",
  urgency_reason: null,
  created_at: new Date().toISOString(),
  suggested_actions: [],
  approved_at: null,
  rejected_at: null,
  edited_body: null,
  feedback_note: null,
  feedback_action: null,
  feedback_reason_code: null,
  routing_category: null,
  approval_note: null,
  approval_scope: null,
  applied_skills: [],
  tool_calls: null,
  correct_actions: [],
}

const renderMutations = (
  resolveActionsTaken: string,
  resolveInvolved: string,
  overrides: {
    rejectNote?: string
    rejectReason?: "" | "incomplete" | "wrong_action" | "tone" | "other"
    processNote?: string
    setRewriteOpen?: Dispatch<SetStateAction<boolean>>
    setRewriteInstruction?: Dispatch<SetStateAction<string>>
    hangInvalidate?: boolean
  } = {},
) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  if (overrides.hangInvalidate) {
    vi.spyOn(queryClient, "invalidateQueries").mockImplementation(() => new Promise(() => {}))
  }
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )

  const setRewriteOpen = overrides.setRewriteOpen ?? vi.fn<Dispatch<SetStateAction<boolean>>>()
  const setRewriteInstruction =
    overrides.setRewriteInstruction ?? vi.fn<Dispatch<SetStateAction<string>>>()

  const { result } = renderHook(
    () =>
      useThreadReviewMutations({
        threadId: "thread-1",
        draft,
        queryClient,
        approveBody: draft.body,
        approvalNote: "",
        approvalScope: "",
        rejectNote: overrides.rejectNote ?? "",
        rejectReason: overrides.rejectReason ?? "",
        processNote: overrides.processNote ?? "",
        setApproveOpen: vi.fn(),
        setApprovalNote: vi.fn(),
        setApprovalScope: vi.fn(),
        setRejectOpen: vi.fn(),
        setRejectNote: vi.fn(),
        setRejectReason: vi.fn(),
        setProcessNote: vi.fn(),
        setRewriteOpen,
        setRewriteInstruction,
        setActionError: vi.fn(),
        setResolvePromptOpen: vi.fn(),
        resolveActionsTaken,
        resolveInvolved,
        setResolveActionsTaken: vi.fn(),
        setResolveInvolved: vi.fn(),
        maybePromptResolve: vi.fn(),
        siblings: {
          open: false,
          setOpen: vi.fn(),
          items: [],
          treatment: "no_reply",
          reason: "",
          urgency: undefined,
          prompt: vi.fn(),
        },
      }),
    { wrapper },
  )

  return { result, setRewriteOpen, setRewriteInstruction }
}

describe("useThreadReviewMutations resolve", () => {
  it("sends actions_taken and involved when confirming resolve", async () => {
    resolveMock.mockResolvedValue({ state: "RESOLVED" })
    const { result } = renderMutations("Checked portal and logged the change", "accounting team")

    result.current.handleConfirmResolve()

    await waitFor(() => {
      expect(resolveMock).toHaveBeenCalledWith("thread-1", {
        actions_taken: "Checked portal and logged the change",
        involved: "accounting team",
      })
    })
  })

  it("omits involved when the field is blank", async () => {
    resolveMock.mockResolvedValue({ state: "RESOLVED" })
    const { result } = renderMutations("Closed the loop in portal", "   ")

    result.current.handleConfirmResolve()

    await waitFor(() => {
      expect(resolveMock).toHaveBeenCalledWith("thread-1", {
        actions_taken: "Closed the loop in portal",
        involved: null,
      })
    })
  })
})

describe("useThreadReviewMutations reject latency", () => {
  it("opens rewrite prompt without waiting for queue refresh", async () => {
    rejectMock.mockResolvedValue(draft)
    const setRewriteOpen = vi.fn<Dispatch<SetStateAction<boolean>>>()
    const setRewriteInstruction = vi.fn<Dispatch<SetStateAction<string>>>()
    const { result } = renderMutations("unused", "", {
      rejectNote: "DraftAssistant drafted a letter. This is an SampleLab invoice.",
      rejectReason: "incomplete",
      processNote:
        "In this case I would process the SampleLab invoice and tell Beau the rebill is with Harmeyer.",
      setRewriteOpen,
      setRewriteInstruction,
      hangInvalidate: true,
    })

    result.current.handleReject()

    await waitFor(() => {
      expect(setRewriteOpen).toHaveBeenCalledWith(true)
    })
    expect(setRewriteInstruction).toHaveBeenCalled()
  })
})
