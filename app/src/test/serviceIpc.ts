import { act } from "@testing-library/react";
import { mockIPC } from "@tauri-apps/api/mocks";
import { vi } from "vitest";
import type {
  ApiFieldError,
  Assessment,
  FrameworkSummary,
  HealthResponse,
  SupervisorState,
} from "@/service/types";

// Test-only helpers for the frontend's IPC boundary. Uses Tauri's official
// `mockIPC`, so the real `invoke` path in `src/service/commands.ts` runs
// unchanged; only the Rust side is replaced.

export type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
};

export function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

export const HEALTHY: HealthResponse = {
  status: "ok",
  service: "controlgauge-service",
  version: "0.1.0",
};

export const FRAMEWORK: FrameworkSummary = {
  id: "fw-test",
  name: "Test Framework",
  version: "Revision 1",
  source_reference: null,
  control_count: 0,
};

const DATA_COMMANDS = ["list_frameworks", "list_assessments", "get_assessment", "create_assessment"] as const;
type DataCommand = (typeof DATA_COMMANDS)[number];

export function apiError(status: number, code: string, message: string, fields: ApiFieldError[] = []) {
  return { kind: "api_error" as const, status, code, message, fields };
}

/**
 * Mocks the Tauri commands the frontend uses. Status is answered
 * immediately from `setStatus`; every health request gets its own deferred
 * promise in `health`, settled explicitly by the test, so async ordering is
 * controlled rather than timed. Data commands are served from an in-memory
 * store (`frameworks`, `assessments`, newest first) that mirrors the
 * service's documented behavior; `failNext` makes the next call of a command
 * reject with a given `ServiceCallError`, and `hold` keeps a command's calls
 * pending until the test settles them. Any other command is recorded and
 * rejected.
 */
export function mockServiceIpc(initial: SupervisorState = { state: "ready" }) {
  let status = initial;
  const calls = { status: 0, data: [] as { cmd: DataCommand; args: Record<string, unknown> }[] };
  const health: Deferred<HealthResponse>[] = [];
  const unexpected: string[] = [];
  const frameworks: FrameworkSummary[] = [FRAMEWORK];
  const assessments: Assessment[] = [];
  const failures = new Map<DataCommand, unknown>();
  const held = new Map<DataCommand, Deferred<void>[]>();
  let seq = 0;

  function serve(cmd: DataCommand, args: Record<string, unknown>): unknown {
    switch (cmd) {
      case "list_frameworks":
        return { frameworks };
      case "list_assessments": {
        const limit = Number(args.limit);
        const offset = Number(args.offset);
        return { assessments: assessments.slice(offset, offset + limit), total: assessments.length, limit, offset };
      }
      case "get_assessment": {
        const found = assessments.find((a) => a.id === args.id);
        if (!found) throw apiError(404, "assessment_not_found", "The assessment was not found.");
        return found;
      }
      case "create_assessment": {
        const input = args.input as { name: string; description: string | null; frameworkId: string };
        if (!input.name.trim()) {
          throw apiError(422, "validation_failed", "The request is not valid.", [
            { field: "name", code: "blank", message: "This field cannot be blank." },
          ]);
        }
        const framework = frameworks.find((f) => f.id === input.frameworkId);
        if (!framework) {
          throw apiError(422, "validation_failed", "The request is not valid.", [
            { field: "framework_id", code: "framework_not_found", message: "The selected framework is not available." },
          ]);
        }
        seq++;
        const created: Assessment = {
          id: `00000000-0000-4000-8000-${String(seq).padStart(12, "0")}`,
          name: input.name.trim(),
          description: input.description,
          framework: { id: framework.id, name: framework.name, version: framework.version },
          created_at: `2026-11-02T12:00:${String(seq).padStart(2, "0")}Z`,
        };
        assessments.unshift(created);
        return created;
      }
    }
  }

  mockIPC(async (cmd, payload) => {
    if (cmd === "get_service_status") {
      calls.status++;
      return status;
    }
    if (cmd === "check_service_health") {
      const request = deferred<HealthResponse>();
      health.push(request);
      return request.promise;
    }
    if ((DATA_COMMANDS as readonly string[]).includes(cmd)) {
      const dataCmd = cmd as DataCommand;
      const args = (payload ?? {}) as Record<string, unknown>;
      calls.data.push({ cmd: dataCmd, args });
      const gate = held.get(dataCmd);
      if (gate) {
        const wait = deferred<void>();
        gate.push(wait);
        await wait.promise;
      }
      if (failures.has(dataCmd)) {
        const err = failures.get(dataCmd);
        failures.delete(dataCmd);
        throw err;
      }
      return serve(dataCmd, args);
    }
    unexpected.push(cmd);
    throw new Error(`unexpected IPC command: ${cmd}`);
  });

  return {
    setStatus(next: SupervisorState) {
      status = next;
    },
    calls,
    health,
    unexpected,
    frameworks,
    assessments,
    failNext(cmd: DataCommand, error: unknown) {
      failures.set(cmd, error);
    },
    /** Holds every later call of `cmd`; returns the queue of pending gates. */
    hold(cmd: DataCommand) {
      const gate: Deferred<void>[] = [];
      held.set(cmd, gate);
      return gate;
    },
  };
}

export type ServiceIpcMock = ReturnType<typeof mockServiceIpc>;

/** Advances fake timers inside `act`, flushing promise callbacks in between. */
export async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

/** Runs `fn` (e.g. settling a deferred) inside `act` and flushes the result. */
export async function settle(fn: () => void = () => {}) {
  await act(async () => {
    fn();
    await vi.advanceTimersByTimeAsync(0);
  });
}
