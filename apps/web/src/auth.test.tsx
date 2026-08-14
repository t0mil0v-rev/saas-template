import { api, ApiError, type User } from "@/api";
import { AuthProvider, useAuth } from "@/auth";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

const user: User = {
  id: "user-1",
  email: "ada@example.com",
  full_name: "Ada",
  is_active: true,
  is_superuser: false,
  totp_enabled: false,
  email_verified_at: null,
  last_login_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

function wrapper({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
}

describe("AuthProvider", () => {
  it("loads the current user and clears it after logout", async () => {
    vi.spyOn(api, "get").mockResolvedValue(user);
    vi.spyOn(api, "post").mockResolvedValue({});
    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.user).toEqual(user);

    await act(() => result.current.logout());
    expect(api.post).toHaveBeenCalledWith("/auth/logout");
    expect(result.current.user).toBeNull();
  });

  it("treats an unauthenticated refresh as a normal signed-out state", async () => {
    vi.spyOn(api, "get").mockRejectedValue(
      new ApiError(401, {
        error: { code: "unauthenticated", message: "Войдите" },
        request_id: "request-1",
      }),
    );
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.user).toBeNull();
    expect(consoleError).not.toHaveBeenCalled();
  });

  it("stores the user returned by login", async () => {
    vi.spyOn(api, "get").mockRejectedValue(
      new ApiError(401, {
        error: { code: "unauthenticated", message: "Войдите" },
        request_id: "request-1",
      }),
    );
    vi.spyOn(api, "post").mockResolvedValue({ user });
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.login("ada@example.com", "password"));
    expect(result.current.user).toEqual(user);
  });
});
