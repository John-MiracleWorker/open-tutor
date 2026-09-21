import { render, screen, fireEvent } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import App from "./App";
import { api, watchJob } from "./api";
vi.mock("./api", () => ({
  enc: encodeURIComponent,
  api: vi.fn(async (path: string) =>
    path === "/subjects"
      ? { subjects: [] }
      : path === "/health"
        ? { ok: true, model: { configured: false } }
        : { threads: [] },
  ),
  watchJob: vi.fn(),
}));
it("returns to the last curriculum and selected session after reload", async () => {
  localStorage.setItem("open-tutor-subject", "z-last");
  localStorage.setItem("open-tutor-thread:z-last", "t-old");
  localStorage.setItem("open-tutor-node:z-last", "m");
  const subjects = ["a-first", "z-last"].map((subject) => ({
    subject,
    title: subject,
    status: "compiled",
    node_count: 1,
    source_count: 1,
    grounded_count: 1,
  }));
  const detail = {
    spec: {
      subject: "z-last",
      title: "Z last",
      nodes: [
        {
          id: "n",
          title: "Node",
          defn: "Definition",
          prereqs: [],
          status: "grounded",
          grounding_corpus: [],
          misconceptions: [],
        },
      ],
      corpus: [],
      scope: {},
      tiers: {},
    },
    report: {},
    decision: { status: "compiled" },
    state: { nodes: {} },
    gates: {},
    due: [],
  };
  detail.spec.nodes.push({
    ...detail.spec.nodes[0],
    id: "m",
    title: "Remembered topic",
    defn: "Remembered definition",
  });
  vi.mocked(api).mockImplementation(async (path) =>
    path === "/health"
      ? { ok: true }
      : path === "/subjects"
        ? { subjects }
        : path.startsWith("/subjects/")
          ? detail
          : path.startsWith("/threads?")
            ? { threads: [{ id: "t-new" }, { id: "t-old" }] }
            : { thread: { id: "t-old" }, messages: [] },
  );
  render(<App />);
  await screen.findByText("Remembered definition");
  expect(api).toHaveBeenCalledWith("/subjects/z-last");
  expect(api).toHaveBeenCalledWith("/threads/t-old");
});
it("has a real empty-library onboarding path and designer", async () => {
  render(<App />);
  expect(
    await screen.findByRole("heading", { name: /A little more.*understood/ }),
  ).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "Design your first curriculum" }),
  );
  expect(
    screen.getByRole("dialog", { name: "Follow your curiosity" }),
  ).toBeInTheDocument();
  expect(
    screen.getByLabelText("What would you like to learn?"),
  ).toBeInTheDocument();
  expect(
    screen.getByLabelText("Review before adding to my library"),
  ).not.toBeChecked();
});
it("offers learn, library, research and progress navigation without login", async () => {
  render(<App />);
  expect(
    await screen.findByRole("navigation", { name: "Main navigation" }),
  ).toBeInTheDocument();
  for (const name of ["Learn", "Library", "Research", "Progress"])
    expect(screen.getByRole("button", { name })).toBeInTheDocument();
  expect(screen.queryByText("Sign in")).toBeNull();
});
it("offers a keyboard-accessible focus layout and current navigation", async () => {
  render(<App />);
  const learn = await screen.findByRole("button", { name: "Learn" });
  expect(learn).toHaveAttribute("aria-current", "page");
  const focus = screen.getByRole("button", { name: "Hide learning path" });
  fireEvent.click(focus);
  expect(screen.getByRole("button", { name: "Show learning path" })).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  expect(screen.getByRole("button", { name: "Library" })).toHaveAttribute("aria-current", "page");
  expect(learn).not.toHaveAttribute("aria-current");
  fireEvent.click(learn);
  fireEvent.click(screen.getByRole("button", { name: "Show learning path" }));
  expect(screen.getByRole("button", { name: "Hide learning path" })).toHaveAttribute("aria-expanded", "true");
});
it("starts a guided adaptive lesson using canonical API fields and honest footer", async () => {
 const node = { id: "qubit", title: "Qubit", defn: "Quantum foundations", prereqs: [], status: "grounded", grounding_corpus: [], misconceptions: [] };
 const detail = { spec: { subject: "quantum", title: "Quantum", nodes: [node], corpus: [], scope: {}, tiers: {} }, report: {}, state: { nodes: {} }, gates: {}, due: [] };
 vi.mocked(api).mockImplementation(async (path) => {
  if (path === "/health") return { ok: true };
  if (path === "/learner/preferences") return { schema_version: "1", approach: "auto", pace: "balanced", goal: "", experience: "", interests: "" };
  if (path === "/subjects") return { subjects: [{ subject: "quantum", title: "Quantum", status: "compiled" }] };
  if (path.startsWith("/subjects/")) return detail;
  if (path.startsWith("/threads?")) return { threads: [] };
  if (path === "/threads") return { id: "t1", subject: "quantum" };
  if (path.endsWith("/ask")) return { job_id: "j1" };
  return { thread: { id: "t1" }, messages: [], teaching_state: {} };
 });
 vi.mocked(watchJob).mockResolvedValue({ id: "j1", status: "completed", stage: "done" });
 render(<App />);
 fireEvent.click(await screen.findByRole("button", { name: /Start a guided lesson/ }));
 await vi.waitFor(() => expect(api).toHaveBeenCalledWith("/threads/t1/ask", expect.objectContaining({ teaching: true, action: "respond", node_id: "qubit" })));
 expect(screen.queryByText(/every answer deterministic-verified/)).toBeNull();
 expect(screen.getByText(/AI explanations labeled/)).toBeInTheDocument();
});

it("greets a fresh curriculum with a tutor-led opener and a one-click guided start", async () => {
 const node = { id: "qubit", title: "Qubit", defn: "Quantum foundations", prereqs: [], status: "grounded", grounding_corpus: [], misconceptions: [] };
 const detail = { spec: { subject: "quantum", title: "Quantum", nodes: [node], corpus: [], scope: {}, tiers: {} }, report: {}, state: { nodes: {} }, gates: {}, due: [] };
 vi.mocked(api).mockImplementation(async (path) => {
   if (path === "/health") return { ok: true };
   if (path === "/learner/preferences") return { schema_version: "1", approach: "auto", pace: "balanced", goal: "", experience: "", interests: "" };
   if (path === "/subjects") return { subjects: [{ subject: "quantum", title: "Quantum", status: "compiled" }] };
   if (path.startsWith("/subjects/")) return detail;
   if (path.startsWith("/threads?")) return { threads: [] };
   if (path === "/threads") return { id: "t1", subject: "quantum" };
   if (path.endsWith("/ask")) return { job_id: "j1" };
   return { thread: { id: "t1" }, messages: [], teaching_state: {} };
 });
 vi.mocked(watchJob).mockResolvedValue({ id: "j1", status: "completed", stage: "done" });
 render(<App />);
 await screen.findByText(/Your curriculum · a conversation, not a lecture/);
 await vi.waitFor(() =>
   expect(api).toHaveBeenCalledWith(
     "/threads/t1/ask",
     expect.objectContaining({ teaching: true, action: "respond", node_id: "qubit" }),
   ),
 );
 // Opener fires exactly once per curriculum, even after re-renders.
 expect(
   vi
     .mocked(api)
     .mock.calls.filter((c) => c[0] === "/threads/t1/ask"),
 ).toHaveLength(1);
 // The learner never faces a blank composer: the guided-start button rides along.
 expect(screen.getByRole("button", { name: /Start a guided lesson/ })).toBeInTheDocument();
});

const quantumFixture = () => {
  const node = { id: "qubit", title: "Qubit", defn: "Quantum foundations", prereqs: [], status: "grounded", grounding_corpus: [], misconceptions: [] };
  const detail = { spec: { subject: "quantum", title: "Quantum", nodes: [node], corpus: [], scope: {}, tiers: {} }, report: {}, state: { nodes: {} }, gates: {}, due: [] };
  return detail;
};

const assistant = (id: string, content: string) => ({ id, role: "assistant", content, created_at: new Date().toISOString() });
let learnerSequence = 0;
const learner = (content: string) => ({ id: `u-${++learnerSequence}`, role: "user", content, created_at: new Date().toISOString() });

it("offers a one-tap catch-up after a same-day gap", async () => {
  localStorage.clear();
  localStorage.setItem("open-tutor-subject", "quantum");
  localStorage.setItem("open-tutor-thread:quantum", "t1");
  localStorage.setItem(
    "open-tutor-last-visit:quantum",
    String(Date.now() - 5 * 36e5),
  );
  const detail = quantumFixture();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/health") return { ok: true };
    if (path === "/learner/preferences") return { schema_version: "1", approach: "auto", pace: "balanced", goal: "", experience: "", interests: "" };
    if (path === "/subjects") return { subjects: [{ subject: "quantum", title: "Quantum", status: "compiled" }] };
    if (path.startsWith("/subjects/")) return detail;
    if (path.startsWith("/threads?")) return { threads: [{ id: "t1" }] };
    if (path === "/threads") return { id: "t2", subject: "quantum" };
    if (path.endsWith("/ask")) return { job_id: "j9" };
    return { thread: { id: "t1" }, messages: [assistant("a1", "Qubits hold two possibilities at once.")], teaching_state: {} };
  });
  vi.mocked(watchJob).mockResolvedValue({ id: "j9", status: "completed", stage: "done" });
  render(<App />);
  const banner = await screen.findByText(/Welcome back/);
  expect(banner.textContent).toMatch(/5h/);
  // Under 20h the tutor offers, it doesn't grab: recap fires only on tap.
  expect(screen.queryByText(/catching you up/i)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Catch me up" }));
  await vi.waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      "/threads/t1/ask",
      expect.objectContaining({
        question: expect.stringMatching(/^I'm back after a break/),
        teaching: true,
        action: "respond",
      }),
    ),
  );
  expect(screen.queryByText(/Welcome back/)).toBeNull();
});

it("catches the learner up unprompted after a long absence", async () => {
  localStorage.clear();
  localStorage.setItem("open-tutor-subject", "quantum");
  localStorage.setItem("open-tutor-thread:quantum", "t1");
  localStorage.setItem(
    "open-tutor-last-visit:quantum",
    String(Date.now() - 30 * 36e5),
  );
  const detail = quantumFixture();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/health") return { ok: true };
    if (path === "/learner/preferences") return { schema_version: "1", approach: "auto", pace: "balanced", goal: "", experience: "", interests: "" };
    if (path === "/subjects") return { subjects: [{ subject: "quantum", title: "Quantum", status: "compiled" }] };
    if (path.startsWith("/subjects/")) return detail;
    if (path.startsWith("/threads?")) return { threads: [{ id: "t1" }] };
    if (path === "/threads") return { id: "t2", subject: "quantum" };
    if (path.endsWith("/ask")) return { job_id: "j8" };
    return { thread: { id: "t1" }, messages: [assistant("a1", "Qubits hold two possibilities at once.")], teaching_state: {} };
  });
  vi.mocked(watchJob).mockResolvedValue({ id: "j8", status: "completed", stage: "done" });
  render(<App />);
  await vi.waitFor(() =>
    expect(api).toHaveBeenCalledWith(
      "/threads/t1/ask",
      expect.objectContaining({
        question: expect.stringMatching(/^I'm back after a break/),
      }),
    ),
  );
  // Exactly one recap per visit, and no banner on top of the auto recap.
  expect(
    vi
      .mocked(api)
      .mock.calls.filter(
        (c) =>
          c[0] === "/threads/t1/ask" &&
          String((c[1] as { question?: string }).question || "").startsWith(
            "I'm back",
          ),
      ),
  ).toHaveLength(1);
  expect(screen.queryByText(/Welcome back/)).toBeNull();
});

it("keeps the lesson moving after the learner answers, then waits again", { timeout: 20000 }, async () => {
  localStorage.clear();
  localStorage.setItem("open-tutor-subject", "quantum");
  localStorage.setItem("open-tutor-thread:quantum", "t1");
  const detail = quantumFixture();
  let stage = 0;
  const history = () => {
    const base = [
      assistant("a1", "A qubit is a two-state system."),
      learner("My own answer about qubits"),
    ];
    if (stage >= 1) base.push(assistant("a2", "Nice — next small step."),);
    if (stage >= 1) base.push(learner("Keep teaching me: take the next small step from my last answer."));
    if (stage >= 2) base.push(assistant("a3", "And one more step."),);
    if (stage >= 2) base.push(learner("Keep teaching me: take the next small step from my last answer."));
    if (stage >= 3) base.push(assistant("a4", "Where this leads."));
    return base;
  };
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/health") return { ok: true };
    if (path === "/learner/preferences") return { schema_version: "1", approach: "auto", pace: "balanced", goal: "", experience: "", interests: "" };
    if (path === "/subjects") return { subjects: [{ subject: "quantum", title: "Quantum", status: "compiled" }] };
    if (path.startsWith("/subjects/")) return detail;
    if (path.startsWith("/threads?")) return { threads: [{ id: "t1" }] };
    if (path === "/threads") return { id: "t2", subject: "quantum" };
    if (path.endsWith("/ask")) {
      stage += 1;
      return { job_id: "j" + stage };
    }
    return { thread: { id: "t1" }, messages: history(), teaching_state: {} };
  });
  vi.mocked(watchJob).mockResolvedValue({ id: "jx", status: "completed", stage: "done" });
  render(<App />);
  // First self-driven step fires after the paced pause…
  await vi.waitFor(
    () => expect(stage).toBeGreaterThanOrEqual(1),
    { timeout: 6000 },
  );
  // …and a second consecutive one, because the learner was still silent.
  await vi.waitFor(
    () => expect(stage).toBeGreaterThanOrEqual(2),
    { timeout: 6000 },
  );
  // …but the tutor then waits: no third self-driven ask without a learner.
  await new Promise((r) => setTimeout(r, 3200));
  expect(stage).toBe(2);
  const advanceCalls = vi
    .mocked(api)
    .mock.calls.filter(
      (c) =>
        c[0] === "/threads/t1/ask" &&
        (c[1] as { question?: string }).question ===
          "Keep teaching me: take the next small step from my last answer.",
    );
  expect(advanceCalls).toHaveLength(2);
});
