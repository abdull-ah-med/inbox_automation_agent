import type { ReactElement } from "react"
import { render, type RenderOptions } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

const makeClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })

type Options = RenderOptions & { queryClient?: QueryClient }

export const renderWithProviders = (ui: ReactElement, options: Options = {}) => {
  const { queryClient = makeClient(), ...rest } = options
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>, rest)
}
