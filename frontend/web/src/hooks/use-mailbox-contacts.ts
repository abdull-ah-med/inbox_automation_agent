"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { api } from "@/lib/api-client"

export const CONTACTS_PAGE_SIZE = 50

export const useMailboxContacts = (
  mailbox: string | null,
  opts: { q?: string; offset?: number; limit?: number } = {},
) => {
  const q = opts.q?.trim() || undefined
  const limit = opts.limit ?? CONTACTS_PAGE_SIZE
  const offset = opts.offset ?? 0
  return useQuery({
    queryKey: ["mailbox", mailbox, "contacts", q ?? "", offset, limit],
    queryFn: () =>
      api.mailboxContacts.list(mailbox!, {
        q,
        limit,
        offset,
      }),
    enabled: Boolean(mailbox),
  })
}

const invalidateContactQueries = async (
  queryClient: ReturnType<typeof useQueryClient>,
  mailbox: string,
) => {
  await queryClient.invalidateQueries({ queryKey: ["mailbox", mailbox, "contacts"] })
  await queryClient.invalidateQueries({ queryKey: ["thread"] })
}

export const useDeleteContact = (mailbox: string) => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (email: string) => api.mailboxContacts.remove(mailbox, email),
    onSuccess: async () => {
      await invalidateContactQueries(queryClient, mailbox)
    },
  })
}
