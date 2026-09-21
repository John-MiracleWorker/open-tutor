import { it, expect, vi, afterEach } from "vitest";
import { watchJob } from "./api";
afterEach(() => {
  vi.unstubAllGlobals();
});
it("subscribes to persisted SSE events and resolves the read-back job, never an unverified token", async () => {
  const events: Record<string, (e: MessageEvent) => void> = {};
  let closed = false;
  class Source {
    constructor(public url: string) {}
    addEventListener(name: string, fn: (e: MessageEvent) => void) {
      events[name] = fn;
    }
    close() {
      closed = true;
    }
    onerror: unknown;
  }
  vi.stubGlobal("EventSource", Source);
  const final = {
    id: "job",
    status: "completed",
    stage: "completed",
    result: {},
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () =>
        events.done
          ? final
          : { ...final, status: "running", stage: "retrieving" },
    })),
  );
  const updates = vi.fn();
  const task = watchJob("job", updates);
  await vi.waitFor(() => expect(events.done).toBeDefined());
  events.progress(
    new MessageEvent("progress", {
      data: JSON.stringify({ stage: "verifying" }),
    }),
  );
  events.done(
    new MessageEvent("done", { data: JSON.stringify({ status: "completed" }) }),
  );
  expect(await task).toEqual(final);
  expect(closed).toBe(true);
  expect(updates.mock.calls.some(([j]) => j.stage === "verifying")).toBe(true);
});
