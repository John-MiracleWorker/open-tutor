export interface Citation {
  source_id: string;
  source_name: string;
  url: string;
  text: string;
  char_start: number;
  char_end: number;
  tier?: number;
  score?: number;
}
export interface Answer {
  draft: string;
  grounded: boolean;
  status: string;
  citations: Citation[];
  failures?: string[];
  verification?: { issues?: string[]; attempts?: number; grounded?: boolean };
  oracle?: {
    status: string;
    oracle_name?: string;
    result?: unknown;
    error?: string;
  };
  resolution?: { node_id?: string };
  mode?: string;
  teaching?: TeachingTurn;
  verification_scope?: "evidence-only";
  evidence_grounded?: boolean;
  evidence?: Answer;
}
export type TeachingApproach = "auto" | "plain" | "socratic" | "worked-example" | "analogy" | "visual" | "challenge";
export type TeachingAction = "respond" | "simpler" | "another-way" | "hint" | "example" | "visual" | "challenge" | "got-it" | "confused";
export interface LearnerPreferences {
  schema_version: "1";
  approach: TeachingApproach;
  pace: "gentle" | "balanced" | "brisk";
  goal: string;
  experience: string;
  interests: string;
}
export interface TeachingDiagram {
  title: string;
  nodes: { id: string; label: string; detail: string }[];
  edges: { from: string; to: string; label: string }[];
}
export interface TeachingTurn {
  schema_version: "1";
  status: "ready" | "fallback" | "blocked";
  verified: false;
  approach: Exclude<TeachingApproach, "auto">;
  pace: LearnerPreferences["pace"];
  intent: "explain" | "probe" | "scaffold" | "transfer" | "respond";
  reason: string;
  title: string;
  explanation: string;
  steps: { title: string; body: string }[];
  diagram: TeachingDiagram | null;
  activity: { kind: "predict" | "explain" | "apply" | "reflect"; prompt: string } | null;
  evidence_refs: number[];
  warnings: string[];
  model: string | null;
  latency_ms: number;
}
export interface TeachingState {
  node_id?: string | null;
  turn_count?: number;
  approach?: TeachingTurn["approach"];
  pace?: LearnerPreferences["pace"];
  hint_level?: number;
  confusion_count?: number;
  pending_question?: string | null;
  last_action?: TeachingAction;
}
export interface Node {
  id: string;
  title: string;
  defn: string;
  prereqs: string[];
  status: string;
  grounding_corpus: string[];
  oracle?: string;
  misconceptions: { id: string; text: string }[];
}
export interface Source {
  id: string;
  name: string;
  url: string;
  adapter?: string;
  status?: string;
  http_status?: number;
  text_len?: number;
  method?: string;
  error?: string;
}
export interface Spec {
  subject: string;
  title: string;
  nodes: Node[];
  corpus: Source[];
  scope: Record<string, unknown>;
  tiers: Record<string, unknown>;
}
export interface NodeState {
  mastery: number;
  attempts: number;
  practice_attempts?: number;
  next_review?: string;
  misconceptions_triggered?: string[];
}
export interface Subject {
  subject: string;
  title: string;
  node_count: number;
  grounded_count: number;
  source_count: number;
  status: string;
  candidate?: {
    status: string;
    fingerprint: string;
    reasons?: string[];
  } | null;
}
export interface Detail {
  spec: Spec;
  report: { corpus?: Source[]; summary?: Record<string, unknown> };
  decision?: { status: string; reasons?: string[]; diff?: unknown };
  state: { nodes: Record<string, NodeState> };
  gates: Record<string, { unlocked: boolean; blocking_prereqs: string[] }>;
  due: string[];
  candidate?: {
    spec: Spec;
    report: Detail["report"];
    decision: Detail["decision"];
    fingerprint: string;
  } | null;
}
export interface Thread {
  id: string;
  subject: string;
  title: string;
  created_at: string;
  updated_at: string;
}
export interface Message {
  id: string;
  role: string;
  content: string;
  answer?: Answer;
  created_at: string;
}
export interface Job {
  id: string;
  status: string;
  stage: string;
  error?: string;
  result?: {
    subject?: string;
    answer?: Answer;
    decision?: { status: string; reasons?: string[] };
    report?: unknown;
  };
}
export interface Settings {
  base_url: string;
  model: string;
  mode: "extractive" | "local-model";
}
