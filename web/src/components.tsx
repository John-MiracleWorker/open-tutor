import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  ArrowUp,
  Check,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  ShieldCheck,
  TriangleAlert,
  X,
  Braces,
} from "lucide-react";
import type { Answer, Citation } from "./types";
export function Status({
  status,
  grounded,
}: {
  status: string;
  grounded?: boolean;
}) {
  const success =
    grounded !== false && ["grounded", "compiled", "live"].includes(status);
  const value =
    grounded === false && status === "grounded" ? "unverified" : status;
  const failure = [
    "no-node",
    "oracle-failed",
    "failed",
    "dead",
    "error",
    "blocked",
  ].includes(value);
  return (
    <span className={"status " + (success ? "ok" : failure ? "bad" : "warn")}>
      {success ? <Check size={11} /> : <TriangleAlert size={11} />}
      <span className={success ? "ok" : failure ? "bad" : "warn"}>
        {value.replaceAll("-", " ").toUpperCase()}
      </span>
    </span>
  );
}
// Logical nesting survives portals; DOM insertion order does not.
const DialogDepth = createContext(0);
let dialogLocks = 0;
let overflowBeforeDialogs = "";
function topDialog() {
  return Array.from(document.querySelectorAll<HTMLElement>("[data-dialog-depth]"))
    .sort((a, b) => Number(a.dataset.dialogDepth) - Number(b.dataset.dialogDepth))
    .at(-1);
}
export function Dialog({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const depth = useContext(DialogDepth) + 1;
  const box = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const el = box.current;
    const get = () =>
      Array.from(
        el?.querySelectorAll<HTMLElement>(
          'button:not(:disabled),a[href],input,select,textarea,[tabindex="0"]',
        ) || [],
      );
    if (topDialog() === el) get()[0]?.focus();
    const key = (e: KeyboardEvent) => {
      if (topDialog() !== el) return;
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopImmediatePropagation();
        close.current();
      }
      if (e.key === "Tab") {
        const list = get();
        const first = list[0],
          last = list[list.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    if (dialogLocks++ === 0) overflowBeforeDialogs = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", key);
      if (--dialogLocks === 0) document.body.style.overflow = overflowBeforeDialogs;
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return createPortal(
    <DialogDepth.Provider value={depth}>
      <div
        className="modal-backdrop"
        style={{ zIndex: 1000 + depth }}
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <div
          ref={box}
          role="dialog"
          data-dialog-depth={depth}
          aria-modal="true"
          aria-label={title}
          className={"modal " + (wide ? "wide" : "")}
        >
          <div className="modal-head">
            <h2>{title}</h2>
            <button
              className="icon-button"
              aria-label="Close dialog"
              onClick={onClose}
            >
              <X size={19} />
            </button>
          </div>
          {children}
        </div>
      </div>
    </DialogDepth.Provider>,
    document.body,
  );
}
export function safeURL(url: string) {
  try {
    const u = new URL(url);
    return ["https:", "http:"].includes(u.protocol) ? u.href : null;
  } catch {
    return null;
  }
}
export function Evidence({
  citations,
  selected,
  onSelect,
}: {
  citations: Citation[];
  selected?: number | null;
  onSelect?: (n: number | null) => void;
}) {
  const [internal, setInternal] = useState<number | null>(null);
  const open = selected === undefined ? internal : selected;
  const setOpen = onSelect || setInternal;
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selected != null)
      box.current
        ?.querySelector(".evidence-detail")
        ?.scrollIntoView?.({ block: "nearest" });
  }, [selected]);
  return (
    <div className="evidence" ref={box}>
      <div className="eyebrow">
        Evidence{" "}
        <span className="mono">
          {citations.length.toString().padStart(2, "0")}
        </span>
      </div>
      {citations.map((c, i) => (
        <div className="evidence-item" key={c.source_id + "-" + i}>
          <button
            className="evidence-toggle"
            aria-expanded={open === i}
            onClick={() => setOpen(open === i ? null : i)}
          >
            <span className="evidence-number">
              {String(i + 1).padStart(2, "0")}
            </span>
            <span>{c.source_name}</span>
            {open === i ? (
              <ChevronDown size={14} />
            ) : (
              <ChevronRight size={14} />
            )}
          </button>
          {open === i && (
            <div className="evidence-detail">
              <blockquote>{c.text}</blockquote>
              <div className="row between">
                <span className="mono muted">
                  chars {c.char_start}–{c.char_end}
                </span>
                {safeURL(c.url) && (
                  <a
                    href={safeURL(c.url)!}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Read source <ExternalLink size={12} />
                  </a>
                )}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
function Inline({
  text,
  onCitation,
}: {
  text: string;
  onCitation?: (n: number) => void;
}) {
  return (
    <>
      {text.split(/(\[\d+\])/g).map((s, i) =>
        /^\[\d+\]$/.test(s) ? (
          <sup className="citation-marker" key={i}>
            {onCitation ? (
              <button
                type="button"
                aria-label={"Open evidence " + s.slice(1, -1)}
                onClick={() => onCitation(Number(s.slice(1, -1)) - 1)}
              >
                {s.slice(1, -1)}
              </button>
            ) : (
              s.slice(1, -1)
            )}
          </sup>
        ) : (
          s
        ),
      )}
    </>
  );
}
export function Prose({
  text,
  onCitation,
}: {
  text: string;
  onCitation?: (n: number) => void;
}) {
  let code = false;
  return (
    <div className="prose">
      {text.split("\n").map((line, i) => {
        if (line.startsWith("```")) {
          code = !code;
          return null;
        }
        if (!line.trim()) return null;
        if (code)
          return (
            <pre className="code-line" key={i}>
              {line}
            </pre>
          );
        if (/^#{1,3}\s/.test(line))
          return <h4 key={i}>{line.replace(/^#+\s/, "")}</h4>;
        if (line.startsWith(">"))
          return (
            <blockquote key={i}>
              <Inline
                text={line.replace(/^>\s?/, "")}
                onCitation={onCitation}
              />
            </blockquote>
          );
        return (
          <p key={i}>
            <Inline text={line} onCitation={onCitation} />
          </p>
        );
      })}
    </div>
  );
}
export function AnswerCard({ answer }: { answer: Answer }) {
  const [oracle, setOracle] = useState(false);
  const [selectedCitation, setSelectedCitation] = useState<number | null>(null);
  const prose = answer.citations.length
    ? answer.draft.replace(/\n## Citations[\s\S]*$/i, "")
    : answer.draft;
  const issues = [
    ...new Set([
      ...(answer.failures || []),
      ...(answer.verification?.issues || []),
    ]),
  ];
  return (
    <article
      className={"answer-card " + (!answer.grounded ? "not-grounded" : "")}
    >
      <div className="row between">
        <Status status={answer.status} grounded={answer.grounded} />
        <span className="answer-origin">
          <ShieldCheck size={13} /> deterministic gate
        </span>
      </div>
      <Prose
        text={prose}
        onCitation={(n) => {
          if (n >= 0 && n < answer.citations.length) setSelectedCitation(n);
        }}
      />
      {issues.length > 0 && (
        <div className="callout warn">
          <TriangleAlert size={15} />
          <div>
            <strong>Evidence has limits</strong>
            {issues.map((x) => (
              <p key={x}>{x}</p>
            ))}
          </div>
        </div>
      )}
      {answer.oracle?.result != null && (
        <div className="oracle">
          <button
            className="text-button row"
            onClick={() => setOracle(!oracle)}
            aria-expanded={oracle}
          >
            <Braces size={15} /> Executed answer key{" "}
            <span className="mono">{answer.oracle.oracle_name}</span>
            <ChevronDown size={14} />
          </button>
          {oracle && <pre>{JSON.stringify(answer.oracle.result, null, 2)}</pre>}
        </div>
      )}
      {answer.citations.length > 0 && (
        <Evidence
          citations={answer.citations}
          selected={selectedCitation}
          onSelect={setSelectedCitation}
        />
      )}
    </article>
  );
}
export function MisconceptionCheck({
  triggered,
  misconceptions,
}: {
  triggered: string[];
  misconceptions: { id: string; text: string }[];
}) {
  const known = misconceptions.filter((m) => triggered.includes(m.id));
  if (!known.length) return null;
  return (
    <aside className="callout warn">
      <TriangleAlert size={17} />
      <div>
        <strong>Misconception check</strong>
        <p>
          This idea was flagged during a learning interaction. Revisit the evidence before
          treating it as fact.
        </p>
        {known.map((m) => (
          <p key={m.id}>“{m.text}”</p>
        ))}
      </div>
    </aside>
  );
}
export function Composer({
  onSend,
  busy,
  placeholder = "Ask about a grounded concept…",
  disabled = false,
}: {
  onSend: (q: string) => void | Promise<boolean | void>;
  busy: boolean;
  placeholder?: string;
  disabled?: boolean;
}) {
  const [text, setText] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (!field.current) return;
    field.current.style.height = "auto";
    field.current.style.height = `${Math.min(field.current.scrollHeight, 160)}px`;
  }, [text]);
  const sending = useRef(false);
  async function send() {
    const q = text.trim();
    if (!q || busy || disabled || sending.current) return;
    sending.current = true;
    try {
      const accepted = await onSend(q);
      if (accepted !== false)
        setText((current) => (current.trim() === q ? "" : current));
    } finally {
      sending.current = false;
    }
  }
  return (
    <div className="composer-wrap">
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <textarea
          ref={field}
          aria-label="Your question"
          maxLength={12000}
          rows={1}
          placeholder={placeholder}
          value={text}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button
          className="send"
          type="submit"
          aria-label="Send question"
          disabled={busy || disabled || !text.trim()}
        >
          <ArrowUp size={20} />
        </button>
      </form>
      <div className="composer-foot">
        <ShieldCheck size={11} />
        <span>
          evidence checked separately · AI explanations labeled
        </span>
        <span className="desktop-only">↵ send</span>
      </div>
    </div>
  );
}
export function Working({ stage }: { stage: string }) {
  return (
    <div className="working" role="status">
      <span className="thinking">
        <i />
        <i />
        <i />
      </span>
      <span>
        {stage.replaceAll("_", " ").replaceAll("-", " ") ||
          "Working with the evidence"}
        …
      </span>
      <span className="muted">Saved as it completes</span>
    </div>
  );
}
export function ErrorBox({
  error,
  onDismiss,
}: {
  error: string;
  onDismiss?: () => void;
}) {
  return (
    <div className="error-box" role="alert">
      <TriangleAlert size={17} />
      <span>{error}</span>
      {onDismiss && (
        <button
          aria-label="Dismiss error"
          className="icon-button"
          onClick={onDismiss}
        >
          <X size={16} />
        </button>
      )}
    </div>
  );
}
