// Минимальный хук загрузки данных без внешних библиотек.
// Отменяет запрос при размонтировании (AbortController), различает
// «загрузка / ошибка / данные».

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/api";

interface State<T> {
  data: T | null;
  error: string;
  loading: boolean;
  reload: () => void;
}

export function useFetch<T>(fetcher: (signal: AbortSignal) => Promise<T>, deps: unknown[]): State<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    fetcher(controller.signal)
      .then((result) => setData(result))
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить данные");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return { data, error, loading, reload };
}

export function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "Что-то пошло не так. Повторите попытку.";
}
