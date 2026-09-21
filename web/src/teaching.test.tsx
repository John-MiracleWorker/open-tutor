import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TeachingCard, TeachingPreferences, TeachingBar } from "./teaching";
import { api } from "./api";
import type { Answer, LearnerPreferences } from "./types";
vi.mock("./api", () => ({ api: vi.fn(), enc: encodeURIComponent }));
const preferences: LearnerPreferences = { schema_version: "1", approach: "auto", pace: "balanced", goal: "", experience: "", interests: "" };
const evidence: Answer = { grounded: true, status: "grounded", draft: "## Claim\nA qubit has two basis states. [1]", citations: [{ source_id: "q", source_name: "Qubit reference", url: "https://example.org", text: "A qubit has two basis states.", char_start: 0, char_end: 28 }] };
const answer: Answer = { grounded: false, status: "coaching", draft: "Let’s build the idea.", citations: evidence.citations, evidence_grounded: true, verification_scope: "evidence-only", evidence, teaching: { schema_version: "1", status: "ready", verified: false, approach: "visual", pace: "gentle", intent: "scaffold", reason: "You asked for a different approach.", title: "Two outcomes, one system", explanation: "Let’s build the idea.", steps: [{ title: "Notice", body: "Keep the outcome separate from the state." }], diagram: { title: "State to observation", nodes: [{ id: "a", label: "State", detail: "Before observation" }, { id: "b", label: "Outcome", detail: "After observation" }], edges: [{ from: "a", to: "b", label: "measure" }] }, activity: { kind: "predict", prompt: "What might we observe?" }, evidence_refs: [1], warnings: [], model: "local-model", latency_ms: 5000 } };
describe("adaptive teacher", () => {
 it("separates AI explanation from verified evidence without a green coaching badge", () => {
  render(<TeachingCard answer={answer} />);
  expect(screen.getByText("AI explanation · not independently verified")).toBeInTheDocument();
  expect(screen.queryByText("GROUNDED")).toBeNull();
  const region = screen.getByRole("article", { name: "AI teaching response" });
  expect(within(region).getByText("Two outcomes, one system")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Inspect checked evidence/ }));
  expect(screen.getByRole("dialog", { name: "Evidence, separately checked" })).toBeInTheDocument();
  expect(screen.getByText("GROUNDED")).toBeInTheDocument();
 });
 it("offers one activity and sends explicit adaptation actions", () => {
  const onAction = vi.fn(), onReply = vi.fn();
  render(<TeachingCard answer={answer} onAction={onAction} onReply={onReply} />);
  expect(screen.getByText("What might we observe?")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Another way" }));
  expect(onAction).toHaveBeenCalledWith("another-way");
  fireEvent.click(screen.getByRole("button", { name: "Try an answer" }));
  expect(onReply).toHaveBeenCalledOnce();
  expect(screen.getByText(/doesn’t change your mastery score/)).toBeInTheDocument();
 });
 it("keeps secondary teaching actions behind a reversible disclosure", () => {
  const onAction = vi.fn();
  render(<TeachingCard answer={answer} onAction={onAction} />);
  expect(screen.queryByRole("button", { name: "Map it out" })).toBeNull();
  const more = screen.getByRole("button", { name: "More ways to learn" });
  expect(more).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(more);
  expect(more).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(screen.getByRole("button", { name: "Map it out" }));
  expect(onAction).toHaveBeenCalledWith("visual");
  expect(screen.queryByRole("button", { name: "Map it out" })).toBeNull();
 });
 it("disables quick actions while a turn is running", () => {
  render(<TeachingCard answer={answer} busy onAction={vi.fn()} onReply={vi.fn()} />);
  expect(screen.getByRole("button", { name: "Just a hint" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Another way" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Try an answer" })).toBeDisabled();
 });
 it("renders diagrams as safe, expandable text data", () => {
  const hostile = structuredClone(answer);
  hostile.teaching!.diagram!.nodes[0].detail = "<img src=x onerror=alert(1)>";
  render(<TeachingCard answer={hostile} />);
  fireEvent.click(screen.getByRole("button", { name: /State: show detail/ }));
  expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
  expect(document.querySelector("img")).toBeNull();
  expect(screen.getByText("measure")).toBeInTheDocument();
 });
 it("does not dress a failed tutor up as a completed lesson", () => {
  const fallback = structuredClone(answer);
  fallback.teaching = { ...fallback.teaching!, status: "fallback", explanation: "Local model unavailable", activity: null, diagram: null, steps: [], warnings: ["Connection refused"] };
  render(<TeachingCard answer={fallback} />);
  expect(screen.getByText("Tutor unavailable")).toBeInTheDocument();
  expect(screen.getByText("Connection refused")).toBeInTheDocument();
  expect(screen.queryByText("What might we observe?")).toBeNull();
  expect(screen.getByRole("button", { name: /Inspect checked evidence/ })).toBeInTheDocument();
 });
 it("shows a blocked evidence verdict even when flags contradict it", () => {
  const blocked = structuredClone(answer);
  blocked.teaching!.status = "blocked";
  blocked.evidence!.status = "oracle-failed";
  render(<TeachingCard answer={blocked} />);
  expect(screen.getByText("Evidence needs attention")).toBeInTheDocument();
  expect(screen.queryByText("Evidence checked")).toBeNull();
 });
});
describe("learner-controlled preferences", () => {
 it("loads and saves local preferences without claiming diagnosed learning styles", async () => {
  vi.mocked(api).mockResolvedValue(preferences);
  const saved = vi.fn();
  render(<TeachingPreferences onClose={vi.fn()} onSaved={saved} />);
  const goal = await screen.findByLabelText("What are you working toward?");
  fireEvent.change(goal, { target: { value: "Understand physics intuitively" } });
  fireEvent.click(screen.getByRole("button", { name: /Visual map/ }));
  fireEvent.change(screen.getByLabelText("Pace"), { target: { value: "gentle" } });
  const expected = { ...preferences, goal: "Understand physics intuitively", approach: "visual", pace: "gentle" };
  vi.mocked(api).mockResolvedValue(expected);
  fireEvent.click(screen.getByRole("button", { name: "Save my preferences" }));
  await vi.waitFor(() => expect(api).toHaveBeenCalledWith("/learner/preferences", expected, "PUT"));
  await vi.waitFor(() => expect(saved).toHaveBeenCalledWith(expected));
  expect(screen.getByText(/preferences, not fixed learning styles/)).toBeInTheDocument();
 });
 it("shows a failed load rather than overwriting existing preferences with defaults", async () => {
  vi.mocked(api).mockRejectedValue(new Error("Offline"));
  render(<TeachingPreferences onClose={vi.fn()} onSaved={vi.fn()} />);
  expect(await screen.findByText("Offline")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save my preferences" })).toBeNull();
 });
 it("reset persists explicit defaults only when saved", async () => {
  vi.mocked(api).mockResolvedValue({ ...preferences, approach: "analogy", goal: "Goal" });
  render(<TeachingPreferences onClose={vi.fn()} onSaved={vi.fn()} />);
  await screen.findByDisplayValue("Goal");
  fireEvent.click(screen.getByRole("button", { name: "Reset preferences" }));
  expect(screen.getByLabelText("What are you working toward?")).toHaveValue("");
  expect(api).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Save my preferences" }));
  await vi.waitFor(() => expect(api).toHaveBeenCalledWith("/learner/preferences", preferences, "PUT"));
 });
 it("the teaching bar makes current preferences discoverable", () => {
  const open = vi.fn();
  render(<TeachingBar preferences={{ ...preferences, approach: "socratic", pace: "gentle" }} onOpen={open} />);
  expect(screen.getByText("Questions first")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Personalize your tutor/ }));
  expect(open).toHaveBeenCalledOnce();
 });
 it("the flow toggle persists and reports auto-advance on/off instantly", async () => {
  vi.mocked(api).mockResolvedValue(preferences);
  const onFlowChange = vi.fn();
  localStorage.setItem("open-tutor-auto-advance", "off");
  render(<TeachingPreferences onClose={vi.fn()} onSaved={vi.fn()} onFlowChange={onFlowChange} />);
  await screen.findByLabelText("Pace");
  const toggle = screen.getByRole("checkbox", { name: /Keep the lesson moving/ });
  expect(toggle).not.toBeChecked();
  fireEvent.click(toggle);
  expect(toggle).toBeChecked();
  expect(localStorage.getItem("open-tutor-auto-advance")).toBe("on");
  expect(onFlowChange).toHaveBeenCalledWith(true);
  expect(screen.getByText(/always drive/i)).toBeInTheDocument();
  localStorage.setItem("open-tutor-auto-advance", "on");
 });
});
