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
  current_release: null,
};
const assessments: Assessment[] = [];

// S6-07: a minimal two-entry catalog (one fast, one embedding) with its
// recommendation report. Synthetic ids/names only -- the app source bans
// real model names (model-agnostic source guard).
const CATALOG = {
  catalog_version: "2026.11.smoke",
  entries: [
    {
      model_id: "catalog-gen-small",
      display_name: "Smoke Gen Small",
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
      files: [{ filename: "smoke-gen-small.gguf", size_bytes: 1_117_320_736, sha256: "1".repeat(64) }],
    },
    {
      model_id: "catalog-embed-default",
      display_name: "Smoke Embed Default",
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
      installed: false,
      installed_model_ids: [],
      evaluation_status: "evaluated",
      files: [{ filename: "smoke-embed-default.gguf", size_bytes: 84_106_624, sha256: "6".repeat(64) }],
    },
  ],
};
const RECOMMENDATIONS = {
  hardware_source: "detected",
  entries: CATALOG.entries.map((entry) => ({
    model_id: entry.model_id,
    role: entry.roles[0],
    verdict: "recommended",
    load_mode: entry.roles[0] === "generation" ? "simultaneous" : null,
    estimated: {
      weights_bytes: entry.total_bytes,
      kv_cache_bytes: 0,
      runtime_overhead_bytes: 0,
      total_bytes: entry.total_bytes,
    },
    reasons: [],
  })),
  pairs: [],
};
const HARDWARE = {
  os_name: "windows",
  os_version: "11",
  cpu_model: "Smoke CPU",
  cpu_cores_logical: 8,
  ram_total_bytes: 32 * 1024 ** 3,
  ram_available_bytes: 16 * 1024 ** 3,
  gpus: [],
  model_dir_free_bytes: 512 * 1024 ** 3,
  detection_complete: true,
  detected_at: "2026-11-02T12:00:00Z",
};
const downloads: Array<{
  download_id: string;
  state: string;
  files: Array<{ filename: string; bytes_downloaded: number; bytes_total: number }>;
  error_code: string | null;
}> = [];

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
    case "list_models":
      // Sprint 5: the smoke test only visits the empty models screen;
      // import/start/stop flows are covered by the component tests.
      return { models: [], runtime: { generation: null, embedding: null } };
    case "get_hardware":
    case "refresh_hardware":
      return { ...HARDWARE, gpus: [...HARDWARE.gpus] };
    case "list_endpoints":
      return { endpoints: [] };
    case "get_model_catalog":
      return {
        catalog_version: CATALOG.catalog_version,
        entries: CATALOG.entries.map((e) => ({ ...e, roles: [...e.roles], files: e.files.map((f) => ({ ...f })) })),
      };
    case "get_recommendations":
      return { ...RECOMMENDATIONS, entries: [...RECOMMENDATIONS.entries], pairs: [] };
    case "start_catalog_download": {
      const entry = CATALOG.entries.find((e) => e.model_id === args.modelId);
      if (!entry) throw { kind: "api_error", status: 404, code: "catalog_entry_unknown", message: "The catalog entry was not found.", fields: [] };
      const record = {
        download_id: "b".repeat(31) + String(downloads.length + 1),
        state: "downloading",
        files: [{ filename: entry.files[0].filename, bytes_downloaded: 0, bytes_total: entry.files[0].size_bytes }],
        error_code: null,
      };
      downloads.unshift(record);
      return { download_id: record.download_id };
    }
    case "get_download_status": {
      const found = downloads.find((d) => d.download_id === args.downloadId);
      if (!found) throw { kind: "api_error", status: 404, code: "download_not_found", message: "The download was not found.", fields: [] };
      return { ...found, files: [...found.files] };
    }
  }
  throw new Error(`unexpected IPC command: ${cmd}`);
});

await import("../src/main.tsx");
