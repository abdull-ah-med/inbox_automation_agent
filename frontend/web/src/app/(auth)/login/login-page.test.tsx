import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/features/auth/use-auth", async () => {
  const actual = await vi.importActual<typeof import("@/features/auth/use-auth")>(
    "@/features/auth/use-auth",
  );
  return {
    ...actual,
    useLogin: () => ({
      mutateAsync: vi.fn().mockResolvedValue({}),
      isPending: false,
    }),
    useAuthState: () => ({
      accessToken: null,
      expiresAt: null,
      user: null,
    }),
  };
});

import LoginPage from "@/app/(auth)/login/page";

describe("login page", () => {
  it("shows validation errors for empty submit", async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByText(/valid email/i)).toBeInTheDocument();
  });
});
