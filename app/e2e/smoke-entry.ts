// Test-only entry for the Playwright browser smoke test. Installs Tauri's
// official IPC mock in place of the Rust side, then loads the app's real
// production entry point (`src/main.tsx`) unchanged. Nothing here ships:
// the production build never references this file.
import { mockIPC } from "@tauri-apps/api/mocks";

type Status = { state: string; reason?: string };
type Assessment = {
  id: string;
  name: string;
  description: string | null;
  framework: { id: string; name: string; version: string };
  created_at: string;
};
type Control = {
  status: Status;
  health: "ok" | "reject";
  calls: string[];
  setStatus(next: Status): void;
};

const HEALTHY = { status: "ok", service: "controlgauge-service", version: "0.1.0" };
const FRAMEWORK = {
  id: "fw-smoke",
  name: "Smoke Framework",
  version: "Revision 1",
  source_reference: null,
  control_count: 0,
};
const assessments: Assessment[] = [];

const control: Control = {
  status: { state: "ready" },
  health: "ok",
  calls: [],
  setStatus(next) {
    control.status = next;
  },
};
// Exposed so the spec can drive state transitions.
(window as unknown as { __e2e: Control }).__e2e = control;

mockIPC((cmd, payload) => {
  control.calls.push(cmd);
  const args = (payload ?? {}) as Record<string, unknown>;
  switch (cmd) {
    case "get_service_status":
      return control.status;
    case "check_service_health":
      if (control.health === "reject") throw "health_request_failed";
      return HEALTHY;
    case "list_frameworks":
      return { frameworks: [FRAMEWORK] };
    case "list_assessments": {
      const limit = Number(args.limit);
      const offset = Number(args.offset);
      return { assessments: assessments.slice(offset, offset + limit), total: assessments.length, limit, offset };
    }
    case "get_assessment": {
      const found = assessments.find((a) => a.id === args.id);
      if (!found) throw { kind: "api_error", status: 404, code: "assessment_not_found", message: "The assessment was not found.", fields: [] };
      return found;
    }
    case "create_assessment": {
      const input = args.input as { name: string; description: string | null; frameworkId: string };
      const created: Assessment = {
        id: `00000000-0000-4000-8000-${String(assessments.length + 1).padStart(12, "0")}`,
        name: input.name,
        description: input.description,
        framework: { id: FRAMEWORK.id, name: FRAMEWORK.name, version: FRAMEWORK.version },
        created_at: "2026-11-02T12:00:00Z",
      };
      assessments.unshift(created);
      return created;
    }
  }
  throw new Error(`unexpected IPC command: ${cmd}`);
});

await import("../src/main.tsx");
