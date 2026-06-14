import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

/** Fetch a GET endpoint with loading/error state + a reload(). Pass null to skip. */
export function useApi<T = unknown>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const reload = useCallback(() => {
    if (!path) { setLoading(false); return; }
    setLoading(true);
    api<T>(path)
      .then((d) => { setData(d); setError(""); })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [path]);

  useEffect(() => { reload(); }, [reload]);
  return { data, loading, error, reload, setData };
}
