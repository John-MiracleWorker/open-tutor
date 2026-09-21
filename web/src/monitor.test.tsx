import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { it, expect, vi, beforeEach } from "vitest";
import App from "./App";
import { api, watchJob } from "./api";

vi.mock("./api", () => ({
  enc: encodeURIComponent,
  api: vi.fn(async () => ({})),
  watchJob: vi.fn(),
}));

const node = {
  id: "qubit", title: "Qubit", defn: "Quantum foundations", prereqs: [],
  status: "grounded", grounding_corpus: [], misconceptions: [],
};
const detail = {
  spec: { subject: "quantum", title: "Quantum", nodes: [node], corpus: [], scope: {}, tiers: {} },
  report: {}, state: { nodes: {} }, gates: {}, due: [],
};

it("monitors every accepted ask even when the readback after ask fails", async () => {
  let askCount = 0;
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/health") return { ok: true };
    if (path === "/learner/preferences")
      return { schema_version: "1", approach: "auto", pace: "balanced", goal: "", experience: "", interests: "" };
    if (path === "/subjects") return { subjects: [{ subject: "quantum", title: "Quantum", status: "compiled" }] };
    if (path.startsWith("/subjects/")) return detail;
    if (path.startsWith("/threads?")) return { threads: [] };
    if (path === "/threads") return { id: "t1", subject: "quantum" };
    if (path.endsWith("/ask")) {
      askCount += 1;
      return { job_id: "j" + askCount };
    }
    // The readback right after each accepted ask fails (transient drop).
    throw new Error("readback failed");
  });
  vi.mocked(watchJob).mockResolvedValue({ id: "j1", status: "completed", stage: "done" });
  render(<App />);
  // On a cold start the tutor's opener ask goes first; it is accepted and
  // monitored even though the readback immediately after it fails.
  await vi.waitFor(() => expect(watchJob).toHaveBeenCalledWith("j1", expect.anything()));
  // Once the opener settles, a learner click sends exactly one more ask —
  // also accepted, also monitored despite the failing readback.
  await vi.waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  fireEvent.click(await screen.findByRole("button", { name: /Start a guided lesson/ }));
  await vi.waitFor(() => expect(watchJob).toHaveBeenCalledWith("j2", expect.anything()));
  expect(askCount).toBe(2);
  expect(watchJob).toHaveBeenCalledTimes(2);
});

it("keeps the running job indicator when the readback after an accepted ask fails", async () => {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/health") return { ok: true };
    if (path === "/learner/preferences")
      return { schema_version: "1", approach: "auto", pace: "balanced", goal: "", experience: "", interests: "" };
    if (path === "/subjects") return { subjects: [{ subject: "quantum", title: "Quantum", status: "compiled" }] };
    if (path.startsWith("/subjects/")) return detail;
    if (path.startsWith("/threads?")) return { threads: [] };
    if (path === "/threads") return { id: "t1", subject: "quantum" };
    if (path.endsWith("/ask")) return { job_id: "j1" };
    throw new Error("readback failed");
  });
  // The job stays running: monitor never resolves it during this test.
  vi.mocked(watchJob).mockImplementation(
    () => new Promise(() => {}) as Promise<{ id: string; status: string; stage: string }>,
  );
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /Start a guided lesson/ }));
  // The accepted job must stay visible as a running status even though the
  // readback threw; the catch path must not clear it.
  await vi.waitFor(() => expect(watchJob).toHaveBeenCalled());
  expect(await screen.findByRole("status")).toBeInTheDocument();
});

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});