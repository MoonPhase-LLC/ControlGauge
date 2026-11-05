import { invoke } from "@tauri-apps/api/core";
import type {
  Artifact,
  ArtifactList,
  Assessment,
  AssessmentList,
  CatalogRelease,
  ControlDetail,
  ControlList,
  ControlQuery,
  FrameworkSummary,
  HealthResponse,
  NewAssessment,
  SectionList,
  SupervisorState,
  UploadResult,
} from "./types";

// Typed wrappers around the Tauri commands the frontend uses (registered in
// `app/src-tauri/src/lib.rs`). None returns, nor accepts, any connection
// detail: every authenticated request happens entirely in Rust. Data
// commands reject with a `ServiceCallError` object (see `types.ts`).

/** Read-only snapshot of the supervisor state. Safe to call at any time. */
export function getServiceStatus(): Promise<SupervisorState> {
  return invoke<SupervisorState>("get_service_status");
}

/**
 * Performs one authenticated `GET /health` round trip in Rust. Rejects with
 * a fixed, non-secret error code string (e.g. `service_not_ready`).
 */
export function checkServiceHealth(): Promise<HealthResponse> {
  return invoke<HealthResponse>("check_service_health");
}

export async function listFrameworks(): Promise<FrameworkSummary[]> {
  const result = await invoke<{ frameworks: FrameworkSummary[] }>("list_frameworks");
  return result.frameworks;
}

export function listAssessments(limit: number, offset: number): Promise<AssessmentList> {
  return invoke<AssessmentList>("list_assessments", { limit, offset });
}

export function getAssessment(id: string): Promise<Assessment> {
  return invoke<Assessment>("get_assessment", { id });
}

export function createAssessment(input: NewAssessment): Promise<Assessment> {
  return invoke<Assessment>("create_assessment", { input });
}

export function getCatalogRelease(id: string): Promise<CatalogRelease> {
  return invoke<CatalogRelease>("get_catalog_release", { id });
}

export function listCatalogControls(releaseId: string, query: ControlQuery): Promise<ControlList> {
  return invoke<ControlList>("list_catalog_controls", { releaseId, query });
}

export function getControl(id: string): Promise<ControlDetail> {
  return invoke<ControlDetail>("get_control", { id });
}

// --- Sprint 4 evidence ---------------------------------------------------------

/**
 * Sends one user-selected file to Rust as a raw IPC body (never a path). The
 * display name travels percent-encoded in an IPC header; Rust validates both
 * and the service stores the file by hash and parses it in its sandbox.
 */
export async function uploadEvidence(assessmentId: string, file: File): Promise<UploadResult> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  return invoke<UploadResult>("upload_evidence", bytes, {
    headers: { "x-assessment-id": assessmentId, "x-filename": encodeURIComponent(file.name) },
  });
}

export function listArtifacts(assessmentId: string, limit: number, offset: number): Promise<ArtifactList> {
  return invoke<ArtifactList>("list_artifacts", { assessmentId, limit, offset });
}

export function getArtifact(id: string): Promise<Artifact> {
  return invoke<Artifact>("get_artifact", { id });
}

export function listArtifactSections(id: string, limit: number, offset: number): Promise<SectionList> {
  return invoke<SectionList>("list_artifact_sections", { id, limit, offset });
}
