import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { ChatTurnList } from "@/components/chat-turn-list"

describe("ChatTurnList", () => {
  it("shows the assistant answer the user can read", () => {
    render(
      <ChatTurnList
        turns={[
          {
            id: "assistant-1",
            role: "assistant",
            text: "Focus on billing.",
          },
        ]}
        mailboxes={[]}
        isAsking={false}
        toolStatus={null}
        onReask={vi.fn()}
      />,
    )

    expect(screen.getByText("Focus on billing.")).toBeInTheDocument()
  })

  it("announces thinking status before any assistant text arrives", () => {
    render(
      <ChatTurnList
        turns={[
          {
            id: "user-1",
            role: "user",
            text: "What is overdue?",
          },
        ]}
        mailboxes={[]}
        isAsking
        toolStatus="Searching mail"
        onReask={vi.fn()}
      />,
    )

    expect(screen.getByRole("status")).toHaveTextContent("Searching mail")
  })
})
