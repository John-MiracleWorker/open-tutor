import type { Job } from "./types";
export async function api<T>(
  path: string,
  body?: unknown,
  method?: string,
): Promise<T> {
  const token = sessionStorage.getItem("open-tutor-token");
  const response = await fetch("/api" + path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const d = await response.json();
      detail =
        typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail || d);
    } catch {
      /* retain status */
    }
    throw new Error(detail);
  }
  return response.json();
}
export async function watchJob(
  id: string,
  onUpdate: (job: Job) => void,
  signal?: AbortSignal,
): Promise<Job> {
  const terminal = (j: Job) =>
    [
      "completed",
      "done",
      "success",
      "failed",
      "error",
      "interrupted",
      "cancelled",
      "blocked",
      "model-error",
    ].includes(j.status);
  if (signal?.aborted) throw new DOMException("Stopped watching", "AbortError");
  let latest = await api<Job>("/jobs/" + encodeURIComponent(id));
  onUpdate(latest);
  if (terminal(latest)) return latest;
  return new Promise<Job>((resolve, reject) => {
    let settled = false,
      reading = false,
      failures = 0;
    let stream: EventSource | undefined;
    const cleanup = () => {
      settled = true;
      stream?.close();
      clearInterval(poll);
      clearTimeout(deadline);
      signal?.removeEventListener("abort", abort);
    };
    const abort = () => {
      cleanup();
      reject(new DOMException("Stopped watching", "AbortError"));
    };
    const readback = async () => {
      if (settled || reading) return;
      reading = true;
      try {
        const j = await api<Job>("/jobs/" + encodeURIComponent(id));
        if (settled) return;
        latest = j;
        onUpdate(j);
        failures = 0;
        if (terminal(j)) {
          cleanup();
          resolve(j);
        }
      } catch (e) {
        if (++failures >= 3) {
          cleanup();
          reject(e);
        }
      } finally {
        reading = false;
      }
    };
    const poll = setInterval(() => void readback(), 2500);
    const deadline = setTimeout(
      () => {
        cleanup();
        reject(
          new Error(
            "This job is taking longer than expected. It remains saved on the server; reopen the session to reconnect.",
          ),
        );
      },
      20 * 60 * 1000,
    );
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) {
      abort();
      return;
    }
    if (typeof EventSource !== "undefined") {
      stream = new EventSource(
        "/api/jobs/" + encodeURIComponent(id) + "/events",
      );
      stream.addEventListener("progress", (e) => {
        try {
          const d = JSON.parse((e as MessageEvent).data);
          if (!settled && typeof d.stage === "string")
            onUpdate({ ...latest, stage: d.stage });
        } catch {
          /* durable readback remains authoritative */
        }
      });
      stream.addEventListener("done", () => void readback());
      // No model-token listener: only persisted, gate-checked answers are read.
      stream.onerror = () => {
        stream?.close();
        void readback();
      };
    }
  });
}
export const enc = encodeURIComponent;
