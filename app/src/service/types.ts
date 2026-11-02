/**
 * Mirrors `app/src-tauri/src/supervisor::SupervisorState`'s
 * `#[serde(tag = "state", rename_all = "snake_case")]` JSON shape exactly.
 * The frontend never receives connection details (host/port/token) at any
 * state, including `ready` -- only this narrow status shape, per S1-04's
 * "smaller exposed capability" decision (see `check_service_health`'s doc
 * comment in `lib.rs`).
 */
export type SupervisorState =
  | { state: "starting" }
  | { state: "awaiting_endpoint" }
  | { state: "verifying_identity" }
  | { state: "installing_credential" }
  | { state: "ready" }
  | { state: "failed"; reason: string }
  | { state: "stopped" };

// --- Sprint 2 assessment commands --------------------------------------------
// Mirror `app/src-tauri/src/supervisor/service_api.rs` (closed schemas).

export type FrameworkSummary = {
  id: string;
  name: string;
  version: string;
  source_reference: string | null;
  /** Controls loaded for this framework; 0 until a catalog is imported. */
  control_count: number;
};

export type FrameworkRef = { id: string; name: string; version: string };

export type Assessment = {
  id: string;
  name: string;
  description: string | null;
  framework: FrameworkRef;
  /** ISO 8601, UTC. */
  created_at: string;
};

export type AssessmentList = {
  assessments: Assessment[];
  total: number;
  limit: number;
  offset: number;
};

export type NewAssessment = {
  name: string;
  description: string | null;
  frameworkId: string;
};

export type ApiFieldError = { field: string; code: string; message: string };

/** `ServiceCallError` from Rust, tagged by `kind`. */
export type ServiceCallError =
  | {
      kind:
        | "service_not_ready"
        | "service_no_longer_alive"
        | "invalid_argument"
        | "request_failed"
        | "response_too_large"
        | "response_malformed"
        | "unauthorized";
    }
  | { kind: "unexpected_status"; status: number }
  | { kind: "api_error"; status: number; code: string; message: string; fields: ApiFieldError[] };

/** Shape of the authenticated `GET /health` body (`service/src/controlgauge_service/routes/health.py`). */
export type HealthResponse = {
  status: string;
  service: string;
  version: string;
};
