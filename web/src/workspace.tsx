import { useId, useState } from "react";
import { ArrowRight, Check, LockKeyhole, Search, X } from "lucide-react";
import type { Detail } from "./types";
import { ACTIONS } from "./teaching";

/** Decorative identity, not an illustration of educational facts. */
export function AuroraMark({ large = false }: { large?: boolean }) {
  return <svg className={large ? "aurora-mark large" : "aurora-mark"} viewBox="0 0 40 40" fill="none" aria-hidden="true">
    <path d="M7 29 16 10h7l10 19M12 25h16M16 10l4 10 3-10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    <circle cx="30" cy="10" r="2" fill="currentColor" />
  </svg>;
}

export function PathArtwork({ variant = 0 }: { variant?: number }) {
  const id = useId();
  return <svg className="path-artwork" viewBox="0 0 400 190" fill="none" aria-hidden="true">
    <defs><linearGradient id={id} x1="80" y1="20" x2="320" y2="170" gradientUnits="userSpaceOnUse"><stop stopColor="currentColor" stopOpacity=".85"/><stop offset="1" stopColor="currentColor" stopOpacity=".1"/></linearGradient></defs>
    <g stroke={`url(#${id})`} strokeWidth=".8">
      {variant % 3 === 0 ? Array.from({length: 7}, (_, i) => <ellipse key={i} cx="215" cy="100" rx={40 + i * 13} ry="62" transform={`rotate(${i * 16 - 48} 215 100)`}/>) : variant % 3 === 1 ? Array.from({length: 9}, (_, i) => <path key={i} d={`M40 ${125+i*4} Q130 ${-60+i*14} 210 ${90+i*3} T390 ${40+i*8}`}/>) : Array.from({length: 8}, (_, i) => <rect key={i} x={150-i*6} y={45-i*3} width={95+i*12} height={95+i*6} rx="4" transform={`rotate(${i*9} 200 95)`}/>)}
    </g><circle cx="292" cy="62" r="3" fill="currentColor"/><path d="M45 33h20M55 23v20" stroke="currentColor" opacity=".3"/>
  </svg>;
}

/** Filtering is presentation-only; original course order and gate semantics stay intact. */
export function CourseOutline({ detail, selected, onSelect }: { detail: Detail; selected: string; onSelect: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const nodes = detail.spec.nodes.map((node, index) => ({ node, index })).filter(({ node }) => node.title.toLowerCase().includes(query.trim().toLowerCase()));
  return <>
    <div className="outline-search">
      <Search size={14} aria-hidden="true" />
      <input type="search" aria-label="Find a concept" placeholder="Find a concept…" value={query} onChange={e => setQuery(e.target.value)} />
      {query && <button className="icon-button" aria-label="Clear concept search" onClick={() => setQuery("")}><X size={14}/></button>}
    </div>
    <div className="concepts">
      {nodes.map(({ node: n, index: i }) => {
        const m = detail.state.nodes[n.id]?.mastery || 0;
        const unlocked = detail.gates[n.id]?.unlocked ?? n.prereqs.length === 0;
        return <button key={n.id} className={"concept " + (n.id === selected ? "selected" : "")} onClick={() => onSelect(n.id)} aria-pressed={n.id === selected}>
          <span className={"concept-dot " + (m >= 0.8 ? "mastered" : detail.due.includes(n.id) ? "review" : unlocked ? "learning" : "locked")}>
            {m >= 0.8 ? <Check size={10}/> : !unlocked ? <LockKeyhole size={9}/> : null}
          </span>
          <span className="concept-label"><strong>{n.title}</strong><small>{!unlocked ? "Prerequisite practice needed" : detail.due.includes(n.id) ? "Ready for a revisit" : m >= 0.8 ? "Well understood" : "Explore & practice"}</small></span>
          <span className="mono concept-index">{m ? Math.round(m * 100) + "%" : String(i + 1).padStart(2, "0")}</span>
        </button>;
      })}
      {nodes.length === 0 && <p className="outline-empty" role="status">No matching concepts.</p>}
    </div>
  </>;
}

/** Presentation only: original messages and model context are never changed. */
export function lessonEvent(content: string): string | null {
  if (/^Start a guided lesson on .+\. Teach one clear idea, then ask me one question to find my starting point\.$/.test(content)) return "A guided lesson, one idea at a time";
  if (content === "Keep teaching me: take the next small step from my last answer.") return "Continuing to the next small step";
  if (content === "I'm back after a break. Give me a short recap of where we left off, then ask me one easy question from it.") return "Picking up where we left off";
  return ACTIONS.find(action => action.question === content)?.label ?? null;
}

export function LessonEvent({ content }: { content: string }) {
  const label = lessonEvent(content);
  if (!label) return <><div className="message-label">YOU</div><div className="user-bubble">{content}</div></>;
  return <details className="lesson-event"><summary><ArrowRight size={12}/><span>{label}</span><small>Lesson request</small></summary><p>{content}</p></details>;
}
