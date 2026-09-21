import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  FlaskConical,
  Layers3,
  PanelLeft,
  Plus,
  Settings2,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  MessageSquare,
  LockKeyhole,
  ChevronDown,
  Clock3,
  History,
} from "lucide-react";
import { api, enc, watchJob } from "./api";
import {
  Composer,
  Dialog,
  ErrorBox,
  Status,
  Working,
  MisconceptionCheck,
} from "./components";
import {
  Designer,
  Library,
  Progress,
  Research,
  SettingsPanel,
  Assessment,
} from "./views";
import { ACTIONS, TeachingBar, TeachingCard, TeachingPreferences } from "./teaching";
import { AuroraMark, CourseOutline, LessonEvent } from "./workspace";
import type { Detail, Job, Message, Subject, Thread, LearnerPreferences, TeachingAction } from "./types";
type View = "learn" | "library" | "research" | "progress";
export default function App() {
  const [view, setView] = useState<View>("learn");
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [subject, setSubject] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [node, setNode] = useState("");
  const [threads, setThreads] = useState<Thread[]>([]);
  const [thread, setThread] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [online, setOnline] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [focusMode, setFocusMode] = useState(false);
  const [design, setDesign] = useState(false);
  const [settings, setSettings] = useState(false);
  const [history, setHistory] = useState(false);
  const [personalize, setPersonalize] = useState(false);
  const [preferences, setPreferences] = useState<LearnerPreferences | null>(null);
  const sending = useRef(false);
  const [assessment, setAssessment] = useState<"quiz" | "teach-back" | null>(
    null,
  );
  const [designTopic, setDesignTopic] = useState("");
  const scroll = useRef<HTMLDivElement>(null);
  const selected = useRef("");
  const active = useRef("");
  const watching = useRef(new Set<string>());
  const threadsReady = useRef("");
  const advanced = useRef("");
  const recapped = useRef("");
  const sawLearner = useRef("");
  const autoStreak = useRef(0);
  const RECAP_PROMPT =
    "I'm back after a break. Give me a short recap of where we left off, then ask me one easy question from it.";
  const ADVANCE_PROMPT =
    "Keep teaching me: take the next small step from my last answer.";
  const busy =
    job !== null &&
    ![
      "completed",
      "done",
      "success",
      "failed",
      "error",
      "blocked",
      "interrupted",
      "cancelled",
      "model-error",
    ].includes(job.status);
  const current = detail?.spec.nodes.find((n) => n.id === node);
  const mastery = detail?.state.nodes || {};
  const pct = detail?.spec.nodes.length
    ? Math.round(
        (detail.spec.nodes.reduce(
          (s, n) => s + (mastery[n.id]?.mastery || 0),
          0,
        ) /
          detail.spec.nodes.length) *
          100,
      )
    : 0;
  async function refreshSubjects() {
    const d = await api<{ subjects: Subject[] }>("/subjects");
    setSubjects(d.subjects);
    return d.subjects.filter((s) => s.status === "compiled");
  }
  async function refreshDetail(id: string) {
    const d = await api<Detail>("/subjects/" + enc(id));
    if (selected.current === id) {
      setDetail(d);
      const remembered = localStorage.getItem("open-tutor-node:" + id);
      setNode((old) =>
        d.spec.nodes.some((n) => n.id === remembered)
          ? remembered!
          : d.spec.nodes.some((n) => n.id === old)
            ? old
            : d.spec.nodes[0]?.id || "",
      );
    }
    return d;
  }
  async function readThread(id: string) {
    const d = await api<{
      thread: Thread;
      messages: Message[];
      active_job?: string | { id: string };
    }>("/threads/" + enc(id));
    if (active.current !== id) return;
    if (selected.current)
      localStorage.setItem("open-tutor-thread:" + selected.current, id);
    setMessages(d.messages);
    if (d.active_job) {
      const jid =
        typeof d.active_job === "string" ? d.active_job : d.active_job.id;
      void monitor(jid, id);
    }
  }
  async function monitor(id: string, tid: string) {
    if (watching.current.has(id)) return;
    watching.current.add(id);
    try {
      const done = await watchJob(id, (j) => {
        if (active.current === tid) setJob(j);
      });
      if (active.current === tid) {
        await readThread(tid);
        if (selected.current) await refreshDetail(selected.current);
        if (
          ["failed", "error", "interrupted", "blocked", "model-error"].includes(
            done.status,
          )
        )
          setError(
            done.error ||
              "This response could not be completed. Your question is saved.",
          );
        setJob(null);
      }
    } catch (e) {
      setError(String((e as Error).message));
      setJob(null);
    } finally {
      watching.current.delete(id);
    }
  }
  async function chooseSubject(id: string) {
    selected.current = id;
    localStorage.setItem("open-tutor-subject", id);
    setSubject(id);
    setDetail(null);
    setMessages([]);
    setThread("");
    threadsReady.current = "";
    active.current = "";
    setJob(null);
    setLoading(true);
    setError("");
    try {
      await refreshDetail(id);
      const ts = await api<{ threads: Thread[] }>(
        "/threads?subject=" + enc(id),
      );
      if (selected.current !== id) return;
      setThreads(ts.threads);
      const remembered =
        ts.threads.find(
          (t) => t.id === localStorage.getItem("open-tutor-thread:" + id),
        ) || ts.threads[0];
      if (remembered) {
        active.current = remembered.id;
        setThread(active.current);
        await readThread(active.current);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      if (selected.current === id) setLoading(false);
    }
  }
  useEffect(() => {
    let live = true;
    api<LearnerPreferences>("/learner/preferences").then(p => { if (live) setPreferences(p); }).catch(() => { /* Dialog exposes load errors without blocking the library. */ });
    Promise.all([api<{ ok: boolean }>("/health"), refreshSubjects()])
      .then(([h, list]) => {
        if (!live) return;
        setOnline(h.ok);
        const remembered =
          list.find(
            (s) => s.subject === localStorage.getItem("open-tutor-subject"),
          ) || list[0];
        if (remembered) void chooseSubject(remembered.subject);
        else setLoading(false);
      })
      .catch((e) => {
        if (live) {
          setError(e.message);
          setLoading(false);
        }
      });
    return () => {
      live = false;
    };
  }, []);
  useEffect(() => {
    const container = scroll.current;
    if (!container) return;
    const items = container.querySelectorAll<HTMLElement>(".message");
    const last = items[items.length - 1];
    if (last?.classList.contains("assistant")) {
      container.scrollTop +=
        last.getBoundingClientRect().top -
        container.getBoundingClientRect().top -
        16;
    } else container.scrollTop = container.scrollHeight;
  }, [messages, job?.stage]);
  useEffect(() => {
    // Maestro-style welcome: a fresh curriculum greets the learner with a
    // tutor-led opening turn instead of an empty chat. Fires once per
    // curriculum, after the remembered thread settles and only when there is
    // truly nothing on screen yet; failures stay silent (the learner can
    // always tap "Start a guided lesson" themselves).
    if (!subject || !detail || busy || loading) return;
    // The loading gate matters: on a returning visit, detail arrives before
    // the remembered thread's readback. Without the gate the opener saw an
    // "empty" chat and greeted into a junk thread, stealing the welcome-back
    // recap's turn. Once loading clears, messages.length below decides.
    if (messages.length > 0 || job) return;
    if (threadsReady.current === subject) return;
    threadsReady.current = subject;
    // One ask path for clicks and the auto-opener: performAsk claims the send
    // slot synchronously (send re-checks it synchronously too), creating the
    // session first when needed — a click landing in the same beat can never
    // double-fire.
    void performAsk(
      thread || active.current || null,
      `Start a guided lesson on ${current?.title || detail.spec.title}. Teach one clear idea, then ask me one question to find my starting point.`,
    );
  }, [subject, detail, messages.length, job, busy, loading, thread, current]);
  const [lastVisit, setLastVisit] = useState<number | null>(null);
  const [autoAdvance, setAutoAdvance] = useState(
    () => localStorage.getItem("open-tutor-auto-advance") !== "off",
  );
  useEffect(() => {
    // Maestro-style welcome-back: remember when this learner was last here.
    // `lastVisit` keeps the previous visit's timestamp so `away` is the real
    // gap since the previous visit; the current visit's time is written on
    // every subject switch and again on unload.
    if (!subject) return;
    const key = "open-tutor-last-visit:" + subject;
    const prev = localStorage.getItem(key);
    setLastVisit(prev ? Number(prev) : null);
    localStorage.setItem(key, String(Date.now()));
    const touch = () => localStorage.setItem(key, String(Date.now()));
    window.addEventListener("beforeunload", touch);
    return () => {
      window.removeEventListener("beforeunload", touch);
      touch();
    };
  }, [subject]);
  const away = lastVisit ? Math.floor((Date.now() - lastVisit) / 36e5) : 0;
  const assistantMsgs = useMemo(
    () => messages.filter((m) => m.role === "assistant"),
    [messages],
  );
  const learnerEngaged = useMemo(
    () =>
      messages.some(
        (m) =>
          m.role === "user" &&
          m.content !== ADVANCE_PROMPT &&
          m.content !== RECAP_PROMPT &&
          !ACTIONS.some((a) => a.question === m.content) &&
          !m.content.startsWith("Start a guided lesson on "),
      ),
    [messages],
  );
  // Two softness tiers, Maestro-style: after a long absence (20h+) the tutor
  // simply catches the learner up, unprompted; after a shorter gap the
  // banner offers the same thing one tap away. Both need a session with real
  // teaching in it, and both step aside once the learner is back in flow.
  const showWelcomeBack =
    away >= 2 &&
    away < 20 &&
    Boolean(thread) &&
    assistantMsgs.length > 0 &&
    !job &&
    !learnerEngaged &&
    recapped.current !== thread;
  useEffect(() => {
    // Welcome-back recap: after a real absence (20h+) the tutor eases the
    // learner back in with a short recap and one easy question, once per
    // visit. If something is in flight it waits for the next quiet render.
    if (busy || job || sending.current || !thread) return;
    if (away < 20 || assistantMsgs.length === 0) return;
    if (recapped.current === thread) return;
    recapped.current = thread;
    void send(RECAP_PROMPT);
  }, [away, thread, busy, job, assistantMsgs.length, send]);
  useEffect(() => {
    // Maestro-style auto-advance: after the learner answers, keep the lesson
    // moving — a short paced pause, then the tutor picks the thread back up.
    // Canned host asks never start the loop, one nudge per tutor turn, and
    // at most two consecutive self-driven steps before the tutor waits for
    // the learner again (no runaway loops in a forgotten tab).
    if (!autoAdvance || busy || job || sending.current) return;
    const userMsgs = messages.filter((m) => m.role === "user");
    const lastUser = userMsgs[userMsgs.length - 1];
    if (lastUser && sawLearner.current !== lastUser.id) {
      sawLearner.current = lastUser.id;
      if (
        lastUser.content !== ADVANCE_PROMPT &&
        lastUser.content !== RECAP_PROMPT
      )
        autoStreak.current = 0;
    }
    if (!learnerEngaged) return;
    const last = assistantMsgs[assistantMsgs.length - 1];
    if (!last || advanced.current === last.id) return;
    if (autoStreak.current >= 2) return;
    const t = setTimeout(() => {
      advanced.current = last.id;
      const chainAuto =
        !!lastUser &&
        (lastUser.content === ADVANCE_PROMPT ||
          lastUser.content === RECAP_PROMPT);
      // The step after a real answer is self-driven step 1, not a free pass.
      autoStreak.current = chainAuto ? autoStreak.current + 1 : 1;
      void send(ADVANCE_PROMPT);
    }, 2600);
    return () => clearTimeout(t);
  }, [autoAdvance, busy, job, learnerEngaged, messages, assistantMsgs, send]);
  async function newThread() {
    if (!subject) return;
    const t = await api<Thread>("/threads", { subject });
    setThread(t.id);
    active.current = t.id;
    localStorage.setItem("open-tutor-thread:" + subject, t.id);
    setMessages([]);
    setThreads((old) => [t, ...old]);
    setJob(null);
    setHistory(false);
    return t.id;
  }
  async function performAsk(
    forcedId: string | null,
    question: string,
    action: TeachingAction = "respond",
  ) {
    if (busy || !subject || sending.current) return false;
    sending.current = true;
    let accepted = false;
    setError("");
    setJob({
      id: "pending",
      status: "running",
      stage: "Preparing your question",
    });
    try {
      const id = forcedId || thread || (await newThread());
      if (!id) throw new Error("Choose a curriculum first.");
      const result = await api<{ job_id: string }>(
        "/threads/" + enc(id) + "/ask",
        { question, teaching: true, action, ...(node ? { node_id: node } : {}) },
      );
      accepted = true;
      setJob({
        id: result.job_id,
        status: "running",
        stage: "Retrieving evidence",
      });
      // The ask was accepted: start monitoring before anything that can throw,
      // so a failed readback can never orphan the running job.
      void monitor(result.job_id, id);
      await readThread(id);
      return true;
    } catch (e) {
      setError((e as Error).message);
      // An accepted ask is still running server-side: keep its live indicator
      // so the monitor owns the outcome; only an unaccepted ask clears it.
      if (!accepted) setJob(null);
      return accepted;
    } finally {
      sending.current = false;
    }
  }
  async function send(question: string, action: TeachingAction = "respond") {
    return performAsk(null, question, action);
  }
  function adapt(action: TeachingAction) {
    const request = ACTIONS.find(a => a.id === action);
    if (request) void send(request.question, action);
  }
  function focusReply() {
    document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Your question"]')?.focus();
  }
  function selectNode(id: string) {
    localStorage.setItem("open-tutor-node:" + subject, id);
    setNode(id);
  }
  function startDesign(topic = "") {
    setDesignTopic(topic);
    setDesign(true);
  }
  const nav = [
    ["learn", "Learn", BookOpen],
    ["library", "Library", Layers3],
    ["research", "Research", FlaskConical],
    ["progress", "Progress", TrendingUp],
  ] as const;
  const rail = (
    <>
      <div className="rail-heading">
        <span className="eyebrow">Your learning path</span>
        <button
          className="icon-button"
          title="New curriculum"
          aria-label="New curriculum"
          onClick={() => startDesign()}
        >
          <Plus size={16} />
        </button>
      </div>
      <button
        className="subject-switch"
        onClick={() => {
          setView("library");
          setDrawer(false);
        }}
      >
        <span className="subject-icon">
          <AuroraMark />
        </span>
        <span>
          <strong>
            {detail?.spec.title.split("—")[0] || "Choose your direction"}
          </strong>
          <small className="mono muted">
            {detail
              ? `${detail.spec.nodes.length} concepts · ${detail.spec.corpus.length} sources`
              : "A curriculum built around you"}
          </small>
        </span>
        <ChevronDown size={15} />
      </button>
      {detail && (
        <>
          <div className="subject-progress">
            <div className="row between">
              <span title="An estimate from verified practice, not course completion">Practice mastery</span>
              <span className="mono">{pct}%</span>
            </div>
            <div className="track">
              <div style={{ width: pct + "%" }} />
            </div>
          </div>
          <div className="rail-heading concepts-title">
            <span className="eyebrow">Course outline</span>
            <span className="mono muted">
              {detail.spec.nodes.length.toString().padStart(2, "0")}
            </span>
          </div>
          <CourseOutline key={subject} detail={detail} selected={node} onSelect={(id) => {
            selectNode(id);
            setDrawer(false);
            setView("learn");
          }} />
        </>
      )}
      <div className="rail-bottom">
        <div className="quiet-note">
          <ShieldCheck size={16} />
          <p>
            Curiosity is yours.
            <br />
            <span>Checking the evidence is ours.</span>
          </p>
        </div>
        <button className="secondary full" onClick={() => startDesign()}>
          <Plus size={15} /> Design a curriculum
        </button>
      </div>
    </>
  );
  return (
    <>
      <div className="sky" aria-hidden="true">
        <div className="band b1" />
        <div className="band b2" />
        <div className="band b3" />
        <div className="band b4" />
        <div className="stars" />
      </div>
      <div className="app">
        <header className="navigation-dock">
          <button
            className="brand"
            onClick={() => setView("learn")}
            aria-label="Open Tutor home"
          >
            <span className="brand-glyph">
              <AuroraMark />
            </span>
            <span className="brand-name">Open Tutor<span>Your learning studio</span></span>
            <span className="brand-version">AURORA</span>
          </button>
          <nav aria-label="Main navigation">
            {nav.map(([id, label, Icon]) => (
              <button
                className={view === id ? "active" : ""}
                aria-current={view === id ? "page" : undefined}
                title={label}
                key={id}
                onClick={() => setView(id)}
              >
                <Icon size={15} />
                <span>{label}</span>
              </button>
            ))}
          </nav>
          <div className="top-actions">
            <span className={"connection " + (online ? "connected" : "")}>
              <span />
              {online ? "Local" : "Offline"}
            </span>
            <button
              className="icon-button"
              aria-label="Settings"
              onClick={() => setSettings(true)}
            >
              <Settings2 size={17} /><span>Settings</span>
            </button>
          </div>
        </header>
        <main
          className={"workspace " + (view === "learn" ? "learning-layout" : "") + (focusMode ? " focus-layout" : "")}
        >
          {view === "learn" && !focusMode && <aside className="rail" aria-label="Learning path">{rail}</aside>}
          <section className="main-pane">
            {error && <ErrorBox error={error} onDismiss={() => setError("")} />}
            {view === "learn" && (
              <>
                <div className="session-toolbar">
                  <div className="row">
                    <button className="icon-button desktop-only" aria-label={focusMode ? "Show learning path" : "Hide learning path"} aria-expanded={!focusMode} title={focusMode ? "Show learning path" : "Focus on the lesson"} onClick={() => setFocusMode(!focusMode)}><PanelLeft size={18} /></button>
                    <button
                      className="icon-button mobile-only"
                      aria-label="Open concept map"
                      onClick={() => setDrawer(true)}
                    >
                      <PanelLeft size={18} />
                    </button>
                    <span className="session-breadcrumb">
                      <span>{detail?.spec.title.split("—")[0] || "Learning space"}</span><span className="breadcrumb-divider">/</span>{" "}
                      <strong>{current?.title || "A fresh start"}</strong>
                    </span>
                  </div>
                  <div className="row">
                    <button
                      className="icon-button"
                      aria-label="Session history"
                      onClick={() => setHistory(true)}
                    >
                      <Clock3 size={16} />
                    </button>
                    <button
                      className="secondary small"
                      aria-label="New session"
                      disabled={!subject || busy}
                      onClick={() =>
                        void newThread().catch((e) => setError(e.message))
                      }
                    >
                      <Plus size={14} />
                      <span>New session</span>
                    </button>
                  </div>
                </div>
                <div className="chat-scroll" ref={scroll}>
                  {loading ? (
                    <div className="loading-state">
                      <Working stage="Opening your learning space" />
                    </div>
                  ) : !detail ? (
                    <div className="welcome">
                      <div className="orbit-emblem" aria-hidden="true"><div /><div /><AuroraMark large /></div>
                      <span className="eyebrow">
                        A space for your curiosity
                      </span>
                      <h1>
                        A little more
                        <br />
                        <em>understood.</em>
                      </h1>
                      <p>
                        Follow a question. Find the evidence.
                        <br />
                        Build understanding that stays with you.
                      </p>
                      <button className="primary" onClick={() => startDesign()}>
                        Design your first curriculum <ArrowRight size={16} />
                      </button>
                      <div className="starter-topics">
                        <span className="mono muted">
                          A FEW PLACES TO BEGIN
                        </span>
                        {[
                          "Quantum computing",
                          "Calculus",
                          "Music theory",
                          "Chemistry",
                        ].map((t) => (
                          <button key={t} onClick={() => startDesign(t)}>
                            {t}
                            <ArrowRight size={12} />
                          </button>
                        ))}
                      </div>
                      <div className="trust-row">
                        <span>
                          <ShieldCheck size={14} /> Evidence, not confidence
                        </span>
                        <span>
                          <LockKeyhole size={14} /> Learning history stays here
                        </span>
                      </div>
                    </div>
                  ) : (
                    <>
                      {messages.length === 0 && (
                        <div className="lesson-intro">
                          <div className="eyebrow">
                            <span className="tiny-orb" /> Let's make sense of it
                          </div>
                          <h1>{current?.title || detail.spec.title}</h1>
                          <span className="mono muted">
                            Your curriculum · a conversation, not a lecture
                          </span>
                          <p className="lesson-description">
                            {current?.defn ||
                              String(
                                detail.spec.scope.goal ||
                                  "Explore your curriculum, one concept at a time.",
                              )}
                          </p>
                          <div className="intro-meta">
                            <Status status={current?.status || "unverified"} />
                            <span className="mono muted">
                              {current?.grounding_corpus.length || 0} linked
                              sources
                            </span>
                            {current?.oracle && (
                              <span className="mono muted">
                                T3 executable key
                              </span>
                            )}
                          </div>
                          {current &&
                            detail.gates[current.id]?.unlocked === false && (
                              <div className="callout">
                                <LockKeyhole size={15} />
                                <div>
                                  <strong>
                                    Explore freely. Build the foundations first.
                                  </strong>
                                  <p>
                                    Practice unlocks after{" "}
                                    {detail.gates[current.id].blocking_prereqs
                                      .map(
                                        (id) =>
                                          detail.spec.nodes.find(
                                            (n) => n.id === id,
                                          )?.title || id,
                                      )
                                      .join(", ")}
                                    . You can still read and ask questions.
                                  </p>
                                </div>
                              </div>
                            )}
                          <div className="suggestion-grid">
                           <button
                             onClick={() =>
                               void send(
                                 `Start a guided lesson on ${current?.title || detail.spec.title}. Teach one clear idea, then ask me one question to find my starting point.`,
                               )
                             }
                              disabled={busy}
                            >
                              <BookOpen size={18} />
                              <strong>Start a guided lesson</strong>
                              <span>One idea. One question. Find what clicks.</span>
                              <ArrowRight size={15} />
                            </button>
                            <button
                              onClick={() => setAssessment("teach-back")}
                              disabled={
                                !current ||
                                detail.gates[node]?.unlocked === false
                              }
                            >
                              <MessageSquare size={18} />
                              <strong>Try a teach-back</strong>
                              <span>See what you understand so far</span>
                              <ArrowRight size={15} />
                            </button>
                          </div>
                        </div>
                      )}
                      {showWelcomeBack && (
                        <div className="callout welcome-back">
                          <History size={15} />
                          <div>
                            <strong>
                              Welcome back — it's been{" "}
                              {away > 72
                                ? Math.round(away / 24) + " days"
                                : away + "h"}
                            </strong>
                            <p>
                              Want a quick recap before we continue?{" "}
                              <button
                                className="linklike"
                                onClick={() => {
                                  recapped.current = thread;
                                  void send(RECAP_PROMPT);
                                }}
                                disabled={busy}
                              >
                                Catch me up
                              </button>
                            </p>
                          </div>
                        </div>
                      )}
                      {current && (
                        <MisconceptionCheck
                          triggered={
                            mastery[current.id]?.misconceptions_triggered || []
                          }
                          misconceptions={current.misconceptions}
                        />
                      )}
                      {messages.map((m, i) => (
                        <div className={"message " + m.role} key={m.id}>
                          {m.role === "user" ? (
                            <>
                              <LessonEvent content={m.content} />
                            </>
                          ) : (
                            <>
                              <div className="message-label">
                                <span className="mini-brand"><AuroraMark /></span><span>Open Tutor</span><span className="message-subtitle">Your learning companion</span>
                              </div>
                              {m.answer ? (
                                <TeachingCard answer={m.answer} busy={busy} onAction={i === messages.length - 1 ? adapt : undefined} onReply={i === messages.length - 1 ? focusReply : undefined} />
                              ) : (
                                <div className="answer-card">
                                  <p>{m.content}</p>
                                </div>
                              )}
                            </>
                          )}
                        </div>
                      ))}
                      {busy && <Working stage={job?.stage || "Verifying"} />}
                      {messages.length > 0 && !busy && current && (
                        <div className="followup-actions">
                          <button
                            onClick={() => setAssessment("quiz")}
                            disabled={detail.gates[node]?.unlocked === false}
                          >
                            <Sparkles size={14} /> Check my understanding
                          </button>
                          <button
                            onClick={() => setAssessment("teach-back")}
                            disabled={detail.gates[node]?.unlocked === false}
                          >
                            <MessageSquare size={14} /> Explain it back
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </div>
                {detail && (
                  <>
                  <div className="composer-dock"><TeachingBar preferences={preferences} onOpen={() => setPersonalize(true)} />
                  <Composer
                    onSend={send}
                    busy={busy}
                    disabled={loading}
                    placeholder={
                      current
                        ? `Your thoughts on ${current.title.toLowerCase()}…`
                        : undefined
                    }
                  /></div>
                  </>
                )}
              </>
            )}
            {view === "library" && (
              <Library
                subjects={subjects}
                onChoose={(id) => {
                  setView("learn");
                  void chooseSubject(id);
                }}
                onDesign={() => startDesign()}
                selected={detail}
                onRefresh={async () => {
                  await refreshSubjects();
                  if (subject) await refreshDetail(subject);
                }}
              />
            )}
            {view === "research" && (
              <Research detail={detail} onDesign={(t) => startDesign(t)} />
            )}
            {view === "progress" && (
              <Progress
                detail={detail}
                onPractice={(id) => {
                  selectNode(id);
                  setView("learn");
                  setAssessment("quiz");
                }}
                onExplore={() => setView("library")}
              />
            )}
          </section>
        </main>
        <footer className="app-foot">
          <span>
            <span className="tiny-orb" /> BUILT FOR UNDERSTANDING
          </span>
          <span>Your pace. Your questions. Your space.</span>
          <span>LOCAL-FIRST / T1—T4</span>
        </footer>
      </div>
      {drawer && (
        <Dialog title="Your concept map" onClose={() => setDrawer(false)}>
          <div className="drawer-content">{rail}</div>
        </Dialog>
      )}
      {design && (
        <Designer
          initialTopic={designTopic}
          onClose={() => setDesign(false)}
          onComplete={async (id) => {
            await refreshSubjects();
            setDesign(false);
            setView("learn");
            if (id) await chooseSubject(id);
          }}
        />
      )}
      {settings && <SettingsPanel onClose={() => setSettings(false)} />}
      {personalize && <TeachingPreferences onClose={() => setPersonalize(false)} onSaved={p => { setPreferences(p); setPersonalize(false); }} onFlowChange={f => setAutoAdvance(f)} />}
      {assessment && detail && current && (
        <Assessment
          kind={assessment}
          subject={subject}
          node={current}
          onClose={() => setAssessment(null)}
          onGraded={() => void refreshDetail(subject)}
        />
      )}
      {history && (
        <Dialog title="Your sessions" onClose={() => setHistory(false)}>
          <div className="dialog-body">
            {threads.length ? (
              threads.map((t) => (
                <button
                  className="history-item"
                  key={t.id}
                  onClick={() => {
                    setThread(t.id);
                    active.current = t.id;
                    setMessages([]);
                    setJob(null);
                    void readThread(t.id).catch((e) => setError(e.message));
                    setHistory(false);
                  }}
                >
                  <MessageSquare size={17} />
                  <span>
                    <strong>{t.title}</strong>
                    <small className="mono muted">
                      {new Date(t.updated_at).toLocaleString()}
                    </small>
                  </span>
                  <ArrowRight size={15} />
                </button>
              ))
            ) : (
              <p className="muted">
                Your conversations will appear here after your first question.
              </p>
            )}
          </div>
        </Dialog>
      )}
    </>
  );
}
