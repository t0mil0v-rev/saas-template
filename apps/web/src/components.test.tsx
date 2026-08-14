import { type User } from "@/api";
import { useAuth } from "@/auth";
import { Alert, Nav, Spinner, Stars, StatusBadge } from "@/components";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/auth", () => ({ useAuth: vi.fn() }));
const mockedUseAuth = vi.mocked(useAuth);

const user: User = {
  id: "admin-1",
  email: "admin@example.com",
  full_name: "Admin",
  is_active: true,
  is_superuser: true,
  totp_enabled: true,
  email_verified_at: "2026-01-01T00:00:00Z",
  last_login_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

describe("shared components", () => {
  beforeEach(() => {
    mockedUseAuth.mockReturnValue({
      user: null,
      loading: false,
      refresh: vi.fn(),
      login: vi.fn(),
      logout: vi.fn(),
    });
  });

  it("renders ratings, statuses, alerts, and loading state", () => {
    const { rerender } = render(<Stars value={3.6} />);
    expect(screen.getByLabelText("3.6 из 5")).toHaveTextContent("★★★★☆");

    rerender(<StatusBadge status="approved" />);
    expect(screen.getByText("опубликован")).toHaveClass("badge-approved");
    rerender(<StatusBadge status="custom" />);
    expect(screen.getByText("custom")).toBeVisible();
    rerender(<Alert kind="success">Готово</Alert>);
    expect(screen.getByText("Готово")).toHaveClass("alert-success");
    rerender(<Alert kind="error">{null}</Alert>);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    rerender(<Spinner />);
    expect(screen.getByText("Загрузка…")).toBeVisible();
  });

  it("shows public navigation to a signed-out visitor", () => {
    render(
      <MemoryRouter>
        <Nav />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: "Вход" })).toHaveAttribute("href", "/login");
    expect(screen.queryByRole("button", { name: "Выйти" })).not.toBeInTheDocument();
  });

  it("logs an administrator out and navigates to login", async () => {
    const logout = vi.fn().mockResolvedValue(undefined);
    mockedUseAuth.mockReturnValue({
      user,
      loading: false,
      refresh: vi.fn(),
      login: vi.fn(),
      logout,
    });
    render(
      <MemoryRouter initialEntries={["/app"]}>
        <Routes>
          <Route path="*" element={<Nav />} />
          <Route path="/login" element={<h1>Signed out</h1>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Админка" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Выйти" }));
    expect(logout).toHaveBeenCalledOnce();
    expect(await screen.findByRole("heading", { name: "Signed out" })).toBeVisible();
  });
});
