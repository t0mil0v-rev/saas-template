import { ApiError } from "@/api";
import { errorText, useFetch } from "@/hooks";
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

describe("useFetch", () => {
  it("loads data and can reload it", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce("first").mockResolvedValueOnce("second");
    const { result } = renderHook(() => useFetch(fetcher, []));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toBe("first");

    act(() => result.current.reload());
    await waitFor(() => expect(result.current.data).toBe("second"));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("shows API errors and a safe fallback for unknown failures", async () => {
    const apiError = new ApiError(503, {
      error: { code: "unavailable", message: "Сервис недоступен" },
      request_id: "request-2",
    });
    const { result, rerender } = renderHook(
      ({ failure }) => useFetch(() => Promise.reject(failure), [failure]),
      { initialProps: { failure: apiError as unknown } },
    );
    await waitFor(() => expect(result.current.error).toBe("Сервис недоступен"));

    rerender({ failure: new Error("internal detail") });
    await waitFor(() => expect(result.current.error).toBe("Не удалось загрузить данные"));
  });

  it("aborts the request when its consumer unmounts", () => {
    let observedSignal: AbortSignal | undefined;
    const { unmount } = renderHook(() =>
      useFetch<string>((signal) => {
        observedSignal = signal;
        return new Promise(() => undefined);
      }, []),
    );
    unmount();
    expect(observedSignal?.aborted).toBe(true);
  });
});

describe("errorText", () => {
  it("keeps public API messages and hides unknown errors", () => {
    const apiError = new ApiError(400, {
      error: { code: "bad_request", message: "Исправьте форму" },
      request_id: "request-3",
    });
    expect(errorText(apiError)).toBe("Исправьте форму");
    expect(errorText(new Error("database password"))).toBe(
      "Что-то пошло не так. Повторите попытку.",
    );
  });
});
