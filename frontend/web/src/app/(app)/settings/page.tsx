"use client"

import { Breadcrumbs } from "@/components/breadcrumbs"

import { AccountSection } from "./_sections/account-section"
import { SkillCandidatesSection } from "./_sections/skill-candidates-section"
import { SkillsSection } from "./_sections/skills-section"
import { ToneMemorySection } from "./_sections/tone-memory-section"
import { ToneProfilesSection } from "./_sections/tone-profiles-section"

export default function SettingsPage() {
  return (
    <>
      <Breadcrumbs items={[{ label: "Overview", href: "/dashboard" }, { label: "Settings" }]} />
      <AccountSection />
      <SkillsSection />
      <SkillCandidatesSection />
      <ToneProfilesSection />
      <ToneMemorySection />
    </>
  )
}
