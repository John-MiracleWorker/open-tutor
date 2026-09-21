import "./teaching.css";
import { useEffect, useId, useState } from "react";
import { ArrowDown, ArrowRight, BookOpen, ChevronDown, CircleHelp, GitBranch, Lightbulb, MessageCircle, RefreshCw, Settings2, ShieldCheck, Sparkles, WandSparkles, Zap } from "lucide-react";
import { api } from "./api";
import { AnswerCard, Dialog, ErrorBox, Prose, Working } from "./components";
import type { Answer, LearnerPreferences, TeachingAction, TeachingApproach, TeachingDiagram } from "./types";

export const DEFAULT_PREFERENCES: LearnerPreferences = {
  schema_version: "1", approach: "auto", pace: "balanced", goal: "", experience: "", interests: "",
};
export const APPROACHES = [
  { id: "auto", label: "Adapt with me", description: "Change tactics as we go", Icon: Sparkles },
  { id: "plain", label: "Plain language", description: "One clear idea at a time", Icon: BookOpen },
  { id: "socratic", label: "Questions first", description: "Find it through conversation", Icon: MessageCircle },
  { id: "worked-example", label: "Worked example", description: "See the steps, then try", Icon: GitBranch },
  { id: "analogy", label: "Make a connection", description: "Start with something familiar", Icon: Lightbulb },
  { id: "visual", label: "Visual map", description: "Explore how ideas connect", Icon: WandSparkles },
  { id: "challenge", label: "Challenge me", description: "Apply it to something new", Icon: Zap },
] as const;
export const approachLabel = (id: TeachingApproach) => APPROACHES.find(a => a.id === id)?.label || "Adapt with me";
export const ACTIONS: { id: TeachingAction; label: string; question: string; Icon: typeof Sparkles }[] = [
  { id: "simpler", label: "Make it simpler", question: "Explain that more simply, one small idea at a time.", Icon: ArrowDown },
  { id: "another-way", label: "Another way", question: "That approach didn't click. Teach the same idea a different way.", Icon: RefreshCw },
  { id: "hint", label: "Just a hint", question: "Give me a small hint for your last question, not the answer.", Icon: Lightbulb },
  { id: "example", label: "Show an example", question: "Walk me through a concrete example of this idea.", Icon: GitBranch },
  { id: "visual", label: "Map it out", question: "Show me a visual map of this concept and explain the connections.", Icon: WandSparkles },
  { id: "got-it", label: "That clicked", question: "That clicked. Help me check if I can apply it in a new situation.", Icon: Sparkles },
  { id: "confused", label: "Still lost", question: "I'm still lost. Find the missing foundation and give me one small step.", Icon: CircleHelp },
];

export function TeachingBar({ preferences, onOpen }: { preferences: LearnerPreferences | null; onOpen: () => void }) {
  return <div className="teaching-bar">
    <span className="teacher-presence"><span className="tiny-orb" /> Room to think</span>
    <button aria-label="Personalize your tutor" onClick={onOpen}>
      <Settings2 size={13} /><span>{preferences ? approachLabel(preferences.approach) : "Choose your approach"}</span>
      {preferences && <span className="pace-label">{preferences.pace} pace</span>}<ChevronDown size={12} />
    </button>
  </div>;
}

export function TeachingPreferences({ onClose, onSaved, onFlowChange }: { onClose: () => void; onSaved: (p: LearnerPreferences) => void; onFlowChange?: (flow: boolean) => void }) {
  const [value, setValue] = useState<LearnerPreferences | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [flow, setFlow] = useState(() => localStorage.getItem("open-tutor-auto-advance") !== "off");
  useEffect(() => {
    let live = true;
    api<LearnerPreferences>("/learner/preferences").then(p => { if (live) setValue(p); }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, []);
  async function save() {
    if (!value || saving) return;
    setSaving(true); setError("");
    try { const saved = await api<LearnerPreferences>("/learner/preferences", value, "PUT"); onSaved(saved); }
    catch (e) { setError((e as Error).message); }
    finally { setSaving(false); }
  }
  return <Dialog title="A tutor that meets you here" onClose={onClose} wide>
    <div className="dialog-body preferences-body">
      <span className="eyebrow">Make room for your way of learning</span>
      <p className="preference-intro">Tell me what helps. We can change it whenever you like.</p>
      {error && <ErrorBox error={error} />}
      {!value && !error && <Working stage="Loading your preferences" />}
      {value && <form onSubmit={e => { e.preventDefault(); void save(); }}>
        <fieldset disabled={saving}>
          <legend>Where should we start?</legend>
          <div className="approach-grid">
            {APPROACHES.map(({ id, label, description, Icon }) => <button type="button" key={id} className={"approach-option " + (value.approach === id ? "selected" : "")} aria-pressed={value.approach === id} onClick={() => setValue({ ...value, approach: id })}>
              <Icon size={19} /><strong>{label}</strong><span>{description}</span>
            </button>)}
          </div>
          <p className="preference-note">These are preferences, not fixed learning styles. I’ll use your feedback to vary the explanation, not put you in a box.</p>
          <div className="preference-fields">
            <label>Pace<select value={value.pace} onChange={e => setValue({ ...value, pace: e.target.value as LearnerPreferences["pace"] })}>
              <option value="gentle">Gentle · small steps, more support</option><option value="balanced">Balanced · explain, then explore</option><option value="brisk">Brisk · concise, more challenge</option>
            </select></label>
            <label>What are you working toward?<textarea rows={2} maxLength={500} value={value.goal} placeholder="Understand the ideas, prepare for a course, build something…" onChange={e => setValue({ ...value, goal: e.target.value })} /></label>
            <label>What do you already know?<textarea rows={2} maxLength={500} value={value.experience} placeholder="Starting fresh is a perfectly good answer. Optional." onChange={e => setValue({ ...value, experience: e.target.value })} /></label>
            <label>Things you enjoy or connect with<input maxLength={500} value={value.interests} placeholder="Music, games, everyday examples… Optional." onChange={e => setValue({ ...value, interests: e.target.value })} /></label>
          </div>
          <label className="flow-toggle">
            <input
              type="checkbox"
              checked={flow}
              onChange={(e) => {
                const next = e.target.checked;
                setFlow(next);
                localStorage.setItem(
                  "open-tutor-auto-advance",
                  next ? "on" : "off",
                );
                if (onFlowChange) onFlowChange(next);
              }}
            />
            <span>
              <strong>Keep the lesson moving</strong>
              <small>
                After you answer, I'll pick the thread back up on my own. Turn
                off to always drive.
              </small>
            </span>
          </label>
        </fieldset>
        <div className="preference-footer"><span><ShieldCheck size={14} /> Saved only on this tutor’s local server.</span><button type="button" className="text-button" disabled={saving} onClick={() => setValue({ ...DEFAULT_PREFERENCES })}>Reset preferences</button></div>
        <button type="submit" className="primary full" disabled={saving}>{saving ? "Saving…" : "Save my preferences"}<ArrowRight size={16} /></button>
      </form>}
    </div>
  </Dialog>;
}

function ConceptDiagram({ diagram }: { diagram: TeachingDiagram }) {
  const [selected, setSelected] = useState<string | null>(null);
  const nodes = diagram.nodes.slice(0, 6);
  const detail = nodes.find(n => n.id === selected);
  return <figure className="teaching-diagram">
    <figcaption><GitBranch size={15} /><span>{diagram.title}</span><small>AI concept map · tap to explore</small></figcaption>
    <div className="diagram-nodes">{nodes.map((n, i) => <button key={n.id} aria-label={`${n.label}: show detail`} aria-expanded={selected === n.id} className={selected === n.id ? "selected" : ""} onClick={() => setSelected(selected === n.id ? null : n.id)}><span className="mono">{String(i + 1).padStart(2, "0")}</span><strong>{n.label}</strong><ChevronDown size={13} /></button>)}</div>
    {detail && <div className="diagram-detail"><strong>{detail.label}</strong><p>{detail.detail}</p></div>}
    <ul className="diagram-connections" aria-label="Concept connections">{diagram.edges.slice(0, 10).map((edge, i) => {
      const from = nodes.find(n => n.id === edge.from), to = nodes.find(n => n.id === edge.to);
      return from && to ? <li key={i}><span>{from.label}</span><span className="connection-label">{edge.label}<ArrowRight size={14} /></span><span>{to.label}</span></li> : null;
    })}</ul>
  </figure>;
}

export function TeachingCard({ answer, onAction, onReply, busy = false }: { answer: Answer; onAction?: (action: TeachingAction) => void; onReply?: () => void; busy?: boolean }) {
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const moreId = useId();
  const t = answer.teaching;
  if (!t) return <AnswerCard answer={answer} />;
  const ready = t.status === "ready";
  const evidenceChecked = answer.evidence_grounded === true && answer.evidence?.grounded === true && answer.evidence.status === "grounded" && t.status !== "blocked";
  return <>
    <article className={"teaching-card " + (!ready ? "teaching-degraded" : "")} aria-label="AI teaching response">
      <div className="teaching-heading"><span className="teaching-method"><Sparkles size={13} />{ready ? approachLabel(t.approach) : t.status === "blocked" ? "Evidence needs attention" : "Tutor unavailable"}</span><span className="mono muted">{t.pace} pace</span></div>
      <span className="coaching-disclosure">AI explanation · not independently verified</span>
      <h2>{t.title}</h2>
      <Prose text={t.explanation} />
      {t.steps.length > 0 && <ol className="teaching-steps">{t.steps.map((step, i) => <li key={i}><span className="step-number">{String(i + 1).padStart(2, "0")}</span><div><h3>{step.title}</h3><Prose text={step.body} /></div></li>)}</ol>}
      {t.diagram && <ConceptDiagram diagram={t.diagram} />}
      {ready && t.activity && <section className="teaching-activity" aria-label="Try it yourself">
        <div className="eyebrow"><MessageCircle size={14} /> Your turn · {t.activity.kind}</div>
        <p>{t.activity.prompt}</p>
        <div className="activity-footer"><span>No pressure. A first thought is enough.</span>{onReply && <button className="secondary small" disabled={busy} onClick={onReply}>Try an answer <ArrowRight size={14} /></button>}</div>
        <small>Conversation feedback doesn’t change your mastery score.</small>
      </section>}
      {t.warnings.length > 0 && <div className="callout warn"><CircleHelp size={16} /><div>{t.warnings.map((w, i) => <p key={i}>{w}</p>)}</div></div>}
      <div className="teaching-trail"><details className="teaching-reason"><summary>Why this approach<ChevronDown size={12}/></summary><p>{t.reason}</p></details>{answer.evidence && <button onClick={() => setEvidenceOpen(true)} aria-label={evidenceChecked ? "Inspect checked evidence" : "Inspect evidence limitations"}><ShieldCheck size={13} />{evidenceChecked ? "Evidence checked" : "Evidence limits"}<ArrowRight size={12} /></button>}</div>
      {onAction && <div className="adaptation-actions" aria-label="Change teaching approach">
        {ACTIONS.slice(0, 3).map(({ id, label, Icon }) => <button key={id} disabled={busy} onClick={() => onAction(id)}><Icon size={14} />{label}</button>)}
        <button aria-label="More ways to learn" aria-expanded={moreOpen} aria-controls={moreId} disabled={busy} onClick={() => setMoreOpen(!moreOpen)}>More<ChevronDown size={13}/></button>
        {moreOpen && <div id={moreId} className="more-actions">{ACTIONS.slice(3).map(({ id, label, Icon }) => <button key={id} disabled={busy} onClick={() => { setMoreOpen(false); onAction(id); }}><Icon size={14}/>{label}</button>)}</div>}
      </div>}
    </article>
    {evidenceOpen && answer.evidence && <Dialog title="Evidence, separately checked" onClose={() => setEvidenceOpen(false)} wide><div className="dialog-body"><p className="muted">This check covers the source excerpts and executed reference examples below—not the AI explanation, analogy, map or conversational feedback.</p><AnswerCard answer={answer.evidence} /></div></Dialog>}
  </>;
}
