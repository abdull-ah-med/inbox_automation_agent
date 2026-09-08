import type { ReactElement, ReactNode } from "react"
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
  const { queryClient = makeClient(), wrapper: UserWrapper, ...rest } = options
  const Wrapper = ({ children }: { children: ReactNode }) => {
    const withQuery = <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    if (!UserWrapper) return withQuery
    return <UserWrapper>{withQuery}</UserWrapper>
  }
  return render(ui, { ...rest, wrapper: Wrapper })
}
