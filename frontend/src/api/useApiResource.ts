import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./client";

export type ResourceState<T> =
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "success"; data: T };

export interface Resource<T> {
  state: ResourceState<T>;
  refetch: () => void;
}

/**
 * Fetches on mount and whenever `deps` change, plus on demand via `refetch()`.
 * No cache layer — the API surface is small and the dashboard is a
 * read-mostly operator view where an explicit refresh is preferable to
 * stale data.
 */
export function useApiResource<T>(
  fetcher: () => Promise<T>,
  deps: ReadonlyArray<unknown> = [],
): Resource<T> {
  const [state, setState] = useState<ResourceState<T>>({ status: "loading" });
  const [nonce, setNonce] = useState(0);
  const refetch = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });

    fetcher()
      .then((data) => {
        if (!cancelled) setState({ status: "success", data });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof ApiError ? err.message : "Unable to reach the RecoverAI API.";
        setState({ status: "error", error: message });
      });

    return () => {
      cancelled = true;
    };
    // `deps` is a caller-controlled dependency list (see JSDoc); `nonce`
    // drives manual refetch. `fetcher` is intentionally excluded — callers
    // pass an inline closure that changes every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { state, refetch };
}
