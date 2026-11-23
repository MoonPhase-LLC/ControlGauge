import { act } from "@testing-library/react";
import { mockIPC } from "@tauri-apps/api/mocks";
import { vi } from "vitest";
import type {
  ApiFieldError,
  Assessment,
  AttachResult,
  CatalogEntry,
  ControlQuery,
  DownloadStatus,
  Endpoint,
  FrameworkSummary,
  HardwareInfo,
  HealthResponse,
  ModelCatalog,
  ModelFile,
  ModelRole,
  RecommendationReport,
  RuntimeStatus,
  SupervisorState,
} from "@/service/types";
import { CONTROLS, RELEASE, RELEASE_SUMMARY } from "./catalogFixture";

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
  control_count: 3,
  current_release: RELEASE_SUMMARY,
};

/** S6-02 machine facts; served for both hardware commands (never a path). */
export const HARDWARE: HardwareInfo = {
  os_name: "windows",
  os_version: "11",
  cpu_model: "Test CPU",
  cpu_cores_logical: 16,
  ram_total_bytes: 64 * 1024 ** 3,
  ram_available_bytes: 32 * 1024 ** 3,
  gpus: [{ vendor: "nvidia", name: "Test GPU", vram_bytes: 32 * 1024 ** 3 }],
  model_dir_free_bytes: 512 * 1024 ** 3,
  detection_complete: true,
  detected_at: "2026-11-02T12:00:00Z",
};

/**
 * S6-06 recommendation report for the mock hardware: everything fits the
 * 32 GiB GPU comfortably, so the pinned shape is all-recommended with the
 * default pair simultaneous. Pure computation in production — the mock is a
 * fixture, not a reimplementation.
 */
export const RECOMMENDATIONS: RecommendationReport = {
  hardware_source: "detected",
  entries: [
    {
      model_id: "catalog-gen-small",
      role: "generation",
      verdict: "recommended",
      load_mode: "simultaneous",
      estimated: {
        weights_bytes: 1_117_320_736,
        kv_cache_bytes: 234_881_024,
        runtime_overhead_bytes: 644_245_094,
        total_bytes: 1_996_446_854,
      },
      reasons: [],
    },
    {
      model_id: "catalog-gen-medium",
      role: "generation",
      verdict: "recommended",
      load_mode: "simultaneous",
      estimated: {
        weights_bytes: 4_683_073_632,
        kv_cache_bytes: 469_762_048,
        runtime_overhead_bytes: 1_073_741_824,
        total_bytes: 6_226_577_504,
      },
      reasons: [],
    },
    {
      model_id: "catalog-gen-large",
      role: "generation",
      verdict: "recommended",
      load_mode: "simultaneous",
      estimated: {
        weights_bytes: 8_988_110_496,
        kv_cache_bytes: 1_610_612_736,
        runtime_overhead_bytes: 1_610_612_736,
        total_bytes: 12_209_335_968,
      },
      reasons: [],
    },
    {
      model_id: "catalog-embed-default",
      role: "embedding",
      verdict: "recommended",
      load_mode: null,
      estimated: {
        weights_bytes: 84_106_624,
        kv_cache_bytes: 0,
        runtime_overhead_bytes: 429_496_730,
        total_bytes: 513_603_354,
      },
      reasons: [],
    },
  ],
  pairs: [
    {
      generation_model_id: "catalog-gen-small",
      embedding_model_id: "catalog-embed-default",
      verdict: "simultaneous",
      combined_total_bytes: 2_510_050_208,
      reasons: [],
    },
    {
      generation_model_id: "catalog-gen-medium",
      embedding_model_id: "catalog-embed-default",
      verdict: "simultaneous",
      combined_total_bytes: 6_740_180_858,
      reasons: [],
    },
    {
      generation_model_id: "catalog-gen-large",
      embedding_model_id: "catalog-embed-default",
      verdict: "simultaneous",
      combined_total_bytes: 12_722_939_322,
      reasons: [],
    },
  ],
};

/**
 * S6-07 curated catalog fixture. Synthetic ids/urls only (the model-agnostic
 * source guard bans dev-model names in app/src); the ids match the
 * RECOMMENDATIONS fixture so recommendation badges join across screens.
 * The large generation entry mirrors the shipped catalog's honest
 * "evaluation pending" disclosure.
 */
export const CATALOG: ModelCatalog = {
  catalog_version: "2026.11.test",
  entries: [
    {
      model_id: "catalog-gen-small",
      display_name: "Test Gen Small",
      format: "gguf",
      backend: "llama-cpp",
      roles: ["generation"],
      tier: "fast",
      recommended: true,
      recommendation_withheld_reason: null,
      context_window: 32768,
      curated_context_tokens: 8192,
      max_input_tokens: 28672,
      max_output_tokens: 4096,
      total_bytes: 1_117_320_736,
      license: "apache-2.0",
      revision: "a".repeat(64),
      repository_url: "https://models.example.test/catalog-gen-small",
      embedding_dimensions: null,
      installed: false,
      installed_model_ids: [],
      evaluation_status: "evaluated",
      files: [{ filename: "catalog-gen-small-q4_k_m.gguf", size_bytes: 1_117_320_736, sha256: "1".repeat(64) }],
    },
    {
      model_id: "catalog-gen-medium",
      display_name: "Test Gen Medium",
      format: "gguf",
      backend: "llama-cpp",
      roles: ["generation"],
      tier: "balanced",
      recommended: true,
      recommendation_withheld_reason: null,
      context_window: 32768,
      curated_context_tokens: 8192,
      max_input_tokens: 28672,
      max_output_tokens: 4096,
      total_bytes: 4_683_073_632,
      license: "apache-2.0",
      revision: "b".repeat(64),
      repository_url: "https://models.example.test/catalog-gen-medium",
      embedding_dimensions: null,
      installed: false,
      installed_model_ids: [],
      evaluation_status: "evaluated",
      files: [
        { filename: "catalog-gen-medium-q4_k_m-00001-of-00002.gguf", size_bytes: 4_000_000_000, sha256: "2".repeat(64) },
        { filename: "catalog-gen-medium-q4_k_m-00002-of-00002.gguf", size_bytes: 683_073_632, sha256: "3".repeat(64) },
      ],
    },
    {
      model_id: "catalog-gen-large",
      display_name: "Test Gen Large",
      format: "gguf",
      backend: "llama-cpp",
      roles: ["generation"],
      tier: "accurate",
      recommended: true,
      recommendation_withheld_reason: null,
      context_window: 32768,
      curated_context_tokens: 8192,
      max_input_tokens: 28672,
      max_output_tokens: 4096,
      total_bytes: 8_988_110_496,
      license: "apache-2.0",
      revision: "c".repeat(64),
      repository_url: "https://models.example.test/catalog-gen-large",
      embedding_dimensions: null,
      installed: false,
      installed_model_ids: [],
      evaluation_status: "evaluation_suite_pending",
      files: [
        { filename: "catalog-gen-large-q4_k_m-00001-of-00002.gguf", size_bytes: 8_000_000_000, sha256: "4".repeat(64) },
        { filename: "catalog-gen-large-q4_k_m-00002-of-00002.gguf", size_bytes: 988_110_496, sha256: "5".repeat(64) },
      ],
    },
    {
      model_id: "catalog-embed-default",
      display_name: "Test Embed Default",
      format: "gguf",
      backend: "llama-cpp",
      roles: ["embedding"],
      tier: null,
      recommended: true,
      recommendation_withheld_reason: null,
      context_window: 2048,
      curated_context_tokens: 2048,
      max_input_tokens: 2048,
      max_output_tokens: null,
      total_bytes: 84_106_624,
      license: "apache-2.0",
      revision: "d".repeat(64),
      repository_url: "https://models.example.test/catalog-embed-default",
      embedding_dimensions: 768,
      installed: true,
      installed_model_ids: ["00000000-0000-4000-8000-00000000000a"],
      evaluation_status: "evaluated",
      files: [{ filename: "catalog-embed-default-q4_k_m.gguf", size_bytes: 84_106_624, sha256: "6".repeat(64) }],
    },
  ] as CatalogEntry[],
};

/** S6-07 external endpoints: one health-checked, one never checked. */
export const ENDPOINTS: Endpoint[] = [
  {
    id: "00000000-0000-4000-8000-0000000000e1",
    display_name: "Checked endpoint",
    backend: "llama-cpp",
    base_url_host_only: "http://127.0.0.1:8081",
    capabilities: "generation,embedding",
    registered_at: "2026-11-02T12:00:00Z",
    last_health_at: "2026-11-02T12:05:00Z",
    last_health_state: "healthy",
    last_health_code: null,
  },
  {
    id: "00000000-0000-4000-8000-0000000000e2",
    display_name: "Unchecked endpoint",
    backend: "llama-cpp",
    base_url_host_only: "http://localhost:8082",
    capabilities: "embedding",
    registered_at: "2026-11-02T12:01:00Z",
    last_health_at: null,
    last_health_state: null,
    last_health_code: null,
  },
];

const DATA_COMMANDS = [
  "list_frameworks",
  "list_assessments",
  "get_assessment",
  "create_assessment",
  "get_catalog_release",
  "list_catalog_controls",
  "get_control",
  "list_artifacts",
  "import_model",
  "list_models",
  "get_model",
  "start_model",
  "stop_model",
  "check_model_health",
  "get_hardware",
  "refresh_hardware",
  "get_recommendations",
  "get_profile_recommendations",
  "get_model_catalog",
  "list_endpoints",
  "register_endpoint",
  "check_endpoint_health",
  "attach_endpoint",
  "delete_endpoint",
  "start_catalog_download",
  "get_download_status",
  "cancel_download",
  "import_model_url",
] as const;
type DataCommand = (typeof DATA_COMMANDS)[number];

export function apiError(status: number, code: string, message: string, fields: ApiFieldError[] = []) {
  return { kind: "api_error" as const, status, code, message, fields };
}

/**
 * Mocks the Tauri commands the frontend uses. Status is answered
 * immediately from `setStatus`; every health request gets its own deferred
 * promise in `health`, settled explicitly by the test, so async ordering is
 * controlled rather than timed. Data commands are served from an in-memory
 * store (`frameworks`, `assessments`, `models`, newest first) that mirrors
 * the service's documented behavior; `failNext` makes the next call of a
 * command reject with a given `ServiceCallError`, and `hold` keeps a
 * command's calls pending until the test settles them. Any other command is
 * recorded and rejected.
 */
export function mockServiceIpc(initial: SupervisorState = { state: "ready" }) {
  let status = initial;
  const calls = { status: 0, data: [] as { cmd: DataCommand; args: Record<string, unknown> }[] };
  const health: Deferred<HealthResponse>[] = [];
  const unexpected: string[] = [];
  const frameworks: FrameworkSummary[] = [FRAMEWORK];
  const assessments: Assessment[] = [];
  const models: ModelFile[] = [];
  const downloads: DownloadStatus[] = [];
  const endpoints: Endpoint[] = [...ENDPOINTS];
  // Mutable so tests can stage infeasible/unknown verdicts for badge tests.
  let recommendations: RecommendationReport = RECOMMENDATIONS;
  const runtime: Record<ModelRole, RuntimeStatus | null> = { generation: null, embedding: null };
  // One-shot: the next health check for a role's occupant answers
  // `unhealthy` with this detail code (an `unhealthy` answer is a resolved
  // status, mirroring the service).
  let unhealthyHealthNext: string | null = null;
  const failures = new Map<DataCommand, unknown>();
  const held = new Map<DataCommand, Deferred<void>[]>();
  let seq = 0;

  /** Registers a synthetic in-flight download (newest first) and answers
   * the 202 shape. `bytesTotal` 0 mirrors "size not declared up front". */
  function beginDownload(filename: string, bytesTotal: number): { download_id: string } {
    seq++;
    const download: DownloadStatus = {
      download_id: String(seq).padStart(32, "b"),
      state: "downloading",
      files: [{ filename, bytes_downloaded: 0, bytes_total: bytesTotal }],
      error_code: null,
    };
    downloads.unshift(download);
    return { download_id: download.download_id };
  }

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
          catalog_release: framework.current_release,
          created_at: `2026-11-02T12:00:${String(seq).padStart(2, "0")}Z`,
        };
        assessments.unshift(created);
        return created;
      }
      case "get_catalog_release": {
        if (args.id !== RELEASE.id) throw apiError(404, "catalog_release_not_found", "The catalog release was not found.");
        return RELEASE;
      }
      case "list_catalog_controls": {
        if (args.releaseId !== RELEASE.id) {
          throw apiError(404, "catalog_release_not_found", "The catalog release was not found.");
        }
        const q = args.query as ControlQuery;
        const baseline = RELEASE.baselines.find((b) => b.id === q.baselineId);
        const term = q.search?.trim().toLowerCase() ?? "";
        const matching = CONTROLS.filter(
          (c) =>
            (!q.familyId || c.family?.id === q.familyId) &&
            (!baseline ||
              c.baselines.includes(baseline.identifier) ||
              c.enhancements.some((e) => e.baselines.includes(baseline.identifier))) &&
            (!term ||
              [c.identifier, c.title, ...c.enhancements.flatMap((e) => [e.identifier, e.title])].some((v) =>
                v.toLowerCase().includes(term),
              )),
        );
        return {
          controls: matching.slice(q.offset, q.offset + q.limit).map((c) => ({
            id: c.id,
            identifier: c.identifier,
            title: c.title,
            status: c.status,
            family_id: c.family?.id ?? null,
            enhancement_count: c.enhancements.length,
            baselines: c.baselines,
          })),
          total: matching.length,
          limit: q.limit,
          offset: q.offset,
        };
      }
      case "list_artifacts":
        // Sprint 4: evidence has its own panel tests (components/evidence).
        return {
          artifacts: [],
          total: 0,
          total_bytes: 0,
          limit: Number(args.limit),
          offset: Number(args.offset),
          limits: {
            max_file_bytes: 50 * 1024 * 1024,
            max_files_per_batch: 50,
            max_artifacts_per_assessment: 1000,
            max_assessment_bytes: 2 * 1024 * 1024 * 1024,
          },
        };
      case "get_control": {
        const found = CONTROLS.find((c) => c.id === args.id);
        if (!found) throw apiError(404, "control_not_found", "The control was not found.");
        return found;
      }
      // --- Sprint 5 model management (mirrors routes/models.py) -----------
      case "import_model": {
        const path = String(args.path ?? "");
        const role = String(args.role ?? "");
        if (!path.startsWith("/") || !path.endsWith(".gguf")) {
          throw apiError(422, "validation_failed", "The request is not valid.", [
            { field: "path", code: "invalid_path", message: "Provide an absolute path to a .gguf file." },
          ]);
        }
        if (role !== "generation" && role !== "embedding") {
          throw apiError(422, "validation_failed", "The request is not valid.", [
            { field: "role", code: "invalid_role", message: "Choose a known model role." },
          ]);
        }
        seq++;
        const created: ModelFile = {
          id: `00000000-0000-4000-8000-${String(seq).padStart(12, "0")}`,
          role,
          display_name: path.split("/").pop() ?? "model.gguf",
          sha256: "a".repeat(64),
          size_bytes: 3_993_201_344,
          imported_at: `2026-11-02T12:00:${String(seq).padStart(2, "0")}Z`,
          last_verified_at: null,
          embedding_dimensions: null,
          embedding_normalization: null,
          embedding_similarity_metric: null,
          origin: "local_import",
          verification: "none",
        };
        models.unshift(created);
        return { model: created, duplicate: false, provenance: "unverified_import", warning: "unverified_import" };
      }
      case "list_models":
        return { models: [...models], runtime: { ...runtime } };
      case "get_model": {
        const found = models.find((m) => m.id === args.id);
        if (!found) throw apiError(404, "model_not_found", "The model was not found.");
        return found;
      }
      case "start_model": {
        const model = models.find((m) => m.id === args.id);
        if (!model) throw apiError(404, "model_not_found", "The model was not found.");
        // Supervised switch: the role's occupant becomes this model.
        const status: RuntimeStatus = {
          state: "ready",
          detail_code: null,
          checked_at: "2026-11-02T12:05:00Z",
          model_id: model.id,
        };
        runtime[model.role as ModelRole] = status;
        return status;
      }
      case "stop_model": {
        const status: RuntimeStatus = { state: "stopped", detail_code: null, checked_at: null, model_id: null };
        const model = models.find((m) => m.id === args.id);
        if (model) {
          runtime[model.role as ModelRole] = status;
          return status;
        }
        // S6-05: the stop route detaches an attached endpoint too (its
        // registry id is the role's model_id).
        const endpointId = String(args.id ?? "");
        const role = (Object.keys(runtime) as ModelRole[]).find((r) => runtime[r]?.model_id === endpointId);
        if (!role) throw apiError(404, "model_not_found", "The model was not found.");
        runtime[role] = status;
        return status;
      }
      case "check_model_health": {
        const model = models.find((m) => m.id === args.id);
        if (!model) throw apiError(404, "model_not_found", "The model was not found.");
        const occupant = runtime[model.role as ModelRole];
        // A model that is not the role's current occupant reports stopped
        // without being contacted.
        if (occupant?.model_id !== model.id) {
          return { state: "stopped", detail_code: null, checked_at: null, model_id: null };
        }
        if (unhealthyHealthNext !== null) {
          const detail = unhealthyHealthNext;
          unhealthyHealthNext = null;
          return { state: "unhealthy", detail_code: detail, checked_at: "2026-11-02T12:06:00Z", model_id: model.id };
        }
        return { state: "ready", detail_code: null, checked_at: "2026-11-02T12:06:00Z", model_id: model.id };
      }
      // --- Sprint 6 hardware detection (mirrors routes/hardware.py) --------
      // GET = cache-or-detect, POST = always re-detect; the mock collapses
      // both to the same snapshot (call counts still distinguish them).
      case "get_hardware":
      case "refresh_hardware":
        return { ...HARDWARE, gpus: [...HARDWARE.gpus] };
      // --- Sprint 6 recommendations (mirrors routes/recommendations.py) --
      // Pure computation in production; the mock serves the pinned
      // all-recommended fixture. Unknown profile ids 404 like the service.
      case "get_recommendations":
        return { ...recommendations, entries: [...recommendations.entries], pairs: [...recommendations.pairs] };
      case "get_profile_recommendations": {
        const profileId = String(args.profileId ?? "");
        if (profileId === "consumer-cpu") {
          const report: RecommendationReport = {
            hardware_source: "profile:consumer-cpu",
            entries: RECOMMENDATIONS.entries.map((e) =>
              e.model_id === "catalog-gen-large"
                ? { ...e, verdict: "infeasible" as const, load_mode: null, reasons: ["insufficient_ram"] }
                : { ...e }
            ),
            pairs: RECOMMENDATIONS.pairs.map((p) =>
              p.generation_model_id === "catalog-gen-large"
                ? { ...p, verdict: "infeasible" as const, reasons: ["insufficient_ram"] }
                : { ...p }
            ),
          };
          return report;
        }
        if (profileId === "workstation-32gb" || profileId === "enterprise-288gb") {
          return {
            ...RECOMMENDATIONS,
            hardware_source: `profile:${profileId}`,
            entries: [...RECOMMENDATIONS.entries],
            pairs: [...RECOMMENDATIONS.pairs],
          };
        }
        throw apiError(404, "profile_unknown", "The requested recommendation profile does not exist.");
      }
      // --- Sprint 6 catalog + endpoints (mirrors routes/model_catalog.py
      // and routes/model_endpoints.py) --------------------------------------
      case "get_model_catalog":
        return {
          catalog_version: CATALOG.catalog_version,
          entries: CATALOG.entries.map((e) => ({
            ...e,
            roles: [...e.roles],
            installed_model_ids: [...e.installed_model_ids],
            files: e.files.map((f) => ({ ...f })),
          })),
        };
      case "list_endpoints":
        return { endpoints: endpoints.map((e) => ({ ...e })) };
      case "register_endpoint": {
        const baseUrl = String(args.baseUrl ?? "");
        // Service-side mirror of the loopback rule; the screen blocks
        // non-loopback input client-side before this can fire.
        const parsed = new URL(baseUrl);
        if (!["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname)) {
          throw apiError(422, "validation_failed", "The request is not valid.", [
            { field: "base_url", code: "registry_endpoint_not_loopback", message: "Endpoints must be local addresses." },
          ]);
        }
        if (endpoints.some((e) => e.base_url_host_only === parsed.origin)) {
          throw apiError(409, "registry_duplicate", "That endpoint address is already registered.");
        }
        seq++;
        const created: Endpoint = {
          id: `00000000-0000-4000-8000-${String(seq).padStart(12, "0")}`,
          display_name: String(args.displayName ?? "Endpoint"),
          backend: String(args.backend ?? "llama-cpp"),
          base_url_host_only: parsed.origin,
          capabilities: String(args.capabilities ?? "generation"),
          registered_at: "2026-11-02T12:07:00Z",
          last_health_at: null,
          last_health_state: null,
          last_health_code: null,
        };
        endpoints.unshift(created);
        return { ...created };
      }
      case "check_endpoint_health": {
        const found = endpoints.find((e) => e.id === args.endpointId);
        if (!found) throw apiError(404, "endpoint_not_found", "The endpoint was not found.");
        found.last_health_at = "2026-11-02T12:08:00Z";
        found.last_health_state = "healthy";
        found.last_health_code = null;
        return { ...found };
      }
      case "attach_endpoint": {
        const found = endpoints.find((e) => e.id === args.endpointId);
        if (!found) throw apiError(404, "endpoint_not_found", "The endpoint was not found.");
        const role = String(args.role ?? "");
        if (!found.capabilities.split(",").includes(role)) {
          throw apiError(422, "validation_failed", "The request is not valid.", [
            { field: "role", code: "endpoint_capability_missing", message: "The endpoint does not serve that role." },
          ]);
        }
        runtime[role as ModelRole] = {
          state: "ready",
          detail_code: null,
          checked_at: "2026-11-02T12:08:00Z",
          model_id: found.id,
        };
        const result: AttachResult = { role, state: "ready", model_id: found.id };
        return result;
      }
      case "delete_endpoint": {
        const index = endpoints.findIndex((e) => e.id === args.endpointId);
        if (index < 0) throw apiError(404, "endpoint_not_found", "The endpoint was not found.");
        const attached = Object.values(runtime).some((s) => s?.model_id === endpoints[index].id);
        if (attached) throw apiError(409, "endpoint_attached", "Stop the endpoint before removing it.");
        endpoints.splice(index, 1);
        return { deleted: true };
      }
      // --- Sprint 6 secure downloads (mirrors routes/model_catalog.py and
      // routes/models.py import-url) ----------------------------------------
      // Starts register a synthetic `downloading` record; the test drives
      // its progress by mutating the exposed `downloads` store.
      case "start_catalog_download": {
        const modelId = String(args.modelId ?? "");
        const entry = CATALOG.entries.find((e) => e.model_id === modelId);
        if (!entry) {
          throw apiError(404, "catalog_entry_unknown", "The catalog entry was not found.");
        }
        // The service downloads every declared file; the mock tracks the
        // first (fixtures have at most two shards).
        return beginDownload(entry.files[0].filename, entry.files[0].size_bytes);
      }
      case "get_download_status": {
        const found = downloads.find((d) => d.download_id === args.downloadId);
        if (!found) throw apiError(404, "download_not_found", "The download was not found.");
        return { ...found, files: [...found.files] };
      }
      case "cancel_download": {
        const found = downloads.find((d) => d.download_id === args.downloadId);
        if (!found) throw apiError(404, "download_not_found", "The download was not found.");
        if (found.state !== "failed" && found.state !== "cancelled" && found.state !== "completed") {
          found.state = "cancelled";
        }
        return { ...found, files: [...found.files] };
      }
      case "import_model_url": {
        const url = String(args.url ?? "");
        const role = String(args.role ?? "");
        if (!/^https:\/\//.test(url) && !/^http:\/\/(127\.0\.0\.1|localhost|\[::1\])/.test(url)) {
          throw apiError(422, "download_url_unsupported", "The request is not valid.", [
            { field: "url", code: "unsupported_url", message: "Provide an https URL." },
          ]);
        }
        if (role !== "generation" && role !== "embedding") {
          throw apiError(422, "validation_failed", "The request is not valid.", [
            { field: "role", code: "invalid_role", message: "Choose a known model role." },
          ]);
        }
        const hasChecksum = typeof args.expectedSha256 === "string" && args.expectedSha256.length === 64;
        if (!hasChecksum && args.acknowledgeUnverified !== true) {
          throw apiError(422, "expected_checksum_required", "The request is not valid.", [
            {
              field: "expected_sha256",
              code: "expected_checksum_required",
              message: "Provide a checksum or acknowledge the unverified download.",
            },
          ]);
        }
        let filename = "model.gguf";
        try {
          filename = new URL(url).pathname.split("/").pop() || filename;
        } catch {
          // unreachable: the scheme check above already gated the shape
        }
        return beginDownload(filename, 0);
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
    models,
    downloads,
    endpoints,
    /** Replaces the recommendation report served for the detected machine. */
    setRecommendations(report: RecommendationReport) {
      recommendations = report;
    },
    runtime,
    /** Makes the next health check of a role's occupant answer `unhealthy`. */
    setUnhealthyHealthNext(detailCode: string) {
      unhealthyHealthNext = detailCode;
    },
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
