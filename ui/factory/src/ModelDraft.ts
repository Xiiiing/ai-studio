import { useSyncExternalStore } from "react";
import type { QueryClient } from "@tanstack/react-query";
import type { RecordData } from "./api";

type ModelDraft = {
  draft: RecordData | null;
  base: string;
  dirty: boolean;
  saving: boolean;
  error: string;
  generation: number;
  probes: RecordData;
  probing: string;
  setupMode: string | null;
};

// The query client lives above routing. Keep drafts with that application,
// without placing private configuration in browser storage or a global cache.
const stores = new WeakMap<QueryClient, ReturnType<typeof createStore>>();
function createStore() {
  let state: ModelDraft = {
    draft: null,
    base: "",
    dirty: false,
    saving: false,
    error: "",
    generation: 0,
    probes: {},
    probing: "",
    setupMode: null,
  };
  const listeners = new Set<() => void>();
  const guard = (event: BeforeUnloadEvent) => {
    if (!state.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  };
  return {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    update: (patch: Partial<ModelDraft>) => {
      const wasDirty = state.dirty;
      state = { ...state, ...patch };
      if (state.dirty !== wasDirty) {
        if (state.dirty) window.addEventListener("beforeunload", guard);
        else window.removeEventListener("beforeunload", guard);
      }
      listeners.forEach((listener) => listener());
    },
  };
}

export function useModelDraft(client: QueryClient) {
  let store = stores.get(client);
  if (!store) {
    store = createStore();
    stores.set(client, store);
  }
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot);
  return { ...snapshot, store };
}
