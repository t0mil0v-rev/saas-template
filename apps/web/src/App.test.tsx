import { App } from "@/App";
import { useAuth } from "@/auth";
import { render, screen, waitFor } from "@testing-library/react";
import axe from "axe-core";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/auth", () => ({ useAuth: vi.fn() }));
vi.mock("@/pages/Dashboard", () => ({ DashboardPage: () => <h1>Личный кабинет</h1> }));
vi.mock("@/pages/Admin", () => ({ AdminPage: () => <h1>Администрирование</h1> }));

const mockedUseAuth = vi.mocked(useAuth);

describe("application routes", () => {
  beforeEach(() => {
    mockedUseAuth.mockReturnValue({
      user: null,
      loading: false,
      refresh: vi.fn(),
      login: vi.fn(),
      logout: vi.fn(),
    });
  });

  it("redirects a signed-out user from a private route", async () => {
    render(
      <MemoryRouter initialEntries={["/app"]}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("heading", { name: "Вход" })).toBeInTheDocument();
  });

  it("does not expose the admin route to a regular user", async () => {
    mockedUseAuth.mockReturnValue({
      user: {
        id: "user-1",
        email: "user@example.com",
        full_name: "User",
        is_active: true,
        is_superuser: false,
        totp_enabled: false,
        email_verified_at: null,
        last_login_at: null,
        created_at: "2026-01-01T00:00:00Z",
      },
      loading: false,
      refresh: vi.fn(),
      login: vi.fn(),
      logout: vi.fn(),
    });
    render(
      <MemoryRouter initialEntries={["/admin"]}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("heading", { name: "Личный кабинет" })).toBeInTheDocument();
  });

  it("renders the login page without detectable accessibility violations", async () => {
    const { container } = render(
      <MemoryRouter initialEntries={["/login"]}>
        <App />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("heading", { name: "Вход" })).toBeVisible());
    const result = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });
});
