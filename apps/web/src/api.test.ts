import { api, ApiError } from "@/api";
import { beforeEach, describe, expect, it, vi } from "vitest";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("API client", () => {
  beforeEach(() => {
    document.cookie = "sid-csrf=; Max-Age=0; Path=/";
    document.cookie = "__Host-sid-csrf=; Max-Age=0; Path=/";
  });

  it("uses same-origin credentials without a CSRF header for safe methods", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.get<{ ok: boolean }>("/health/live")).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/health/live",
      expect.objectContaining({
        method: "GET",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      }),
    );
  });

  it("copies the CSRF cookie into unsafe requests", async () => {
    document.cookie = "sid-csrf=token%20value; Path=/";
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ saved: true }));
    vi.stubGlobal("fetch", fetchMock);

    await api.patch("/auth/me", { full_name: "Ada" });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/me",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ full_name: "Ada" }),
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRF-Token": "token value",
        },
      }),
    );
  });

  it("returns undefined for an empty 204 response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(api.del("/sessions/one")).resolves.toBeUndefined();
  });

  it("turns the server error envelope into ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error: {
              code: "invalid_credentials",
              message: "Неверные данные",
              details: { attempts: 2 },
            },
            request_id: "request-42",
          },
          401,
        ),
      ),
    );

    const error = await api.post("/auth/login", {}).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 401,
      code: "invalid_credentials",
      message: "Неверные данные",
      requestId: "request-42",
      details: { attempts: 2 },
    });
  });
});
