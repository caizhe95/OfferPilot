import { expect, test } from "@playwright/test";
import { initialRunViewState, reduceRunState } from "../../src/web/src/lib/run-state";
import type { Run, RunEvent } from "../../src/web/src/lib/types";

const run: Run = { id: "run-1", session_id: "session-1", type: "diagnosis", status: "pending", input: {} };

function event(type: string, sequence: number, data: Record<string, unknown> = {}): RunEvent {
  return { type, session_id: "session-1", run_id: "run-1", sequence, created_at: "now", data };
}

test("Run reducer keeps durable ordering, deduplicates and settles terminal states", () => {
  let state = reduceRunState(initialRunViewState, { type: "set_run", run });
  state = reduceRunState(state, { type: "event", event: event("run_started", 1) });
  state = reduceRunState(state, { type: "event", event: event("diagnosis_model_progress", 2, { received_chars: 80 }) });
  state = reduceRunState(state, { type: "event", event: event("diagnosis_model_progress", 2, { received_chars: 80 }) });
  state = reduceRunState(state, { type: "event", event: event("run_completed", 3) });
  expect(state.events.map((item) => item.sequence)).toEqual([1, 2, 3]);
  expect(state.run?.status).toBe("completed");
  expect(state.lastSequence).toBe(3);
});

test("Run reducer ignores unknown events without changing the current phase", () => {
  let state = reduceRunState(initialRunViewState, { type: "set_run", run });
  state = reduceRunState(state, { type: "event", event: event("future_event", 1, { value: "ignored by UI" }) });
  expect(state.phase).toBe("等待开始");
  expect(state.lastSequence).toBe(1);
});

test("Run reducer keeps a run waiting when run_complete reports approval", () => {
  let state = reduceRunState(initialRunViewState, { type: "set_run", run: { ...run, status: "running" } });
  state = reduceRunState(state, { type: "event", event: event("run_complete", 1, { status: "waiting_approval" }) });
  expect(state.run?.status).toBe("waiting_approval");
});

test("Run reducer deduplicates by trace and sequence", () => {
  const first = { ...event("diagnosis_model_progress", 1, { received_chars: 10 }), trace_id: "trace-a" };
  const second = { ...event("diagnosis_model_progress", 1, { received_chars: 20 }), trace_id: "trace-b" };
  let state = reduceRunState(initialRunViewState, { type: "set_run", run });
  state = reduceRunState(state, { type: "event", event: first });
  state = reduceRunState(state, { type: "event", event: first });
  state = reduceRunState(state, { type: "event", event: second });
  expect(state.events).toHaveLength(2);
});
