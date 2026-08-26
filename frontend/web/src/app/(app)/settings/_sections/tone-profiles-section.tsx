"use client"

import { useQuery } from "@tanstack/react-query"

import { StatusBadge } from "@/components/status-badge"
import { api } from "@/lib/api-client"
import type { ToneProfileResponse } from "@/lib/types"

export const ToneProfilesSection = () => {
  const { data: toneProfiles } = useQuery({
    queryKey: ["tone-profiles"],
    queryFn: () => api.toneProfiles.list(),
  })
  const profiles = toneProfiles ?? []

  return (
    <section className="mt-10" aria-labelledby="tone-profiles-heading">
      <div className="mb-5">
        <h2
          id="tone-profiles-heading"
          className="text-xl font-semibold text-gray-900 dark:text-gray-100"
        >
          Tone profiles
        </h2>
        <p className="mt-1 text-sm text-gray-500">
          Distilled voice rules rebuilt automatically after enough approvals.
          Read-only.
        </p>
      </div>
      <div className="overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
        {profiles.length === 0 ? (
          <p className="p-6 text-sm text-gray-500">
            No tone profiles yet. Approve at least 10 drafts to distill a profile.
          </p>
        ) : (
          <ul className="divide-y divide-gray-100 dark:divide-gray-800">
            {profiles.map((profile: ToneProfileResponse) => (
              <li key={profile.id} className="space-y-2 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-medium text-gray-900 dark:text-gray-100">
                    {profile.mailbox}
                  </p>
                  <StatusBadge label={profile.routing_category} tone="neutral" />
                  <StatusBadge
                    label={`v${profile.version}`}
                    tone="blue"
                  />
                  <span className="text-xs text-gray-500">
                    {profile.sample_count} samples
                  </span>
                </div>
                <p className="text-sm text-gray-700 dark:text-gray-300">
                  {profile.profile.formality} · {profile.profile.typical_length}
                  {profile.profile.greeting_pattern
                    ? ` · ${profile.profile.greeting_pattern}`
                    : ""}
                  {profile.profile.sign_off_pattern
                    ? ` · ${profile.profile.sign_off_pattern}`
                    : ""}
                </p>
                {profile.profile.behavioral_rules.length > 0 ? (
                  <ul className="list-disc pl-5 text-sm text-gray-600 dark:text-gray-400">
                    {profile.profile.behavioral_rules.map((rule) => (
                      <li key={rule}>{rule}</li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
