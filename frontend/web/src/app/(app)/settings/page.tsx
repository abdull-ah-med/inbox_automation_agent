"use client"

import { Breadcrumbs } from "@/components/breadcrumbs"

import { AccountSection } from "./_sections/account-section"
import { ContactsSection } from "./_sections/contacts-section"
import { PromotionProposalsSection } from "./_sections/promotion-proposals-section"
import { RejectionMemorySection } from "./_sections/rejection-memory-section"
import { SkillCandidatesSection } from "./_sections/skill-candidates-section"
import { SkillsSection } from "./_sections/skills-section"
import { TeachingNotesSection } from "./_sections/teaching-notes-section"
import { ToneMemorySection } from "./_sections/tone-memory-section"
import { ToneProfilesSection } from "./_sections/tone-profiles-section"
import { UrgencyRulesSection } from "./_sections/urgency-rules-section"

export default function SettingsPage() {
  return (
    <>
      <Breadcrumbs items={[{ label: "Overview", href: "/dashboard" }, { label: "Settings" }]} />
      <div className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-100">
          Settings
        </h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Account, greeting contacts, skills, rejected drafts, and tone memory for your mailboxes.
        </p>
      </div>
      <AccountSection />
      <ContactsSection />
      <SkillsSection />
      <SkillCandidatesSection />
      <ToneProfilesSection />
      <div className="mt-10 space-y-10">
        <ToneMemorySection />
        <RejectionMemorySection />
        <TeachingNotesSection />
        <PromotionProposalsSection />
        <UrgencyRulesSection />
      </div>
    </>
  )
}
