import { useEffect, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  Check,
  ChevronRight,
  Clock3,
  ExternalLink,
  FileText,
  Layers3,
  LockKeyhole,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { api, enc, watchJob } from "./api";
import { PathArtwork } from "./workspace";
import { Dialog, ErrorBox, safeURL, Status, Working } from "./components";
import type {
  Detail,
  Job,
  Node,
  Settings,
  Source,
  Spec,
  Subject,
} from "./types";
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
export function Designer({
  initialTopic,
  onClose,
  onComplete,
}: {
  initialTopic: string;
  onClose: () => void;
  onComplete: (id?: string) => void;
}) {
  const [topic, setTopic] = useState(initialTopic);
  const [level, setLevel] = useState("beginner");
  const [depth, setDepth] = useState("foundations");
  const [review, setReview] = useState(false);
  const [sources, setSources] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [inspecting, setInspecting] = useState(false);
  const terminal =
    job &&
    [
      "completed",
      "failed",
      "error",
      "model-error",
      "interrupted",
      "blocked",
      "cancelled",
    ].includes(job.status);
  const busy = Boolean(job && !terminal);
  async function follow(id: string) {
    try {
      const done = await watchJob(id, setJob);
      localStorage.removeItem("open-tutor-design-job");
      if (done.error) setError(done.error);
    } catch (e) {
      setError(message(e));
      setJob((old) => (old ? { ...old, status: "error" } : null));
    }
  }
  useEffect(() => {
    const id = localStorage.getItem("open-tutor-design-job");
    if (id) {
      setJob({
        id,
        status: "running",
        stage: "Reconnecting to your curriculum",
      });
      void follow(id);
    }
  }, []);
  async function submit() {
    setError("");
    try {
      const urls = sources
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      if (urls.some((u) => !safeURL(u)))
        throw new Error(
          "Each source must be a complete http:// or https:// URL.",
        );
      setJob({
        id: "pending",
        status: "running",
        stage: "Finding your starting point",
      });
      const { job_id } = await api<{ job_id: string }>("/design", {
        topic: topic.trim(),
        level,
        depth,
        review,
        sources: urls,
      });
      localStorage.setItem("open-tutor-design-job", job_id);
      void follow(job_id);
    } catch (e) {
      setError(message(e));
      setJob(null);
    }
  }
  const decision = job?.result?.decision;
  const ready = decision?.status === "compiled";
  if (inspecting && job?.result?.subject)
    return (
      <CurriculumReview
        subject={job.result.subject}
        onClose={() => setInspecting(false)}
        onChanged={() => onComplete(job.result?.subject)}
      />
    );
  return (
    <Dialog title="Follow your curiosity" onClose={onClose} wide>
      <div className="dialog-body designer">
        <div className="designer-heading">
          <span className="feature-icon">
            <Sparkles size={23} />
          </span>
          <p>
            A question becomes a path.
            <br />
            <span className="muted">
              We find the sources. The verifier checks the foundations.
            </span>
          </p>
        </div>
        {error && <ErrorBox error={error} />}
        {!job && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <label className="field">
              What would you like to learn?
              <input
                autoComplete="off"
                required
                maxLength={300}
                placeholder="e.g. How does quantum computing work?"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
              />
            </label>
            <div className="field-pair">
              <label className="field">
                Starting point
                <select
                  value={level}
                  onChange={(e) => setLevel(e.target.value)}
                >
                  <option value="beginner">I'm starting fresh</option>
                  <option value="intermediate">I know the basics</option>
                  <option value="advanced">Ready to go deeper</option>
                </select>
              </label>
              <label className="field">
                Depth
                <select
                  value={depth}
                  onChange={(e) => setDepth(e.target.value)}
                >
                  <option value="foundations">Build the foundations</option>
                  <option value="overview">Get the big picture</option>
                  <option value="deep">Explore in depth</option>
                </select>
              </label>
            </div>
            <details className="source-options">
              <summary>
                Bring your own sources <span className="muted">optional</span>
              </summary>
              <label className="field">
                Source URLs, one per line
                <textarea
                  rows={3}
                  placeholder="https://…"
                  value={sources}
                  onChange={(e) => setSources(e.target.value)}
                />
              </label>
              <p className="muted">
                Public textbooks, course pages, articles or papers. Metadata
                alone never counts as grounding.
              </p>
            </details>
            <label className="check-field">
              <input
                type="checkbox"
                aria-label="Review before adding to my library"
                checked={review}
                onChange={(e) => setReview(e.target.checked)}
              />
              <span>
                Review before adding to my library
                <small>
                  Optional. Verified curricula are normally ready automatically.
                </small>
              </span>
            </label>
            <div className="callout">
              <ShieldCheck size={18} />
              <p>
                Every concept needs substantive source evidence. Unsupported
                material stays out of your learning path.
              </p>
            </div>
            <button className="primary full" disabled={!topic.trim()}>
              Build my curriculum <ArrowRight size={16} />
            </button>
          </form>
        )}
        {busy && (
          <div className="design-running">
            <Working stage={job?.stage || "Researching"} />
            <div className="pipeline-steps">
              {[
                "Discover sources",
                "Extract & measure",
                "Map the concepts",
                "Verify & compile",
              ].map((s, i) => (
                <div key={s}>
                  <span className="mono">0{i + 1}</span>
                  {s}
                </div>
              ))}
            </div>
            <p className="muted">
              This can take a few minutes with a local model. You can close this
              window; the job continues on your server.
            </p>
          </div>
        )}
        {terminal && (
          <div className="design-result">
            <Status status={decision?.status || job?.status || "unverified"} />
            <h3>
              {ready
                ? "Your next chapter is ready."
                : decision?.status === "paused"
                  ? "Ready for your review."
                  : "The evidence needs another look."}
            </h3>
            <p className="muted">
              {ready
                ? "Your curriculum passed the deterministic checks and is saved in your library."
                : decision?.status === "paused"
                  ? "Verification passed. Review the sources and concepts before adding it."
                  : "Nothing unverified was added to your learning path. Inspect the report, adjust the scope or add stronger sources."}
            </p>
            {decision?.reasons?.map((r) => (
              <p className="mono warn" key={r}>
                {r}
              </p>
            ))}
            <div className="row wrap">
              {ready ? (
                <button
                  className="primary"
                  onClick={() => onComplete(job?.result?.subject)}
                >
                  Start exploring <ArrowRight size={15} />
                </button>
              ) : (
                <>
                  <button className="secondary" onClick={() => setJob(null)}>
                    Adjust & try again
                  </button>
                  {job?.result?.subject && (
                    <button
                      className="primary"
                      onClick={() => setInspecting(true)}
                    >
                      Inspect curriculum <ChevronRight size={15} />
                    </button>
                  )}
                </>
              )}
            </div>
            <details className="raw-details">
              <summary>Verification report</summary>
              <pre>{JSON.stringify(job?.result?.report || job, null, 2)}</pre>
            </details>
          </div>
        )}
      </div>
    </Dialog>
  );
}
export function Library({
  subjects,
  onChoose,
  onDesign,
  selected,
  onRefresh,
}: {
  subjects: Subject[];
  onChoose: (id: string) => void;
  onDesign: () => void;
  selected: Detail | null;
  onRefresh: () => Promise<unknown>;
}) {
  const [review, setReview] = useState("");
  const [query, setQuery] = useState("");
  const candidates = subjects.filter((s) => s.candidate);
  const activeSubjects = subjects.filter((s) => s.status === "compiled");
  const visibleSubjects = activeSubjects.filter(s => s.title.toLowerCase().includes(query.trim().toLowerCase()));
  const currentSubject = activeSubjects.find(s => s.subject === selected?.spec.subject);
  const [error, setError] = useState("");
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Your library</span>
          <h1>Room to explore.</h1>
          <p>
            Thoughtfully sourced paths into the things you want to understand.
          </p>
        </div>
        <button className="primary" onClick={onDesign}>
          <Plus size={16} /> New curriculum
        </button>
      </div>
      {error && <ErrorBox error={error} />}
      {currentSubject && <section className="resume-path" aria-label="Pick up where you left off">
        <div className="resume-copy">
          <span className="eyebrow"><span className="tiny-orb"/> Pick up where you left off</span>
          <h2>{currentSubject.title.includes("—") ? <>{currentSubject.title.slice(0, currentSubject.title.indexOf("—"))}<span className="resume-subtitle">{currentSubject.title.slice(currentSubject.title.indexOf("—"))}</span></> : currentSubject.title}</h2>
          <p>{currentSubject.node_count} concepts <span aria-hidden="true">·</span> {currentSubject.source_count} sources <span aria-hidden="true">·</span> At your own pace</p>
          <button className="primary" onClick={() => onChoose(currentSubject.subject)}>Continue learning <ArrowRight size={16}/></button>
        </div>
        <div className="resume-art" aria-hidden="true"><PathArtwork /></div>
      </section>}
      {activeSubjects.length > 0 && <div className="collection-heading">
        <div><h2>Your collection <span className="mono">{activeSubjects.length.toString().padStart(2, "0")}</span></h2><p>Follow a familiar path. Or find a new one.</p></div>
        <label className="collection-search"><Search size={16}/><input type="search" aria-label="Find a curriculum" placeholder="Find a curriculum…" value={query} onChange={e => setQuery(e.target.value)} /></label>
      </div>}
      {activeSubjects.length ? (
        <section className="curriculum-grid" aria-label="Your curricula">
          {visibleSubjects.map((s) => {
            const i = activeSubjects.indexOf(s);
            return (
            <article className={"curriculum-card " + (selected?.spec.subject === s.subject ? "current-path" : "")} key={s.subject}>
              <div className="curriculum-art" data-variant={i % 3}>
                <PathArtwork variant={i % 3} />
                <span className="mono art-label">
                  LEARNING PATH / {String(i + 1).padStart(2, "0")}
                </span>
              </div>
              <div className="curriculum-card-body">
                <div className="row between"><span className="eyebrow">{selected?.spec.subject === s.subject ? "Your current path" : "Ready to explore"}</span><Status status={s.status} /></div>
                <h2>{s.title}</h2>
                <div className="mono muted">
                  {s.node_count} concepts · {s.source_count} sources
                </div>
                <div className="card-bottom">
                  <button
                    className="text-button"
                    onClick={() => onChoose(s.subject)}
                  >
                    {selected?.spec.subject === s.subject ? "Open curriculum" : "Explore curriculum"} <ArrowRight size={14} />
                  </button>
                  <button
                    className="icon-button"
                    aria-label={"Review " + s.title}
                    onClick={() => setReview(s.subject)}
                  >
                    <FileText size={16} />
                  </button>
                </div>
              </div>
            </article>
          ); })}
          {visibleSubjects.length === 0 && <div className="empty-panel search-empty"><Search size={26}/><h2>No curricula match your search.</h2><button className="secondary" onClick={() => setQuery("")} aria-label="Clear curriculum search">Show all curricula</button></div>}
        </section>
      ) : (
        <div className="empty-panel">
          <Layers3 size={38} />
          <h2>A home for everything you're curious about.</h2>
          <p>Your verified learning paths will appear here.</p>
          <button className="secondary" onClick={onDesign}>
            Design a curriculum <ArrowRight size={15} />
          </button>
        </div>
      )}
      {candidates.length > 0 && (
        <section className="section-block">
          <div className="eyebrow">Curricula awaiting attention</div>
          {candidates.map((s) => (
            <button
              className="history-item"
              key={s.subject}
              onClick={() => setReview(s.subject)}
            >
              <FileText size={18} />
              <span>
                <strong>{s.title}</strong>
                <small className="muted">Candidate · not yet active</small>
              </span>
              <Status status={s.candidate?.status || s.status} />
              <ChevronRight size={16} />
            </button>
          ))}
        </section>
      )}
      {selected && (
        <div className="library-note">
          <ShieldCheck size={19} />
          <p>
            <strong>The evidence stays within reach.</strong>
            <br />
            Each path keeps its concept map, source report and verification
            trail. Review or edit them at any time.
          </p>
        </div>
      )}
      {review && (
        <CurriculumReview
          subject={review}
          onClose={() => setReview("")}
          onChanged={() => {
            void onRefresh().catch((e) => setError(message(e)));
            setReview("");
          }}
        />
      )}
    </div>
  );
}
function CurriculumReview({
  subject,
  onClose,
  onChanged,
}: {
  subject: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [d, setD] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("concepts");
  const [edit, setEdit] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [working, setWorking] = useState(false);
  async function load() {
    const data = await api<Detail>(
      "/subjects/" + enc(subject) + "?candidate=true",
    );
    const visible = data.candidate ? { ...data, ...data.candidate } : data;
    setD(visible);
    setEdit(JSON.stringify(visible.spec, null, 2));
  }
  useEffect(() => {
    void load().catch((e) => setError(message(e)));
  }, [subject]);
  async function decide(action: string) {
    setWorking(true);
    setError("");
    try {
      await api("/subjects/" + enc(subject) + "/review", {
        action,
        fingerprint: d?.candidate?.fingerprint,
      });
      onChanged();
    } catch (e) {
      setError(message(e));
    } finally {
      setWorking(false);
    }
  }
  async function save() {
    setError("");
    setWorking(true);
    try {
      const spec: Spec = JSON.parse(edit);
      if (spec.subject !== subject)
        throw new Error("The subject identifier cannot be changed.");
      const result = await api<{ job_id: string }>(
        "/subjects/" + enc(subject) + "/candidate",
        { spec },
        "PUT",
      );
      const done = await watchJob(result.job_id, setJob);
      if (done.error) throw new Error(done.error);
      await load();
      setTab("report");
    } catch (e) {
      setError(message(e));
    } finally {
      setWorking(false);
      setJob(null);
    }
  }
  return (
    <Dialog title="Curriculum & evidence" onClose={onClose} wide>
      <div className="dialog-body">
        {error && <ErrorBox error={error} />}{" "}
        {!d ? (
          <Working stage="Loading verification trail" />
        ) : (
          <>
            <div className="row between wrap">
              <h3>{d.spec.title}</h3>
              <Status status={d.decision?.status || "unverified"} />
            </div>
            <div className="tabs">
              {["concepts", "sources", "report", "edit"].map((t) => (
                <button
                  key={t}
                  className={tab === t ? "active" : ""}
                  onClick={() => setTab(t)}
                >
                  {t}
                </button>
              ))}
            </div>
            {tab === "concepts" &&
              d.spec.nodes.map((n) => (
                <div className="review-node" key={n.id}>
                  <div className="row between">
                    <strong>{n.title}</strong>
                    <Status status={n.status} />
                  </div>
                  <p>{n.defn}</p>
                  <span className="mono muted">
                    {n.prereqs.length
                      ? "After: " + n.prereqs.join(", ")
                      : "Foundation · no prerequisites"}{" "}
                    · {n.oracle || "No executable oracle"}
                  </span>
                </div>
              ))}
            {tab === "sources" && (
              <SourceTable
                sources={d.spec.corpus.map((s) => ({
                  ...s,
                  ...d.report.corpus?.find((c) => c.id === s.id),
                }))}
              />
            )}{" "}
            {tab === "report" && (
              <>
                <p className="muted">
                  Measured evidence, compilation decision and change details.
                  Review cannot override a failed check.
                </p>
                <pre className="report-json">
                  {JSON.stringify(
                    { decision: d.decision, summary: d.report.summary },
                    null,
                    2,
                  )}
                </pre>
              </>
            )}
            {tab === "edit" && (
              <>
                <p className="muted">
                  Edit the curriculum spec. Saving re-runs verification and
                  creates a candidate; your active curriculum stays intact.
                </p>
                <label className="field">
                  Curriculum JSON
                  <textarea
                    className="json-editor"
                    rows={16}
                    value={edit}
                    onChange={(e) => setEdit(e.target.value)}
                  />
                </label>
                <button
                  className="primary"
                  disabled={working}
                  onClick={() => void save()}
                >
                  Save & reverify <ShieldCheck size={16} />
                </button>
              </>
            )}
            {job && <Working stage={job.stage} />}{" "}
            {d.candidate && ["paused", "compiled"].includes(d.decision?.status || "") && (
              <div className="review-actions">
                <button
                  className="secondary"
                  disabled={working}
                  onClick={() => void decide("reject")}
                >
                  Discard candidate
                </button>
                <button
                  className="primary"
                  disabled={working}
                  onClick={() => void decide("approve")}
                >
                  Add verified curriculum <Check size={16} />
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </Dialog>
  );
}
function SourceTable({ sources }: { sources: Source[] }) {
  return (
    <div className="source-list">
      {sources.map((s) => (
        <article className="source-row" key={s.id}>
          <span className="source-symbol">
            <FileText size={18} />
          </span>
          <div className="source-copy">
            <strong>{s.name}</strong>
            {safeURL(s.url) && (
              <a
                href={safeURL(s.url)!}
                target="_blank"
                rel="noopener noreferrer"
              >
                {new URL(s.url).hostname} <ExternalLink size={11} />
              </a>
            )}
            <div className="mono muted">
              {s.adapter || s.method || "source"}
              {s.http_status ? " · HTTP " + s.http_status : ""}
              {s.text_len !== undefined
                ? " · " + s.text_len.toLocaleString() + " chars"
                : ""}
            </div>
            {s.error && <p className="warn">{s.error}</p>}
          </div>
          <Status status={s.status || "discovered"} />
        </article>
      ))}
    </div>
  );
}
export function Research({
  detail,
  onDesign,
}: {
  detail: Detail | null;
  onDesign: (topic: string) => void;
}) {
  const [q, setQ] = useState("");
  const [result, setResult] = useState<{
    sources: Source[];
    errors: string[];
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function search() {
    setBusy(true);
    setError("");
    try {
      setResult(await api("/research?query=" + enc(q.trim())));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">The research desk</span>
          <h1>Go to the source.</h1>
          <p>Follow the evidence behind an idea. See what holds up.</p>
        </div>
      </div>
      <form
        className="research-search"
        onSubmit={(e) => {
          e.preventDefault();
          void search();
        }}
      >
        <Search size={19} />
        <input
          aria-label="Research a topic"
          placeholder="A subject, a concept, a question…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          maxLength={300}
          required
        />
        <button className="primary" disabled={busy || !q.trim()}>
          Find sources <ArrowRight size={15} />
        </button>
      </form>
      <div className="source-adapters">
        <span>Wikidata</span>
        <span>MIT OpenCourseWare</span>
        <span>arXiv</span>
        <span>Project Gutenberg</span>
      </div>
      {error && <ErrorBox error={error} />}{" "}
      {busy && <Working stage="Looking for authoritative sources" />}
      {result && (
        <section className="section-block">
          <div className="row between wrap">
            <div>
              <h2>Research findings</h2>
              <p className="muted">
                Discovery is a starting point, not a verification badge.
              </p>
            </div>
            <button className="secondary" onClick={() => onDesign(q)}>
              Build a path from here <ArrowRight size={15} />
            </button>
          </div>
          {result.errors.map((s, i) => (
            <div className="callout warn" key={i}>
              <TriangleAlert size={15} />
              <p>{s}</p>
            </div>
          ))}
          {result.sources.length ? (
            <SourceTable sources={result.sources} />
          ) : (
            <p className="empty-message">
              No matching sources. Try a broader subject, or bring a source URL
              into the curriculum designer.
            </p>
          )}
        </section>
      )}
      {detail && (
        <section className="section-block">
          <div className="row between wrap">
            <div>
              <span className="eyebrow">In your current curriculum</span>
              <h2>{detail.spec.title}</h2>
            </div>
            <span className="mono muted">
              {detail.spec.corpus.length} sources
            </span>
          </div>
          <SourceTable
            sources={detail.spec.corpus.map((s) => ({
              ...s,
              ...detail.report.corpus?.find((c) => c.id === s.id),
            }))}
          />
        </section>
      )}
      {!detail && !result && (
        <div className="research-principles">
          <div>
            <span className="mono">01</span>
            <h3>Find the original</h3>
            <p>
              Textbooks, open courses and primary references. Not another
              model's guess.
            </p>
          </div>
          <div>
            <span className="mono">02</span>
            <h3>Read beyond the title</h3>
            <p>
              A working URL isn't evidence. Extracted text must clear the
              substance threshold.
            </p>
          </div>
          <div>
            <span className="mono">03</span>
            <h3>Keep the trail</h3>
            <p>
              Every claim links back to its source. Limitations stay visible.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
export function Progress({
  detail,
  onPractice,
  onExplore,
}: {
  detail: Detail | null;
  onPractice: (id: string) => void;
  onExplore: () => void;
}) {
  if (!detail)
    return (
      <div className="page">
        <span className="eyebrow">Your progress</span>
        <h1>Understanding starts with a question.</h1>
        <p className="muted">
          Choose a curriculum and take your first small step. No streaks to
          protect. No catching up.
        </p>
        <button className="primary" onClick={onExplore}>
          Explore your library <ArrowRight size={15} />
        </button>
      </div>
    );
  const entries = detail.spec.nodes;
  const states = detail.state.nodes;
  const pct = Math.round(
    (entries.reduce((s, n) => s + (states[n.id]?.mastery || 0), 0) /
      Math.max(entries.length, 1)) *
      100,
  );
  const attempts = Object.values(states).reduce(
    (s, n) => s + (n.practice_attempts || 0),
    0,
  );
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Your progress</span>
          <h1>Understanding, taking root.</h1>
          <p>Not how much you read. What you can bring back to mind.</p>
        </div>
      </div>
      <div className="stat-grid">
        <div className="stat-card">
          <span>Traced understanding</span>
          <strong className="mono">
            {pct}
            <small>%</small>
          </strong>
          <div className="track">
            <div style={{ width: pct + "%" }} />
          </div>
          <p>Estimated from verified practice</p>
        </div>
        <div className="stat-card">
          <span>Ready to revisit</span>
          <strong className="mono">
            {detail.due.length.toString().padStart(2, "0")}
          </strong>
          <p>Spaced reviews, at your pace</p>
        </div>
        <div className="stat-card">
          <span>Practice attempts</span>
          <strong className="mono">
            {attempts.toString().padStart(2, "0")}
          </strong>
          <p>Asking questions never earns mastery</p>
        </div>
      </div>
      <section className="section-block">
        <div className="row between">
          <h2>Your concept map</h2>
          <span className="mono muted">From verified practice</span>
        </div>
        {entries.map((n) => {
          const s = states[n.id];
          const gated = detail.gates[n.id]?.unlocked === false;
          return (
            <div className="progress-node" key={n.id}>
              <span className="source-symbol">
                {gated ? (
                  <LockKeyhole size={18} />
                ) : detail.due.includes(n.id) ? (
                  <Clock3 size={18} />
                ) : (
                  <BookOpen size={18} />
                )}
              </span>
              <div>
                <strong>{n.title}</strong>
                <p className="muted">
                  {gated
                    ? "Build the prerequisites first"
                    : s?.next_review
                      ? "Next review " +
                        new Date(s.next_review).toLocaleDateString()
                      : "A fresh place to begin"}
                </p>
                {(s?.misconceptions_triggered?.length || 0) > 0 && (
                  <p className="warn">An idea worth revisiting</p>
                )}
              </div>
              <div className="node-mastery">
                <span className="mono">
                  {Math.round((s?.mastery || 0) * 100)}%
                </span>
                <div className="track">
                  <div style={{ width: (s?.mastery || 0) * 100 + "%" }} />
                </div>
              </div>
              <button
                className="secondary small"
                disabled={gated}
                onClick={() => onPractice(n.id)}
              >
                Practice <ArrowRight size={13} />
              </button>
            </div>
          );
        })}
      </section>
      <div className="library-note">
        <ShieldCheck size={19} />
        <p>
          These are estimates, not labels. Only scored, verified practice
          updates your learning state. An unclear answer is left unscored, not
          treated as a failure.
        </p>
      </div>
    </div>
  );
}
export function SettingsPanel({ onClose }: { onClose: () => void }) {
  const [s, setS] = useState<Settings | null>(null);
  const [token, setToken] = useState(
    sessionStorage.getItem("open-tutor-token") || "",
  );
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<Settings>("/settings")
      .then((d) =>
        setS({
          ...d,
          base_url: d.base_url || "",
          model: d.model || "",
          mode: d.mode || "extractive",
        }),
      )
      .catch((e) => setError(message(e)));
  }, []);
  async function save() {
    if (!s) return;
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      if (token) sessionStorage.setItem("open-tutor-token", token);
      else sessionStorage.removeItem("open-tutor-token");
      // PUT only accepts writable fields; `configured` is a derived GET flag.
      setS(await api<Settings>("/settings", {
        base_url: s.base_url,
        model: s.model,
        mode: s.mode,
      }, "PUT"));
      setSaved(true);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog title="Your private workspace" onClose={onClose}>
      <div className="dialog-body">
        {error && <ErrorBox error={error} />}{" "}
        {s ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <div className="callout">
              <LockKeyhole size={18} />
              <p>
                Single-user. Self-hosted. No accounts. Learner data and model
                inference stay on your own machines.
              </p>
            </div>
            <label className="field">
              Legacy evidence-answer mode
              <select
                value={s.mode}
                onChange={(e) =>
                  setS({ ...s, mode: e.target.value as Settings["mode"] })
                }
              >
                <option value="extractive">
                  Evidence excerpts · no model required
                </option>
                <option value="local-model">
                  Local model · exact-source verification
                </option>
              </select>
            </label>
            <label className="field">
              Local provider URL
              <input
                type="url"
                placeholder="http://127.0.0.1:9120"
                value={s.base_url}
                onChange={(e) => setS({ ...s, base_url: e.target.value })}
              />
            </label>
            <label className="field">
              Model name
              <input
                placeholder="Exact model ID from your local provider"
                value={s.model}
                onChange={(e) => setS({ ...s, model: e.target.value })}
              />
            </label>
            <p className="muted">
              Adaptive teaching always uses your local model with separately checked evidence.
              The mode above applies only to legacy evidence-answer clients. Curriculum
              generation also uses this model; candidate curricula must pass the independent verifier.
            </p>
            <details className="source-options">
              <summary>Optional write protection</summary>
              <label className="field">
                Workspace write token
                <input
                  type="password"
                  autoComplete="off"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                />
              </label>
              <p className="muted">
                Only needed if the server has a static write token configured.
                Kept in this browser tab's session storage.
              </p>
            </details>
            <button className="primary full" disabled={busy}>
              {saved ? (
                <>
                  <Check size={16} /> Settings saved
                </>
              ) : (
                <>
                  Save settings <ArrowRight size={16} />
                </>
              )}
            </button>
          </form>
        ) : (
          !error && <Working stage="Reading local settings" />
        )}
      </div>
    </Dialog>
  );
}
interface Item {
  id: string;
  prompt: string;
  rubric_points?: string[];
  kind: string;
  quantitative?: boolean;
  answer_options?: string[];
}
interface PracticeEvidence {
  source_id: string;
  text: string;
  char_start: number;
  char_end: number;
}
interface Grade {
  verdict: string;
  score: number | null;
  flags?: string[];
  issues?: string[];
  feedback?: string;
  rubric?: unknown;
}
export function Assessment({
  kind,
  subject,
  node,
  onClose,
  onGraded,
}: {
  kind: "quiz" | "teach-back";
  subject: string;
  node: Node;
  onClose: () => void;
  onGraded: () => void;
}) {
  const [item, setItem] = useState<Item | null>(null);
  const [evidence, setEvidence] = useState<PracticeEvidence[]>([]);
  const [text, setText] = useState("");
  const [grade, setGrade] = useState<Grade | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<{ item: Item; evidence?: PracticeEvidence[] }>(
      "/subjects/" +
        enc(subject) +
        "/assessment?node_id=" +
        enc(node.id) +
        "&kind=" +
        kind,
    )
      .then((d) => {
        setItem(d.item);
        setEvidence(d.evidence || []);
      })
      .catch((e) => setError(message(e)));
  }, [kind, subject, node.id]);
  async function submit() {
    if (!item) return;
    setBusy(true);
    setError("");
    try {
      const d = await api<{ grade: Grade }>(
        "/subjects/" + enc(subject) + "/assessment",
        { item_id: item.id, response: { text, citations: evidence } },
      );
      setGrade(d.grade);
      onGraded();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      title={
        kind === "quiz" ? "A small understanding check" : "Make it your own"
      }
      onClose={onClose}
    >
      <div className="dialog-body">
        {error && <ErrorBox error={error} />}{" "}
        {!item && !error ? (
          <Working stage="Preparing something for you to try" />
        ) : (
          item && (
            <>
              <div className="eyebrow">{node.title}</div>
              <h3>{item.prompt}</h3>
              <p className="muted">
                {kind === "teach-back"
                  ? "Explain the idea as you would to a friend. An ambiguous explanation stays unscored."
                  : "A quick look at what's sticking — not a test, nothing to pass."}
              </p>
              {item.rubric_points && (
                <div className="rubric-hints">
                  {item.rubric_points.map((p) => (
                    <span key={p}>{p}</span>
                  ))}
                </div>
              )}
              {!grade ? (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void submit();
                  }}
                >
                  {item.answer_options?.length ? (
                    <fieldset className="answer-options">
                      <legend>Choose your response</legend>
                      {item.answer_options.map((option) => {
                        const value = option.match(/^([A-Z])\./)?.[1] || option;
                        return (
                          <label
                            key={option}
                            className={text === value ? "chosen" : ""}
                          >
                            <input
                              type="radio"
                              name="practice-choice"
                              value={value}
                              checked={text === value}
                              onChange={() => setText(value)}
                            />
                            <span>{option}</span>
                          </label>
                        );
                      })}
                    </fieldset>
                  ) : (
                    <label className="field">
                      Your response
                      <textarea
                        rows={item.quantitative ? 2 : 6}
                        required
                        maxLength={20000}
                        value={text}
                        onChange={(e) => setText(e.target.value)}
                        placeholder={
                          item.quantitative
                            ? "Enter your numerical result…"
                            : "Here's how I understand it…"
                        }
                      />
                    </label>
                  )}
                  {evidence.length > 0 && (
                    <details className="source-options">
                      <summary>
                        Reference passage{" "}
                        <span className="muted">a little help</span>
                      </summary>
                      {evidence.map((e, i) => (
                        <div key={i}>
                          <p>{e.text}</p>
                          <span className="mono muted">
                            {e.source_id} · chars {e.char_start}–{e.char_end}
                          </span>
                        </div>
                      ))}
                    </details>
                  )}
                  <button
                    className="primary full"
                    disabled={busy || !text.trim()}
                  >
                    {busy ? "Checking the evidence…" : "Check my understanding"}{" "}
                    <ArrowRight size={15} />
                  </button>
                </form>
              ) : (
                <div
                  className={
                    "assessment-result " +
                    (grade.verdict === "correct" ? "ok" : "warn")
                  }
                >
                  <h3>
                    {grade.verdict === "correct"
                      ? "That idea is taking root."
                      : grade.verdict === "incorrect"
                        ? "A useful place to return to."
                        : grade.verdict === "partial"
                          ? "You have part of the picture."
                          : "Not enough evidence to score this."}
                  </h3>
                  <p>
                    {grade.feedback ||
                      (["flagged", "degraded", "not-scored"].includes(
                        grade.verdict,
                      )
                        ? "Your progress has not been penalized. Try a more concrete response or revisit the source."
                        : "Your practice is saved. You can revisit the explanation whenever you like.")}
                  </p>
                  {grade.score != null && (
                    <span className="mono">Score: {grade.score}</span>
                  )}
                  {[...(grade.flags || []), ...(grade.issues || [])].map(
                    (f, i) => (
                      <p className="mono" key={i}>
                        {f}
                      </p>
                    ),
                  )}
                  <button className="secondary full" onClick={onClose}>
                    Back to learning <ArrowRight size={15} />
                  </button>
                </div>
              )}
            </>
          )
        )}
      </div>
    </Dialog>
  );
}
