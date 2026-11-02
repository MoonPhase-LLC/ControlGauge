import { useCallback, useEffect, useState } from "react";

export type ServiceQuery<T> = {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
};

/**
 * Runs `load` while `enabled` (normally: the service is `ready`), and again on
 * `reload()` or when `key` changes. Same cancellation rule as
 * `useLocalService`: a request belongs to one effect run, and a stale one
 * (superseded, disabled, or unmounted) never writes its result.
 */
export function useServiceQuery<T>(load: () => Promise<T>, key: string, enabled: boolean): ServiceQuery<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(enabled);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    load()
      .then((result) => {
        if (cancelled) return;
        setData(result);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setData(null);
        setError(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // `load` is intentionally keyed by `key`: callers pass inline closures.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled, generation]);

  const reload = useCallback(() => setGeneration((n) => n + 1), []);
  return { data, error, loading, reload };
}
