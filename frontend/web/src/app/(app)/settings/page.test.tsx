import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

vi.mock("@/components/breadcrumbs", () => ({
  Breadcrumbs: () => null,
}))
vi.mock("./_sections/account-section", () => ({
  AccountSection: () => <div>Account stub</div>,
}))
vi.mock("./_sections/contacts-section", () => ({
  ContactsSection: () => <div>Contacts stub</div>,
}))
vi.mock("./_sections/skills-section", () => ({
  SkillsSection: () => <div>Skills stub</div>,
}))
vi.mock("./_sections/skill-candidates-section", () => ({
  SkillCandidatesSection: () => <div>Candidates stub</div>,
}))
vi.mock("./_sections/tone-profiles-section", () => ({
  ToneProfilesSection: () => <div>Tone profiles stub</div>,
}))
vi.mock("./_sections/tone-memory-section", () => ({
  ToneMemorySection: () => <div>Tone memory stub</div>,
}))
vi.mock("./_sections/rejection-memory-section", () => ({
  RejectionMemorySection: () => <div>Rejection memory stub</div>,
}))
vi.mock("./_sections/teaching-notes-section", () => ({
  TeachingNotesSection: () => <div>Teaching notes</div>,
}))
vi.mock("./_sections/promotion-proposals-section", () => ({
  PromotionProposalsSection: () => <div>Promotion proposals</div>,
}))
vi.mock("./_sections/urgency-rules-section", () => ({
  UrgencyRulesSection: () => <div>Urgency rules</div>,
}))

vi.mock("./_sections/mailbox-filter", () => ({
  useMailboxFilter: () => ({
    mailbox: "sampleagent",
    mailboxes: [],
    setMailbox: () => {},
    isLoading: false,
  }),
  MailboxFilter: () => null,
}))

import SettingsPage from "./page"

describe("SettingsPage", () => {
  it("shows teaching notes, promotion proposals, and urgency rules", () => {
    render(<SettingsPage />)

    expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument()
    expect(screen.getByText("Teaching notes")).toBeInTheDocument()
    expect(screen.getByText("Promotion proposals")).toBeInTheDocument()
    expect(screen.getByText("Urgency rules")).toBeInTheDocument()
  })
})
