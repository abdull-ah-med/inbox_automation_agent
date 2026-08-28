"use client"

import { AuditSection } from "@/components/thread-triage/audit-section"
import { ClassificationSection } from "@/components/thread-triage/classification-section"
import { DraftSection } from "@/components/thread-triage/draft-section"
import { Tabs, TabsContent, TabsIndicator, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type {
  AuditEntry,
  DraftView,
  ReplyAddresseeView,
  ThreadSummary,
  TriageFlags,
} from "@/lib/types"

type ThreadTriageTabsProps = {
  threadId: string
  thread: ThreadSummary
  triage: TriageFlags | null
  draft: DraftView | null
  auditLog: AuditEntry[]
  suggestedActions: NonNullable<DraftView["suggested_actions"]>
  badge: { label: string; tone: "green" | "blue" | "red" | "amber" } | null
  actionError: string | null
  feedbackDone: boolean
  busy: boolean
  replyAddressee?: ReplyAddresseeView | null
  onApprove: () => void
  onApproveKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void
  onReject: () => void
  onRejectKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void
}

export const ThreadTriageTabs = ({
  threadId,
  thread,
  triage,
  draft,
  auditLog,
  suggestedActions,
  badge,
  actionError,
  feedbackDone,
  busy,
  replyAddressee = null,
  onApprove,
  onApproveKeyDown,
  onReject,
  onRejectKeyDown,
}: ThreadTriageTabsProps) => (
  <Tabs defaultValue="classification" className="w-full gap-3">
    <TabsList className="w-full" aria-label="Thread review sections">
      <TabsTrigger value="classification">Classification</TabsTrigger>
      <TabsTrigger value="draft">Draft</TabsTrigger>
      <TabsTrigger value="audit">Audit ({auditLog.length})</TabsTrigger>
      <TabsIndicator />
    </TabsList>

    <TabsContent value="classification" className="space-y-4 outline-none">
      <ClassificationSection
        threadId={threadId}
        thread={thread}
        triage={triage}
        suggestedActions={suggestedActions}
      />
    </TabsContent>

    <TabsContent value="draft" className="outline-none">
      <DraftSection
        draft={draft}
        badge={badge}
        actionError={actionError}
        feedbackDone={feedbackDone}
        busy={busy}
        mailboxKey={thread.mailbox_key}
        replyAddressee={replyAddressee}
        onApprove={onApprove}
        onApproveKeyDown={onApproveKeyDown}
        onReject={onReject}
        onRejectKeyDown={onRejectKeyDown}
      />
    </TabsContent>

    <TabsContent value="audit" className="outline-none">
      <AuditSection auditLog={auditLog} />
    </TabsContent>
  </Tabs>
)
