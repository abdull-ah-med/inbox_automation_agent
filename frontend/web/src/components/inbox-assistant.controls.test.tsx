import { screen, act, fireEvent, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  askChat,
  deliverStream,
  groundedResponse,
  renderWithClient,
  resetInboxAssistantMocks,
} from "@/components/inbox-assistant.test-helpers"

vi.mock("@/lib/api-client", async () => {
  const helpers = await import("@/components/inbox-assistant.test-helpers")
  return {
    api: {
      chat: {
        askStream: (...args: unknown[]) => helpers.askChat(...args),
        createSession: (...args: unknown[]) => helpers.createSession(...args),
        getSession: (...args: unknown[]) => helpers.getSession(...args),
      },
      mailboxes: {
        list: (...args: unknown[]) => helpers.listMailboxes(...args),
      },
    },
  }
})

import { InboxAssistant } from "@/components/inbox-assistant"

describe("InboxAssistant controls", () => {
  beforeEach(() => {
    resetInboxAssistantMocks()
  })

  it("aborts the in-flight stream when a new question is sent", async () => {
    let releaseFirst: () => void = () => {}
    const firstGate = new Promise<void>((resolve) => {
      releaseFirst = resolve
    })
    let firstSignal: AbortSignal | undefined
    askChat.mockImplementationOnce(
      async (
        _body: unknown,
        _handlers: Parameters<typeof deliverStream>[0],
        signal?: AbortSignal,
      ) => {
        firstSignal = signal
        await firstGate
      },
    )
    askChat.mockImplementationOnce(
      async (_body: unknown, handlers: Parameters<typeof deliverStream>[0]) => {
        deliverStream(handlers, "The SampleLab thread is waiting on review.")
      },
    )
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "threads about SampleLab",
    )
    await user.keyboard("{Meta>}{Enter}{/Meta}")
    expect(firstSignal?.aborted).toBe(true)
    releaseFirst()
    expect(await screen.findByText("The SampleLab thread is waiting on review.")).toBeInTheDocument()
    expect(screen.queryByText("Could not ask InboxAssistant")).not.toBeInTheDocument()
  })

  it("aborts the in-flight stream when InboxAssistant is closed", async () => {
    let firstSignal: AbortSignal | undefined
    askChat.mockImplementation(
      async (
        _body: unknown,
        _handlers: Parameters<typeof deliverStream>[0],
        signal?: AbortSignal,
      ) => {
        firstSignal = signal
        await new Promise(() => {})
      },
    )
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    await user.click(screen.getByRole("button", { name: /close inboxassistant/i }))
    expect(firstSignal?.aborted).toBe(true)
  })

  it("shows a stop button while generating and keeps the partial answer", async () => {
    askChat.mockImplementation(
      async (
        _body: unknown,
        handlers: Parameters<typeof deliverStream>[0],
        signal?: AbortSignal,
      ) => {
        handlers.onDelta?.("The overdue")
        await new Promise<void>((_resolve, reject) => {
          signal?.addEventListener("abort", () => {
            reject(new DOMException("The operation was aborted.", "AbortError"))
          })
        })
      },
    )
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(await screen.findByText(/the overdue/i)).toBeInTheDocument()
    const stop = await screen.findByRole("button", { name: /stop generating/i })
    expect(screen.queryByRole("button", { name: /^send$/i })).not.toBeInTheDocument()
    await user.click(stop)
    expect(await screen.findByRole("button", { name: /^send$/i })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /stop generating/i })).not.toBeInTheDocument()
    expect(screen.getByText(/the overdue/i)).toBeInTheDocument()
    expect(screen.queryByText(/connection problem/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/could not ask/i)).not.toBeInTheDocument()
  })

  it("shows a cached badge and re-asks with bypass_cache", async () => {
    const user = userEvent.setup()
    askChat.mockImplementation(
      async (_body: unknown, handlers: Parameters<typeof deliverStream>[0]) => {
        deliverStream(handlers, groundedResponse.answer, { cached: true })
      },
    )
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "what should I focus on today",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(await screen.findByText(/cached · re-ask to refresh/i)).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /re-ask now/i }))
    const lastBody = askChat.mock.calls.at(-1)?.[0] as { bypass_cache?: boolean }
    expect(lastBody.bypass_cache).toBe(true)
  })

  it("dims an unsupported answer and exposes the verification note", async () => {
    const user = userEvent.setup()
    askChat.mockImplementation(
      async (_body: unknown, handlers: Parameters<typeof deliverStream>[0]) => {
        deliverStream(handlers, groundedResponse.answer, {
          grounded_verifier: "UNSUPPORTED",
        })
      },
    )
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "when did Ashley sign",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(
      await screen.findByText(/could not be confirmed from the cited threads/i),
    ).toBeInTheDocument()
    expect(
      screen.getByLabelText(/could not be confirmed from the cited threads/i),
    ).toBeInTheDocument()
  })

  it("dims an answer with an unverified (UNKNOWN) groundedness verdict", async () => {
    // H2: verify_grounded now fails closed to UNKNOWN (timeout/parse-failure)
    // instead of silently claiming SUPPORTED — the client must surface this
    // distinctly from a real UNSUPPORTED verdict.
    const user = userEvent.setup()
    askChat.mockImplementation(
      async (_body: unknown, handlers: Parameters<typeof deliverStream>[0]) => {
        deliverStream(handlers, groundedResponse.answer, {
          grounded_verifier: "UNKNOWN",
        })
      },
    )
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "when did Ashley sign",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(await screen.findByText(/could not be verified in time/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/could not be verified in time/i)).toBeInTheDocument()
  })

  it("shows an error when the stream ends without an answer", async () => {
    const user = userEvent.setup()
    askChat.mockImplementation(async () => undefined)
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "give me the latest on omason",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(await screen.findByText(/could not ask inboxassistant/i)).toBeInTheDocument()
    expect(screen.getByText(/did not return an answer/i)).toBeInTheDocument()
    expect(screen.getByText("give me the latest on omason")).toBeInTheDocument()
  })

  it("keeps a mid-stream fenced block as visible text, not garbled HTML", async () => {
    const user = userEvent.setup()
    askChat.mockImplementation(
      async (_body: unknown, handlers: Parameters<typeof deliverStream>[0]) => {
        handlers.onMeta?.({
          citations: groundedResponse.citations,
          retrieval_count: 1,
          mailbox: null,
          refused_write: false,
        })
        handlers.onDelta?.("Use this snippet:\n```\nselect * from")
        handlers.onDone?.()
      },
    )
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(screen.getByRole("textbox", { name: /message inboxassistant/i }), "show the query")
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(await screen.findByText(/select \* from/i)).toBeInTheDocument()
    expect(document.querySelector("code")).toBeNull()
  })

  it("wraps a long unbreakable string in the composer and the sent bubble", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    const input = screen.getByRole("textbox", { name: /message inboxassistant/i })
    expect(input.className).toMatch(/wrap-anywhere/)
    const blob = "d".repeat(80)
    fireEvent.change(input, { target: { value: blob } })
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    const bubble = await screen.findByText(blob)
    expect(bubble.className).toMatch(/wrap-anywhere/)
  })

  it("keeps the composer shell a rounded rectangle when wrapped text has no newlines", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    const input = screen.getByRole("textbox", { name: /message inboxassistant/i })
    const shell = input.parentElement
    expect(shell).not.toBeNull()
    fireEvent.change(input, {
      target: { value: "tell ".repeat(40).trimEnd() },
    })
    expect(shell?.className).not.toMatch(/rounded-full/)
    expect(shell?.className).toMatch(/rounded-2xl/)
  })

  it("shows a generating caret while tokens are still arriving", async () => {
    let handlers: Parameters<typeof deliverStream>[0] | null = null
    askChat.mockImplementation(
      (_body: unknown, nextHandlers: Parameters<typeof deliverStream>[0]) => {
        handlers = nextHandlers
        return new Promise(() => {})
      },
    )
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    await act(async () => {
      handlers?.onDelta?.("The overdue ")
    })
    expect(await screen.findByLabelText("Generating answer")).toBeInTheDocument()
    await act(async () => {
      handlers?.onDone?.()
    })
    // Caret stays while the smooth reveal catches up, then clears.
    await waitFor(() => {
      expect(screen.queryByLabelText("Generating answer")).not.toBeInTheDocument()
    })
  })

  it("links the empty-question validation to the composer", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent("Enter a question")
    expect(alert).toHaveAttribute("id", "inboxassistant-validation")
    expect(screen.getByRole("textbox", { name: /message inboxassistant/i })).toHaveAttribute(
      "aria-describedby",
      "inboxassistant-validation",
    )
  })
})
