import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useReducer,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { AlertCircle, CheckCircle2, X } from "lucide-react";
import { text } from "./api";

type Entry = { kind: "error" | "notice"; message: string };
type State = { entries: Record<string, Entry>; dismissed: Set<string> };
type Change =
  | { type: "report"; id: string; entry?: Entry }
  | { type: "dismiss"; key: string };
const entryKey = (entry: Entry) => entry.kind + ":" + entry.message;
function reduce(state: State, change: Change): State {
  if (change.type === "dismiss")
    return { ...state, dismissed: new Set([...state.dismissed, change.key]) };
  const previous = state.entries[change.id];
  if (
    previous?.message === change.entry?.message &&
    previous?.kind === change.entry?.kind
  )
    return state;
  const entries = { ...state.entries };
  if (change.entry) entries[change.id] = change.entry;
  else delete entries[change.id];
  const active = new Set(Object.values(entries).map(entryKey));
  return {
    entries,
    dismissed: new Set([...state.dismissed].filter((key) => active.has(key))),
  };
}
const FeedbackContext = createContext<
  ((id: string, entry?: Entry) => void) | null
>(null);

function FeedbackCard({
  entry,
  onClose,
}: {
  entry: Entry;
  onClose: () => void;
}) {
  return (
    <div
      className={"feedback-card feedback-" + entry.kind}
      role={entry.kind === "error" ? "alert" : "status"}
      aria-atomic="true"
    >
      {entry.kind === "error" ? (
        <AlertCircle size={20} />
      ) : (
        <CheckCircle2 size={20} />
      )}
      <div className="feedback-copy">
        <strong>
          {entry.kind === "error" ? "操作或同步未完成" : "操作已完成"}
        </strong>
        <p tabIndex={0}>{entry.message}</p>
      </div>
      <button
        type="button"
        className="icon-button"
        aria-label={entry.kind === "error" ? "关闭错误提示" : "关闭提示"}
        onClick={onClose}
      >
        <X size={20} />
      </button>
    </div>
  );
}
function Region({ children }: { children: ReactNode }) {
  return createPortal(
    <aside className="feedback-region" aria-label="操作反馈">
      {children}
    </aside>,
    document.body,
  );
}

/** One overlay for all query/action feedback; identical active errors share a card. */
export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reduce, {
    entries: {},
    dismissed: new Set<string>(),
  });
  const report = useCallback((id: string, entry?: Entry) => {
    dispatch({ type: "report", id, entry });
  }, []);
  const entries = useMemo(() => {
    const unique = new Map<string, Entry>();
    Object.values(state.entries).forEach((entry) =>
      unique.set(entryKey(entry), entry),
    );
    return [...unique].filter(([key]) => !state.dismissed.has(key));
  }, [state]);
  return (
    <FeedbackContext.Provider value={report}>
      {children}
      {entries.length > 0 && (
        <Region>
          {entries.map(([key, entry]) => (
            <FeedbackCard
              key={key}
              entry={entry}
              onClose={() => dispatch({ type: "dismiss", key })}
            />
          ))}
        </Region>
      )}
    </FeedbackContext.Provider>
  );
}
function FloatingFeedback({ kind, message }: Entry) {
  const id = useId();
  const report = useContext(FeedbackContext);
  const [dismissed, setDismissed] = useState("");
  useEffect(() => {
    setDismissed("");
    report?.(id, message ? { kind, message } : undefined);
    return () => report?.(id);
  }, [id, kind, message, report]);
  if (report || !message || dismissed === message) return null;
  return (
    <Region>
      <FeedbackCard
        entry={{ kind, message }}
        onClose={() => setDismissed(message)}
      />
    </Region>
  );
}
export function FloatingErrorNotice({ error }: { error?: unknown }) {
  const message = error
    ? error instanceof Error
      ? error.message
      : text(error)
    : "";
  return <FloatingFeedback kind="error" message={message} />;
}
export function ActionNotice({ message }: { message: string }) {
  return <FloatingFeedback kind="notice" message={message} />;
}
