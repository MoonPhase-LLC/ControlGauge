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
  DownloadStarted,
  DownloadStatus,
  FrameworkSummary,
  HealthResponse,
  HardwareInfo,
  ModelHealthStatus,
  ModelImportResult,
  ModelInventory,
  ModelRole,
  ModelSummary,
  NewAssessment,
  RecommendationReport,
  RuntimeStatus,
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

// --- Sprint 5 model management ---------------------------------------------------

/**
 * Imports a local GGUF file chosen through the native file dialog. The path
 * exists only to be forwarded to Rust and on to the service (which
 * re-validates it, sniff-validates the container, and hashes the file): it is
 * stored server-side and never appears in any response (SECURITY T-10).
 * `role` must be a known `ModelRole`; the service re-validates everything.
 */
export function importModel(path: string, role: ModelRole): Promise<ModelImportResult> {
  return invoke<ModelImportResult>("import_model", { path, role });
}

export function listModels(): Promise<ModelInventory> {
  return invoke<ModelInventory>("list_models");
}

export function getModel(id: string): Promise<ModelSummary> {
  return invoke<ModelSummary>("get_model", { id });
}

/**
 * Starts the model for its recorded role (supervised stop-then-start). The
 * service answers only after the load finished -- up to minutes for a 7B
 * model on CPU, so callers must keep the busy state up throughout.
 */
export function startModel(id: string): Promise<RuntimeStatus> {
  return invoke<RuntimeStatus>("start_model", { id });
}

export function stopModel(id: string): Promise<RuntimeStatus> {
  return invoke<RuntimeStatus>("stop_model", { id });
}

/**
 * One bounded real capability check for the model. An `unhealthy` answer is
 * a resolved status, not a rejection; only transport/service failures reject.
 */
export function checkModelHealth(id: string): Promise<ModelHealthStatus> {
  return invoke<ModelHealthStatus>("check_model_health", { id });
}

// --- Sprint 6 hardware detection ---------------------------------------------------

/**
 * The last detected hardware (structural: served from the service's cache when
 * present). A cold cache triggers one fresh detection, which may run the
 * platform's own bounded local probes; the explicit re-detect is
 * `refreshHardware` -- staleness is never fixed by re-getting.
 */
export function getHardware(): Promise<HardwareInfo> {
  return invoke<HardwareInfo>("get_hardware");
}

/** Explicit re-detection: always runs the probes and rewrites the service cache. */
export function refreshHardware(): Promise<HardwareInfo> {
  return invoke<HardwareInfo>("refresh_hardware");
}

// --- Sprint 6 recommendations (S6-06) -----------------------------------------------

/**
 * The hardware-aware recommendation report for THIS machine (pure
 * computation over the shipped catalog + the service's cached hardware
 * snapshot). Structural: never contacts a model child or the network.
 */
export function getRecommendations(): Promise<RecommendationReport> {
  return invoke<RecommendationReport>("get_recommendations");
}

/**
 * The same math on a synthetic profile (the 32 GB / 288 GB owner
 * targets). Unknown profile ids are a service 404 `profile_unknown`.
 */
export function getProfileRecommendations(profileId: string): Promise<RecommendationReport> {
  return invoke<RecommendationReport>("get_profile_recommendations", { profileId });
}

// --- Sprint 6 secure downloads (S6-04) ----------------------------------------------

/**
 * Starts downloading a curated-catalog entry. This POST IS the user's approval
 * (the UI confirms first); the transfer runs in the service's background and
 * is polled structurally with `getDownloadStatus`.
 */
export function startCatalogDownload(modelId: string): Promise<DownloadStarted> {
  return invoke<DownloadStarted>("start_catalog_download", { modelId });
}

/**
 * Structural poll of a download: state machine + byte counts, never a path
 * (SECURITY T-10). Answers immediately from the service's in-memory records.
 */
export function getDownloadStatus(downloadId: string): Promise<DownloadStatus> {
  return invoke<DownloadStatus>("get_download_status", { downloadId });
}

/**
 * Requests cancellation; the worker stops at the next chunk boundary and the
 * resumable `.part` is retained. Cancelling a terminal download is a no-op
 * returning its status.
 */
export function cancelDownload(downloadId: string): Promise<DownloadStatus> {
  return invoke<DownloadStatus>("cancel_download", { downloadId });
}

/**
 * Starts a direct-URL model download (this POST IS the approval). Either an
 * expected checksum (enforced against the computed digest before anything is
 * registered) or the explicit `acknowledgeUnverified` acknowledgment is
 * required (PD-026); progress is polled with `getDownloadStatus`.
 */
export function importModelUrl(args: {
  url: string;
  role: ModelRole;
  displayName?: string;
  expectedSha256?: string;
  acknowledgeUnverified?: boolean;
}): Promise<DownloadStarted> {
  return invoke<DownloadStarted>("import_model_url", {
    url: args.url,
    role: args.role,
    displayName: args.displayName ?? null,
    expectedSha256: args.expectedSha256 ?? null,
    acknowledgeUnverified: args.acknowledgeUnverified ?? false,
  });
}
