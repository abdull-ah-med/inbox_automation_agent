export interface Email {
  id: string;
  inbox: "client-relations" | "sales" | "vendor" | "intermediary";
  from: string;
  to: string;
  subject: string;
  body: string;
  receivedAt: string;
  threadId: string;
  isRead: boolean;
}

export interface Classification {
  type: string;
  intent: "reply" | "forward" | "document-request" | "fyi";
  urgency: "high" | "medium" | "low";
  entities: {
    client?: string;
    order?: string;
    county?: string;
    drugScreenStatus?: string;
    vendor?: string;
    invoice?: string;
  };
  confidence: number;
  routedBy: "rule" | "llm";
  ruleMatch?: string;
}

export interface SuggestedAction {
  action: "reply" | "forward";
  forwardTo?: string;
  draft: string;
  teachingNote: string;
}

export interface ThreadState {
  status: "new" | "awaiting-client" | "awaiting-vendor" | "awaiting-partner-services" | "resolved";
  lastUpdated: string;
  hoursStale: number;
  suggestedDelegate?: string;
}

export interface AuditEntry {
  timestamp: string;
  event: string;
  detail: string;
  source: "system" | "agent" | "elise";
}

export interface TriageResult {
  email: Email;
  classification: Classification;
  suggestedAction: SuggestedAction;
  threadState: ThreadState;
  auditLog: AuditEntry[];
}

export const sampleEmails: Email[] = [
  {
    id: "e-001",
    inbox: "client-relations",
    from: "marcus.j@sample-logistics.example.com",
    to: "clientrelations@sample-information.example.com",
    subject: "RE: Order #PSC-40821 — Pre-employment screen results needed ASAP",
    body: `Hi team,

We have a new hire starting this Monday and still haven't received the drug screen results for Order #PSC-40821 (candidate: J. Rivera, Travis County). 

The hiring manager is asking me every day. Can you please expedite this? We need the clear-to-start before Friday COB.

Thanks,
Marcus Jensen
HR Coordinator, Sample Logistics`,
    receivedAt: "2026-07-08T09:14:00Z",
    threadId: "t-001",
    isRead: false,
  },
  {
    id: "e-002",
    inbox: "sales",
    from: "linda.w@sample-staffing.example.com",
    to: "sales@sample-information.example.com",
    subject: "Pricing inquiry — statewide criminal + MVR bundle",
    body: `Good morning,

We're a mid-size staffing agency (roughly 200 placements/month) looking at switching background screening vendors. Could you send over pricing for:

1. Statewide criminal search
2. MVR (Motor Vehicle Record)
3. A bundled rate for both

We'd also like to understand turnaround times and whether you integrate with BambooHR.

Best,
Linda Wu
VP of Operations, Sample Staffing`,
    receivedAt: "2026-07-08T08:42:00Z",
    threadId: "t-002",
    isRead: false,
  },
  {
    id: "e-003",
    inbox: "vendor",
    from: "ap@sample-records.example.com",
    to: "vendors@sample-information.example.com",
    subject: "Invoice #CDR-9917 — June court-runner services",
    body: `Please find attached Invoice #CDR-9917 for court-runner services rendered in June 2026.

Amount due: $2,340.00
Payment terms: Net 30
Due date: July 28, 2026

If you have questions about any line items, please reach out.

Regards,
Accounts Payable
Sample Records Services`,
    receivedAt: "2026-07-08T07:55:00Z",
    threadId: "t-003",
    isRead: false,
  },
  {
    id: "e-004",
    inbox: "intermediary",
    from: "compliance@sample-checks.example.com",
    to: "intermediary@sample-information.example.com",
    subject: "FYI — DPPA policy update effective August 1",
    body: `Hi Partner Screening,

This is to inform you that our DPPA compliance policy has been updated effective August 1, 2026. Key changes:

• All MVR requests must now include the end-user's permissible purpose code.
• We will reject requests missing this field starting Aug 1.
• Updated forms are attached.

Please update your integration and notify your downstream clients.

Best,
Compliance Team
Sample Checks`,
    receivedAt: "2026-07-08T10:03:00Z",
    threadId: "t-004",
    isRead: false,
  },
  {
    id: "e-005",
    inbox: "client-relations",
    from: "sarah.k@sample-hiring.example.com",
    to: "clientrelations@sample-information.example.com",
    subject: "Need county criminal search added to our account",
    body: `Hi,

We'd like to add Maricopa County (AZ) criminal search to our standard package. Can you confirm pricing and whether this changes our turnaround SLA?

Also, our account rep mentioned something about a volume discount kicking in at 500 searches/month — we're close. Can you check?

Thanks,
Sarah Kim
Talent Ops, SampleHiring`,
    receivedAt: "2026-07-08T11:20:00Z",
    threadId: "t-005",
    isRead: false,
  },
];

export const sampleResults: TriageResult[] = [
  {
    email: sampleEmails[0],
    classification: {
      type: "Order Status Inquiry",
      intent: "reply",
      urgency: "high",
      entities: {
        client: "Sample Logistics",
        order: "PSC-40821",
        county: "Travis County",
        drugScreenStatus: "pending",
      },
      confidence: 0.95,
      routedBy: "rule",
      ruleMatch: "RULE-012: Order status follow-up — contains order number + status request",
    },
    suggestedAction: {
      action: "reply",
      draft: `Hi Marcus,

Thank you for following up. I'm checking on Order #PSC-40821 for J. Rivera (Travis County) right now.

I'll have a status update for you by end of day today. If the results are in, I'll send them over immediately so you can get the clear-to-start before Friday.

Best regards,
Elise`,
      teachingNote: `**What this is:** A client following up on a pending drug screen order with a tight deadline (new hire starting Monday).

**Which workflow:** Order Status Follow-Up (RULE-012). When a client references an order number and asks for status/results, check the order in the system, get the current status from the lab or court runner, and reply with a concrete timeline.

**Why it's urgent:** The client has a hard deadline (Friday COB) and their hiring manager is pressuring them. Delays here risk the client relationship.

**What to do:** Look up PSC-40821 in the system. If results are back, attach and send. If still pending, contact the lab/runner for an ETA and relay that to Marcus.`,
    },
    threadState: {
      status: "awaiting-vendor",
      lastUpdated: "2026-07-08T09:14:00Z",
      hoursStale: 0,
    },
    auditLog: [
      { timestamp: "2026-07-08T09:14:02Z", event: "email_received", detail: "New email in client-relations inbox from marcus.j@sample-logistics.example.com", source: "system" },
      { timestamp: "2026-07-08T09:14:03Z", event: "classification", detail: "Classified as 'Order Status Inquiry' — intent: reply, urgency: high (RULE-012)", source: "agent" },
      { timestamp: "2026-07-08T09:14:04Z", event: "entities_extracted", detail: "Client: Sample Logistics | Order: PSC-40821 | County: Travis | Drug screen: pending", source: "agent" },
      { timestamp: "2026-07-08T09:14:05Z", event: "draft_generated", detail: "Reply draft generated — acknowledges urgency and commits to EOD update", source: "agent" },
      { timestamp: "2026-07-08T09:14:05Z", event: "queued_for_review", detail: "Item added to Slack review queue (confidence: 0.95)", source: "system" },
    ],
  },
  {
    email: sampleEmails[1],
    classification: {
      type: "New Business Inquiry",
      intent: "forward",
      urgency: "medium",
      entities: {
        client: "Sample Staffing",
      },
      confidence: 0.91,
      routedBy: "rule",
      ruleMatch: "RULE-003: Sales inquiry — pricing request from non-existing client",
    },
    suggestedAction: {
      action: "forward",
      forwardTo: "kelvin@sample-information.example.com",
      draft: `Hi Kelvin,

New pricing inquiry from Linda Wu at Sample Staffing (~200 placements/month). They're looking at statewide criminal + MVR bundled pricing and want to know about BambooHR integration.

Could be a good fit for the mid-tier package. Details below.

— Elise`,
      teachingNote: `**What this is:** A sales lead — a prospective client asking about pricing and integration. This is a warm inbound lead.

**Which workflow:** Forward to Sales (RULE-003). Pricing inquiries from new/unknown clients go to Kelvin with a brief context note. Don't reply directly with pricing — Kelvin handles the sales conversation.

**Why medium urgency:** Sales leads should be responded to within 24 hours to stay competitive, but there's no hard deadline like an active order.

**What to do:** Forward to Kelvin with a summary. Flag the volume (~200/month) since it affects tier pricing. Note the BambooHR integration question since that's a common deal-closer.`,
    },
    threadState: {
      status: "new",
      lastUpdated: "2026-07-08T08:42:00Z",
      hoursStale: 0,
    },
    auditLog: [
      { timestamp: "2026-07-08T08:42:01Z", event: "email_received", detail: "New email in sales inbox from linda.w@sample-staffing.example.com", source: "system" },
      { timestamp: "2026-07-08T08:42:02Z", event: "classification", detail: "Classified as 'New Business Inquiry' — intent: forward, urgency: medium (RULE-003)", source: "agent" },
      { timestamp: "2026-07-08T08:42:03Z", event: "entities_extracted", detail: "Client: Sample Staffing (new prospect)", source: "agent" },
      { timestamp: "2026-07-08T08:42:04Z", event: "draft_generated", detail: "Forward draft to kelvin@sample-information.example.com with sales context", source: "agent" },
      { timestamp: "2026-07-08T08:42:04Z", event: "queued_for_review", detail: "Item added to Slack review queue (confidence: 0.91)", source: "system" },
    ],
  },
  {
    email: sampleEmails[2],
    classification: {
      type: "Vendor Invoice",
      intent: "forward",
      urgency: "low",
      entities: {
        vendor: "Sample Records Services",
        invoice: "CDR-9917",
      },
      confidence: 0.97,
      routedBy: "rule",
      ruleMatch: "RULE-008: Vendor invoice — contains invoice number + amount due",
    },
    suggestedAction: {
      action: "forward",
      forwardTo: "accounting@sample-information.example.com",
      draft: `Hi Accounting,

Forwarding Invoice #CDR-9917 from Sample Records Services for June court-runner services.

Amount: $2,340.00
Due: July 28, 2026 (Net 30)

Please process per usual.

— Elise`,
      teachingNote: `**What this is:** A routine vendor invoice for court-runner services. These come monthly from our research partners.

**Which workflow:** Forward to Accounting (RULE-008). Vendor invoices are forwarded to accounting with the key details pulled out (amount, due date, terms). No reply needed to the vendor unless there's a discrepancy.

**Why low urgency:** Net 30 terms, due date is 3 weeks out. No action needed beyond forwarding.

**What to do:** Forward to accounting. If the amount looks unusual compared to prior months, flag it in the forward note. Otherwise, this is a straight pass-through.`,
    },
    threadState: {
      status: "new",
      lastUpdated: "2026-07-08T07:55:00Z",
      hoursStale: 0,
    },
    auditLog: [
      { timestamp: "2026-07-08T07:55:01Z", event: "email_received", detail: "New email in vendor inbox from ap@sample-records.example.com", source: "system" },
      { timestamp: "2026-07-08T07:55:02Z", event: "classification", detail: "Classified as 'Vendor Invoice' — intent: forward, urgency: low (RULE-008)", source: "agent" },
      { timestamp: "2026-07-08T07:55:03Z", event: "entities_extracted", detail: "Vendor: Sample Records Services | Invoice: CDR-9917 | Amount: $2,340.00", source: "agent" },
      { timestamp: "2026-07-08T07:55:03Z", event: "draft_generated", detail: "Forward draft to accounting@sample-information.example.com", source: "agent" },
      { timestamp: "2026-07-08T07:55:04Z", event: "queued_for_review", detail: "Item added to Slack review queue (confidence: 0.97)", source: "system" },
    ],
  },
  {
    email: sampleEmails[3],
    classification: {
      type: "Compliance Update",
      intent: "fyi",
      urgency: "medium",
      entities: {
        vendor: "Sample Checks",
      },
      confidence: 0.78,
      routedBy: "llm",
    },
    suggestedAction: {
      action: "forward",
      forwardTo: "kelvin@sample-information.example.com",
      draft: `Hi Kelvin,

Heads up — Sample Checks is updating their DPPA compliance policy effective August 1. Key change: all MVR requests will need to include the end-user's permissible purpose code or they'll be rejected.

We should update our integration before then. I'll forward the updated forms as well.

Let me know if you want to discuss.

— Elise`,
      teachingNote: `**What this is:** A compliance policy change from one of our intermediary partners. They're tightening DPPA requirements for MVR (Motor Vehicle Record) requests.

**Which workflow:** No exact rule match — this was handled by the LLM. Compliance updates from partners typically get forwarded to Kelvin with a summary of what's changing and when, plus any action items for our side.

**Why medium urgency:** The effective date is August 1, giving us ~3 weeks. But integration changes take time, so this shouldn't sit.

**What to do:** Forward to Kelvin. The action items are: (1) update our MVR request integration to include the permissible purpose code, (2) notify any downstream clients who submit MVR requests through us. Calendar-block a reminder for July 25 to verify the update is live.`,
    },
    threadState: {
      status: "new",
      lastUpdated: "2026-07-08T10:03:00Z",
      hoursStale: 0,
    },
    auditLog: [
      { timestamp: "2026-07-08T10:03:01Z", event: "email_received", detail: "New email in intermediary inbox from compliance@sample-checks.example.com", source: "system" },
      { timestamp: "2026-07-08T10:03:03Z", event: "classification", detail: "Classified as 'Compliance Update' — intent: fyi, urgency: medium (LLM fallback, no rule match)", source: "agent" },
      { timestamp: "2026-07-08T10:03:04Z", event: "entities_extracted", detail: "Vendor: Sample Checks | Policy change: DPPA / MVR permissible purpose code required", source: "agent" },
      { timestamp: "2026-07-08T10:03:05Z", event: "draft_generated", detail: "Forward draft to kelvin@sample-information.example.com with compliance summary", source: "agent" },
      { timestamp: "2026-07-08T10:03:06Z", event: "queued_for_review", detail: "Item added to Slack review queue (confidence: 0.78)", source: "system" },
    ],
  },
  {
    email: sampleEmails[4],
    classification: {
      type: "Account Modification Request",
      intent: "reply",
      urgency: "medium",
      entities: {
        client: "SampleHiring",
        county: "Maricopa County, AZ",
      },
      confidence: 0.88,
      routedBy: "rule",
      ruleMatch: "RULE-015: Account change request — add/remove search type",
    },
    suggestedAction: {
      action: "reply",
      draft: `Hi Sarah,

Thanks for reaching out! I'd be happy to help get Maricopa County (AZ) criminal search added to your package.

Here's what I can tell you:
- Pricing for Maricopa County criminal: I'll confirm the exact rate and send it over shortly.
- Turnaround: County criminal searches in Maricopa typically take 1–2 business days and shouldn't change your current SLA.
- Volume discount: I'll pull your current monthly numbers and check where you stand on the 500/month threshold.

I'll have all the details for you by tomorrow. Let me know if you have any other questions!

Best,
Elise`,
      teachingNote: `**What this is:** An existing client asking to add a new search type to their account and also asking about a volume discount.

**Which workflow:** Account Modification (RULE-015). When a client asks to add or change search types, confirm pricing, check SLA impact, and if they mention volume thresholds, pull their current numbers.

**Why medium urgency:** Existing client request, not time-critical but should be handled within 24 hours to maintain good service.

**What to do:** (1) Look up Maricopa County criminal search pricing. (2) Confirm turnaround time for that county. (3) Pull SampleHiring's monthly search volume from the system to check if they qualify for the 500/month discount. Reply with all three answers. If the volume discount applies, loop in Kelvin since it affects their contract rate.`,
    },
    threadState: {
      status: "new",
      lastUpdated: "2026-07-08T11:20:00Z",
      hoursStale: 0,
    },
    auditLog: [
      { timestamp: "2026-07-08T11:20:01Z", event: "email_received", detail: "New email in client-relations inbox from sarah.k@sample-hiring.example.com", source: "system" },
      { timestamp: "2026-07-08T11:20:02Z", event: "classification", detail: "Classified as 'Account Modification Request' — intent: reply, urgency: medium (RULE-015)", source: "agent" },
      { timestamp: "2026-07-08T11:20:03Z", event: "entities_extracted", detail: "Client: SampleHiring | County: Maricopa County, AZ | Volume discount inquiry", source: "agent" },
      { timestamp: "2026-07-08T11:20:04Z", event: "draft_generated", detail: "Reply draft with pricing/SLA/discount check commitment", source: "agent" },
      { timestamp: "2026-07-08T11:20:04Z", event: "queued_for_review", detail: "Item added to Slack review queue (confidence: 0.88)", source: "system" },
    ],
  },
];
