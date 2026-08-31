import { render, screen } from "@testing-library/react"
import { createRef } from "react"
import { describe, expect, it, vi } from "vitest"

import { ChatComposer } from "@/components/chat-composer"

describe("ChatComposer", () => {
  it("surfaces validation next to the message field", () => {
    render(
      <ChatComposer
        message=""
        onMessageChange={vi.fn()}
        onSubmit={vi.fn()}
        onKeyDown={vi.fn()}
        isAsking={false}
        validation="Enter a question"
        onStop={vi.fn()}
        mailbox=""
        mailboxItems={[{ label: "All mailboxes", value: null }]}
        onMailboxChange={vi.fn()}
        mailboxesLoading={false}
        inputRef={createRef<HTMLTextAreaElement>()}
        composerRows={1}
      />,
    )

    expect(screen.getByRole("alert")).toHaveTextContent("Enter a question")
    expect(screen.getByLabelText("Message InboxAssistant")).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByLabelText("Message InboxAssistant")).toHaveAttribute(
      "aria-describedby",
      "inboxassistant-validation",
    )
  })
})
