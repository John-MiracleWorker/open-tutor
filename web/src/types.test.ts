import { expect, it } from "vitest";
import type { TeachingState } from "./types";

it("represents the server's cleared teaching context without invented strings", () => {
  // Typecheck must accept the real no-node API shape, not just an absent field.
  const cleared: TeachingState = { node_id: null, pending_question: null, turn_count: 0 };
  expect(JSON.parse(JSON.stringify(cleared))).toEqual({
    node_id: null, pending_question: null, turn_count: 0,
  });
});
