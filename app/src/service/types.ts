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

export type ReleaseSummary = { id: string; version: string; title: string };

export type FrameworkSummary = {
  id: string;
  name: string;
  version: string;
  source_reference: string | null;
  /** Controls in the current catalog release; 0 while no catalog is imported. */
  control_count: number;
  /** The catalog release new assessments are pinned to; null if none is imported. */
  current_release: ReleaseSummary | null;
};

export type FrameworkRef = { id: string; name: string; version: string };

export type Assessment = {
  id: string;
  name: string;
  description: string | null;
  framework: FrameworkRef;
  /** The catalog release this assessment is pinned to (never changes once set). */
  catalog_release: ReleaseSummary | null;
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

// --- Sprint 3 catalog commands ------------------------------------------------
// Official text fields (`prose`, `title`, `label`, `guidelines`, `choices`)
// are verbatim source text: render them as inert text only (SECURITY T-21).

export type CatalogPart = {
  name: string;
  label: string | null;
  title: string | null;
  prose: string | null;
  parts: CatalogPart[];
};

export type CatalogParam = {
  source_id: string;
  label: string | null;
  guidelines: string[];
  select_how_many: string | null;
  choices: string[] | null;
};

export type LinkTarget = {
  kind: "control" | "enhancement" | "part" | "family" | "resource" | "external" | "unresolved";
  id: string | null;
  control_id: string | null;
  part_label: string | null;
  identifier: string | null;
  title: string | null;
  citation: string | null;
  href: string | null;
};

export type CatalogLink = { rel: string | null; href: string; target: LinkTarget };

export type FamilyRef = { id: string; identifier: string; name: string };

export type Family = FamilyRef & { control_count: number; parts: CatalogPart[] };

export type BaselineSummary = {
  id: string;
  identifier: string;
  title: string;
  version: string | null;
  member_count: number;
  source_file_name: string;
  source_sha256: string;
  source_file_url: string | null;
};

export type CatalogRelease = {
  id: string;
  framework: FrameworkRef;
  version: string;
  title: string;
  is_current: boolean;
  imported_at: string;
  source: {
    format: string;
    format_version: string | null;
    last_modified: string | null;
    file_name: string;
    sha256: string;
    repository_url: string | null;
    release_url: string | null;
    file_url: string | null;
    tag: string | null;
    commit: string | null;
  };
  license_notice: string;
  attribution: string;
  control_count: number;
  enhancement_count: number;
  ungrouped_control_count: number;
  families: Family[];
  baselines: BaselineSummary[];
};

export type ControlSummary = {
  id: string;
  identifier: string;
  title: string;
  status: string | null;
  family_id: string | null;
  enhancement_count: number;
  baselines: string[];
};

export type ControlList = { controls: ControlSummary[]; total: number; limit: number; offset: number };

export type ControlQuery = {
  familyId: string | null;
  baselineId: string | null;
  search: string | null;
  limit: number;
  offset: number;
};

export type Enhancement = {
  id: string;
  identifier: string;
  title: string;
  status: string | null;
  baselines: string[];
  parts: CatalogPart[];
  params: CatalogParam[];
  links: CatalogLink[];
};

export type ControlDetail = {
  id: string;
  identifier: string;
  title: string;
  status: string | null;
  release: {
    id: string;
    version: string;
    title: string;
    is_current: boolean;
    attribution: string;
    framework: FrameworkRef;
  };
  family: FamilyRef | null;
  baselines: string[];
  parts: CatalogPart[];
  params: CatalogParam[];
  links: CatalogLink[];
  enhancements: Enhancement[];
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
