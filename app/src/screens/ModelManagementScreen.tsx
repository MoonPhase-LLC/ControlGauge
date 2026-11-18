import { useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { Cpu, TriangleAlert } from "lucide-react";
import { ErrorNotice, LoadingNotice, ScreenHeading, ServiceNotReady, formatDateTime } from "@/components/screen";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { checkModelHealth, importModel, listModels, startModel, stopModel } from "@/service/commands";
import type { ModelFile, ModelInventory, ModelRole, RuntimeStatus, SupervisorState } from "@/service/types";
import { useServiceQuery } from "@/service/useServiceQuery";

/**
 * T-08: every model here was imported from a local file and never vetted by
 * the app, so each entry carries this fixed provenance warning -- visually
 * distinct from the verified catalog downloads a later release adds. Fixed
 * frontend text (not service-returned) so the warning cannot vary with data.
 */
export const PROVENANCE_WARNING =
  "This model was imported from a local file. Its provenance is not verified by the app, and it runs with full local privileges. Verified catalog downloads are coming in a later release.";

const ROLE_LABELS: Record<string, string> = {
  generation: "Generation",
  embedding: "Embedding",
};

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

type ModelAction = "start" | "stop" | "health";

const ACTION_TITLES: Record<ModelAction, string> = {
  start: "The model could not be started",
  stop: "The model could not be stopped",
  health: "The health check could not be run",
};

/**
 * Small opaque-state pill: informative colors only for the two known terminal
 * states. The state comes from the inventory, i.e. it is the role's last
 * known state -- never a live check (Sprint 5 review F1); the explicit
 * "Run health check" action is the live check, so the tooltip says so.
 */
function RuntimeBadge({ state }: { state: string }) {
  const tone =
    state === "ready"
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200"
      : state === "unhealthy"
        ? "border-destructive/30 bg-destructive/5 text-destructive"
        : "border-transparent bg-muted text-muted-foreground";
  return (
    <span
      title={`Last known state: ${state}`}
      className={`shrink-0 rounded-full border px-2.5 py-0.5 text-xs font-medium ${tone}`}
    >
      {state}
    </span>
  );
}

function ProvenanceWarning() {
  return (
    <div
      role="note"
      className="flex gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm"
    >
      <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-amber-600" />
      <p className="text-amber-900 dark:text-amber-200">{PROVENANCE_WARNING}</p>
    </div>
  );
}

/**
 * Import, start, stop, and health-check local GGUF models over the pinned
 * connection. Every action is fully offline (ARCHITECTURE §11): the file is
 * hashed and run by the local service only. The machine-local path of an
 * imported model never travels back to this screen (SECURITY T-10) -- the
 * list is identified by model ids, display names, and hashes.
 */
export function ModelManagementScreen({
  status,
  focusOnMount,
}: {
  status: SupervisorState;
  focusOnMount: boolean;
}) {
  const ready = status.state === "ready";
  const inventory = useServiceQuery(() => listModels(), "models", ready);
  const [role, setRole] = useState<ModelRole>("generation");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<unknown>(null);
  // One action at a time, scoped to its model: buttons elsewhere stay usable.
  const [busy, setBusy] = useState<{ id: string; action: ModelAction } | null>(null);
  const [actionError, setActionError] = useState<{ id: string; action: ModelAction; error: unknown } | null>(null);
  // Health answers are statuses (an `unhealthy` model is a result, not a
  // rejection), kept per model until the next check replaces them.
  const [health, setHealth] = useState<Record<string, RuntimeStatus>>({});

  const models = inventory.data?.models ?? [];
  const runtime = inventory.data?.runtime;

  async function chooseAndImport() {
    if (importing) return;
    // T-14: the path comes only from the native picker -- never typed in,
    // never remembered. Cancelling the dialog is a no-op.
    const selection = await open({
      multiple: false,
      filters: [{ name: "GGUF model", extensions: ["gguf"] }],
    });
    if (selection === null) return;
    const path = Array.isArray(selection) ? selection[0] : selection;
    setImporting(true);
    setImportError(null);
    try {
      // The service hashes and sniff-validates the whole file before
      // answering -- seconds for a multi-GB model, hence the busy state.
      await importModel(path, role);
      inventory.reload();
    } catch (err) {
      setImportError(err);
    } finally {
      setImporting(false);
    }
  }

  async function runAction(id: string, action: ModelAction) {
    if (busy) return;
    setBusy({ id, action });
    setActionError(null);
    try {
      if (action === "start") {
        // Answers only after the load finished -- up to minutes for a 7B
        // model on CPU (D-026: the Rust side carries the wide deadline).
        await startModel(id);
      } else if (action === "stop") {
        await stopModel(id);
      } else {
        const result = await checkModelHealth(id);
        setHealth((prev) => ({ ...prev, [id]: result }));
      }
      // Start/stop change the role's occupant; the inventory is the source
      // of truth for which model a Start/Stop button applies to.
      if (action !== "health") inventory.reload();
    } catch (err) {
      setActionError({ id, action, error: err });
    } finally {
      setBusy(null);
    }
  }

  function actionLabel(action: ModelAction): string {
    const pending = busy?.action === action;
    if (action === "start") return pending ? "Starting…" : "Start";
    if (action === "stop") return pending ? "Stopping…" : "Stop";
    return pending ? "Checking…" : "Run health check";
  }

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <ScreenHeading focusOnMount={focusOnMount}>Models</ScreenHeading>
        <p className="max-w-prose text-muted-foreground">
          Import a local GGUF model file and run it for generation or embedding. Everything on this
          screen happens on this computer; no model file or content ever leaves it. The state badge
          shows a role&apos;s last known state; use a model&apos;s Run health check action to verify
          it right now.
        </p>
      </header>

      {!ready ? (
        <ServiceNotReady
          status={status}
          waitingDescription="Models will appear as soon as the local service has started."
          failedDescription="Models are managed by the local service, which is not running. Restart ControlGauge to try again."
        />
      ) : inventory.loading && !inventory.data ? (
        <LoadingNotice label="Loading models…" />
      ) : inventory.error ? (
        <ErrorNotice title="Models could not be loaded" error={inventory.error} onRetry={inventory.reload} />
      ) : (
        <>
          <Card>
            <CardHeader className="justify-items-start gap-3">
              <CardTitle>Import a model</CardTitle>
              <CardDescription className="max-w-prose">
                Choose a GGUF model file on this computer. The local service validates the file and
                computes its checksum before adding it to the list below, which takes a moment for
                large files.
              </CardDescription>
              <div className="flex flex-wrap items-end gap-3">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="model-role">Role</Label>
                  <NativeSelect
                    id="model-role"
                    className="min-w-44"
                    value={role}
                    onChange={(e) => setRole(e.target.value as ModelRole)}
                  >
                    <NativeSelectOption value="generation">Generation (text)</NativeSelectOption>
                    <NativeSelectOption value="embedding">Embedding (vectors)</NativeSelectOption>
                  </NativeSelect>
                </div>
                <Button type="button" onClick={chooseAndImport} aria-disabled={importing || undefined}>
                  {importing ? "Importing…" : "Choose GGUF file…"}
                </Button>
              </div>
              {importError !== null && (
                <ErrorNotice
                  title="The model could not be imported"
                  error={importError}
                  onRetry={chooseAndImport}
                />
              )}
            </CardHeader>
          </Card>

          {models.length === 0 ? (
            <Card>
              <CardHeader className="justify-items-start gap-3">
                <Cpu aria-hidden="true" className="size-6 text-muted-foreground" />
                <CardTitle>No models imported</CardTitle>
                <CardDescription className="max-w-prose">
                  Import a GGUF model file above to run generation or embedding fully offline.
                </CardDescription>
              </CardHeader>
            </Card>
          ) : (
            <section aria-labelledby="model-list-heading" className="flex flex-col gap-3">
              <h2 id="model-list-heading" className="sr-only">
                Imported models
              </h2>
              <ul aria-labelledby="model-list-heading" className="flex flex-col gap-2">
                {models.map((model) => (
                  <ModelRow
                    key={model.id}
                    model={model}
                    runtime={runtime}
                    health={health[model.id] ?? null}
                    busy={busy}
                    actionError={actionError}
                    onAction={runAction}
                    actionLabel={actionLabel}
                  />
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}

type ModelRowProps = {
  model: ModelFile;
  runtime: ModelInventory["runtime"] | undefined;
  health: RuntimeStatus | null;
  busy: { id: string; action: ModelAction } | null;
  actionError: { id: string; action: ModelAction; error: unknown } | null;
  onAction: (id: string, action: ModelAction) => void;
  actionLabel: (action: ModelAction) => string;
};

/**
 * One imported model. The Start/Stop pair is decided by the role's current
 * occupant (`runtime[role].model_id`, structural): the occupant gets Stop,
 * every other model of the role gets Start (the service performs the
 * supervised switch). The badge is the occupant's last known state.
 */
function ModelRow({
  model,
  runtime,
  health,
  busy,
  actionError,
  onAction,
  actionLabel,
}: ModelRowProps) {
  const occupant = runtime?.[model.role as ModelRole] ?? null;
  const isOccupant = occupant?.model_id === model.id;
  const occupantState = isOccupant ? (occupant?.state ?? "stopped") : "stopped";
  const rowBusy = busy?.id === model.id;
  const rowError = actionError?.id === model.id ? actionError : null;

  return (
    <li className="flex flex-col gap-3 rounded-xl bg-card p-4 shadow-xs ring-1 ring-foreground/10">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="truncate font-medium">{model.display_name}</span>
          <span className="text-sm text-muted-foreground">
            {ROLE_LABELS[model.role] ?? model.role} · {formatBytes(model.size_bytes)} · Imported{" "}
            {formatDateTime(model.imported_at)}
            {model.embedding_dimensions !== null && ` · ${model.embedding_dimensions} dimensions`}
          </span>
        </div>
        <RuntimeBadge state={occupantState} />
      </div>

      <ProvenanceWarning />

      {health && (
        <p className="text-sm text-muted-foreground" role="status">
          Last health check: {health.state}
          {health.detail_code && ` (${health.detail_code})`}
        </p>
      )}

      {rowError && (
        <ErrorNotice
          title={ACTION_TITLES[rowError.action]}
          error={rowError.error}
          onRetry={() => onAction(model.id, rowError.action)}
        />
      )}

      <div className="flex flex-wrap gap-2">
        {isOccupant ? (
          <Button
            type="button"
            variant="outline"
            onClick={() => onAction(model.id, "stop")}
            aria-disabled={(rowBusy && busy?.action === "stop") || undefined}
          >
            {actionLabel("stop")}
          </Button>
        ) : (
          <Button
            type="button"
            onClick={() => onAction(model.id, "start")}
            aria-disabled={(rowBusy && busy?.action === "start") || undefined}
          >
            {actionLabel("start")}
          </Button>
        )}
        <Button
          type="button"
          variant="outline"
          onClick={() => onAction(model.id, "health")}
          aria-disabled={(rowBusy && busy?.action === "health") || undefined}
        >
          {actionLabel("health")}
        </Button>
      </div>
    </li>
  );
}
