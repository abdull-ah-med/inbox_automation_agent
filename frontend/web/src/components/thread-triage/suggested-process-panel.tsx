import { Panel } from "@/components/thread-triage/panel"
import type { DraftView } from "@/lib/types"

type SuggestedProcessPanelProps = {
  suggestedActions: NonNullable<DraftView["suggested_actions"]>
}

export const SuggestedProcessPanel = ({ suggestedActions }: SuggestedProcessPanelProps) => (
  <Panel title="Suggested Process">
    {suggestedActions.length > 0 ? (
      <ol className="space-y-3">
        {suggestedActions
          .toSorted((a, b) => a.step - b.step)
          .map((item) => (
            <li key={`${item.step}-${item.action}`} className="flex gap-3">
              <span
                className="flex size-6 shrink-0 items-center justify-center rounded-full bg-blue-100 text-xs font-semibold text-blue-700 dark:bg-blue-950 dark:text-blue-300"
                aria-hidden="true"
              >
                {item.step}
              </span>
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                  {item.action}
                </p>
                {item.stakeholder ? (
                  <span className="mt-1 inline-block rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-700 dark:bg-gray-800 dark:text-gray-300">
                    {item.stakeholder}
                  </span>
                ) : null}
                <p className="mt-1 text-xs text-gray-500">{item.rationale}</p>
              </div>
            </li>
          ))}
      </ol>
    ) : (
      <p className="text-sm text-gray-500">
        No suggested process. This thread hasn&apos;t produced a draft.
      </p>
    )}
  </Panel>
)
