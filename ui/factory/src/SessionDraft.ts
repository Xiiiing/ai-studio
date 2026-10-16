import { useEffect, useSyncExternalStore } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { RecordData } from "./api";

type DraftState = { draft: RecordData | null; busy: boolean };
export function useShotDraft(projectId: string, shotId: string) {
  const client = useQueryClient();
  const key = ["shot-draft", projectId, shotId];
  useQuery<DraftState>({
    queryKey: key,
    queryFn: () => ({ draft: null, busy: false }),
    enabled: false,
    initialData: { draft: null, busy: false },
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const data = useSyncExternalStore(
    (listener) =>
      client.getQueryCache().subscribe((event) => {
        if (
          event.query.queryKey[0] === "shot-draft" &&
          event.query.queryKey[1] === projectId &&
          event.query.queryKey[2] === shotId
        )
          listener();
      }),
    () => client.getQueryData<DraftState>(key)!,
  );
  const update = (fn: (state: DraftState) => DraftState) =>
    client.setQueryData<DraftState>(key, (old) =>
      fn(old || { draft: null, busy: false }),
    );
  return {
    draft: data.draft,
    busy: data.busy,
    setDraft: (
      next: RecordData | null | ((old: RecordData | null) => RecordData),
    ) =>
      update((state) => ({
        ...state,
        draft: typeof next === "function" ? next(state.draft) : next,
      })),
    begin: () => {
      if (client.getQueryData<DraftState>(key)?.busy) return false;
      update((state) => ({ ...state, busy: true }));
      return true;
    },
    finish: () => update((state) => ({ ...state, busy: false })),
  };
}

export function useUnsavedShotGuard() {
  const client = useQueryClient();
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (
        client
          .getQueryCache()
          .findAll({ queryKey: ["shot-draft"] })
          .some((query) => (query.state.data as DraftState | undefined)?.draft)
      ) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [client]);
}
