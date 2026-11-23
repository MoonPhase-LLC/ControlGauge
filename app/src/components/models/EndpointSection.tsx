import { useId, useState, type FormEvent } from "react";
import { Server } from "lucide-react";
import { ErrorNotice, formatDateTime } from "@/components/screen";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import {
  attachEndpoint,
  checkEndpointHealth,
  deleteEndpoint,
  listEndpoints,
  registerEndpoint,
  stopModel,
} from "@/service/commands";
import type { Endpoint, ModelInventory, ModelRole } from "@/service/types";
import { useServiceQuery } from "@/service/useServiceQuery";

const BACKENDS = ["llama-cpp"] as const;

const ROLE_LABELS: Record<ModelRole, string> = {
  generation: "generation",
  embedding: "embedding",
};

/**
 * Fixed security note (frontend text, never service-returned): V0.1 registers
 * unauthenticated loopback endpoints only -- no credential field exists
 * anywhere in the flow.
 */
export const ENDPOINT_SECURITY_NOTE =
  "Register only servers running on this computer. Endpoint addresses must be local (127.0.0.1 or localhost); no credentials are stored, and model traffic never leaves this machine.";

/** Client-side mirror of the service's loopback rule (fail closed, fixed text). */
export function isLoopbackUrl(value: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return false;
  }
  if (parsed.protocol !== "http:") return false;
  return parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost" || parsed.hostname === "[::1]";
}

/** Health badge tone: informative colors for the known resolved states. */
function HealthBadge({ endpoint }: { endpoint: Endpoint }) {
  const state = endpoint.last_health_state;
  const tone =
    state === "healthy"
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200"
      : state === "unreachable"
        ? "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-200"
        : state === "unhealthy"
          ? "border-destructive/30 bg-destructive/5 text-destructive"
          : "border-transparent bg-muted text-muted-foreground";
  const label = state ?? "Not checked";
  const title =
    state === null
      ? "This endpoint has not been checked yet"
      : `Last health check: ${state}${endpoint.last_health_code !== null ? ` (${endpoint.last_health_code})` : ""}`;
  return (
    <span title={title} className={`shrink-0 rounded-full border px-2.5 py-0.5 text-xs font-medium ${tone}`}>
      {label}
    </span>
  );
}

type FormErrors = { displayName?: string; baseUrl?: string; capabilities?: string };

/**
 * Register, inspect, and use external OpenAI-compatible endpoints on
 * localhost (S6-05/S6-07). Registration probes every declared capability
 * before a row exists; attaching makes the endpoint a role's occupant
 * (stop-then-attach switch), and Stop detaches it via the role's stop route.
 */
export function EndpointSection({
  enabled,
  runtime,
  onRuntimeChanged,
}: {
  enabled: boolean;
  runtime: ModelInventory["runtime"] | undefined;
  onRuntimeChanged: () => void;
}) {
  const endpoints = useServiceQuery(listEndpoints, "endpoints", enabled);
  const nameId = useId();
  const urlId = useId();
  const backendId = useId();
  const [displayName, setDisplayName] = useState("");
  const [backend, setBackend] = useState<string>(BACKENDS[0]);
  const [baseUrl, setBaseUrl] = useState("");
  const [capabilities, setCapabilities] = useState<Record<ModelRole, boolean>>({
    generation: true,
    embedding: false,
  });
  const [formErrors, setFormErrors] = useState<FormErrors>({});
  const [registering, setRegistering] = useState(false);
  const [registerError, setRegisterError] = useState<unknown>(null);
  // One action at a time, scoped to its endpoint.
  const [busy, setBusy] = useState<{ id: string; action: "health" | "attach" | "stop" | "delete"; role?: ModelRole } | null>(
    null,
  );
  const [actionError, setActionError] = useState<{
    id: string;
    action: "health" | "attach" | "stop" | "delete";
    role?: ModelRole;
    error: unknown;
  } | null>(null);

  function validate(): FormErrors {
    const errors: FormErrors = {};
    if (!displayName.trim()) errors.displayName = "Enter a name for the endpoint.";
    if (!baseUrl.trim()) {
      errors.baseUrl = "Enter the endpoint's full address, including the port.";
    } else if (!isLoopbackUrl(baseUrl.trim())) {
      errors.baseUrl = "Endpoint addresses must be local (127.0.0.1 or localhost), for example http://127.0.0.1:8080.";
    }
    if (!capabilities.generation && !capabilities.embedding) {
      errors.capabilities = "Choose at least one capability.";
    }
    return errors;
  }

  async function register() {
    if (registering) return;
    const errors = validate();
    setFormErrors(errors);
    if (Object.keys(errors).length > 0) return;
    const caps = (Object.keys(capabilities) as ModelRole[]).filter((r) => capabilities[r]);
    setRegistering(true);
    setRegisterError(null);
    try {
      await registerEndpoint({
        displayName: displayName.trim(),
        backend,
        baseUrl: baseUrl.trim(),
        capabilities: caps.join(","),
      });
      setDisplayName("");
      setBaseUrl("");
      setCapabilities({ generation: true, embedding: false });
      endpoints.reload();
    } catch (err) {
      setRegisterError(err);
    } finally {
      setRegistering(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await register();
  }

  async function runAction(id: string, action: "health" | "attach" | "stop" | "delete", role?: ModelRole) {
    if (busy) return;
    setBusy({ id, action, role });
    setActionError(null);
    try {
      if (action === "health") {
        await checkEndpointHealth(id);
        endpoints.reload();
      } else if (action === "attach" && role) {
        await attachEndpoint(id, role);
        onRuntimeChanged();
      } else if (action === "stop") {
        await stopModel(id);
        onRuntimeChanged();
      } else if (action === "delete") {
        await deleteEndpoint(id);
        endpoints.reload();
      }
    } catch (err) {
      setActionError({ id, action, role, error: err });
    } finally {
      setBusy(null);
    }
  }

  const list = endpoints.data ?? [];

  return (
    <Card>
      <CardHeader className="justify-items-start gap-3">
        <Server aria-hidden="true" className="size-6 text-muted-foreground" />
        <CardTitle>External endpoints</CardTitle>
        <CardDescription className="max-w-prose">
          Use a model server already running on this computer instead of a downloaded model file.
          The server must speak the OpenAI-compatible API on a local address.
        </CardDescription>

        <p className="max-w-prose text-sm text-muted-foreground">{ENDPOINT_SECURITY_NOTE}</p>

        <form onSubmit={submit} className="flex flex-col gap-3" noValidate>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={nameId}>Display name</Label>
              <Input
                id={nameId}
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="My local server"
                aria-invalid={formErrors.displayName !== undefined || undefined}
                required
              />
              {formErrors.displayName && <p className="text-sm text-destructive">{formErrors.displayName}</p>}
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={backendId}>Backend</Label>
              <NativeSelect id={backendId} value={backend} onChange={(e) => setBackend(e.target.value)}>
                {BACKENDS.map((b) => (
                  <NativeSelectOption key={b} value={b}>
                    {b}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={urlId}>Base address</Label>
            <Input
              id={urlId}
              type="url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://127.0.0.1:8080"
              aria-invalid={formErrors.baseUrl !== undefined || undefined}
              aria-describedby={`${urlId}-helper`}
              required
            />
            <p id={`${urlId}-helper`} className="text-sm text-muted-foreground">
              Must be a 127.0.0.1/localhost address, for example http://127.0.0.1:8080.
            </p>
            {formErrors.baseUrl && <p className="text-sm text-destructive">{formErrors.baseUrl}</p>}
          </div>
          <fieldset className="flex flex-col gap-1.5">
            <legend className="text-sm font-medium">Capabilities</legend>
            <div className="flex flex-wrap gap-4">
              {(Object.keys(ROLE_LABELS) as ModelRole[]).map((role) => (
                <label key={role} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={capabilities[role]}
                    onChange={(e) => setCapabilities((prev) => ({ ...prev, [role]: e.target.checked }))}
                  />
                  {ROLE_LABELS[role]}
                </label>
              ))}
            </div>
            {formErrors.capabilities && <p className="text-sm text-destructive">{formErrors.capabilities}</p>}
          </fieldset>
          {registerError !== null && (
            <ErrorNotice title="The endpoint could not be registered" error={registerError} onRetry={register} />
          )}
          <div>
            <Button type="submit" aria-disabled={registering || undefined}>
              {registering ? "Checking and registering…" : "Register endpoint"}
            </Button>
          </div>
        </form>

        {endpoints.error !== null && list.length === 0 && (
          <ErrorNotice title="Endpoints could not be loaded" error={endpoints.error} onRetry={endpoints.reload} />
        )}

        {list.length > 0 && (
          <ul aria-label="Registered endpoints" className="flex flex-col gap-2">
            {list.map((endpoint) => {
              const rowBusy = busy?.id === endpoint.id;
              const rowError = actionError?.id === endpoint.id ? actionError : null;
              const caps = endpoint.capabilities.split(",").filter((c) => c !== "");
              const occupantRoles = (Object.keys(ROLE_LABELS) as ModelRole[]).filter(
                (role) => runtime?.[role]?.model_id === endpoint.id,
              );
              return (
                <li key={endpoint.id} className="flex flex-col gap-2 rounded-xl bg-card p-4 shadow-xs ring-1 ring-foreground/10">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="flex min-w-0 flex-col gap-0.5">
                      <span className="truncate font-medium">{endpoint.display_name}</span>
                      <span className="text-sm text-muted-foreground">
                        {endpoint.backend} · <span className="font-mono text-xs">{endpoint.base_url_host_only}</span> ·{" "}
                        {caps.join(" + ")} · Registered {formatDateTime(endpoint.registered_at)}
                      </span>
                    </div>
                    <HealthBadge endpoint={endpoint} />
                  </div>
                  {rowError && (
                    <ErrorNotice
                      title="The endpoint action could not be completed"
                      error={rowError.error}
                      onRetry={() => runAction(endpoint.id, rowError.action, rowError.role)}
                    />
                  )}
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => runAction(endpoint.id, "health")}
                      aria-disabled={(rowBusy && busy?.action === "health") || undefined}
                    >
                      {rowBusy && busy?.action === "health" ? "Checking…" : "Check health"}
                    </Button>
                    {occupantRoles.length === 0
                      ? caps.map((role) =>
                          role === "generation" || role === "embedding" ? (
                            <Button
                              key={role}
                              type="button"
                              variant="outline"
                              onClick={() => runAction(endpoint.id, "attach", role)}
                              aria-disabled={(rowBusy && busy?.action === "attach") || undefined}
                            >
                              Use for {ROLE_LABELS[role]}
                            </Button>
                          ) : null,
                        )
                      : occupantRoles.map((role) => (
                          <Button
                            key={role}
                            type="button"
                            variant="outline"
                            onClick={() => runAction(endpoint.id, "stop")}
                            aria-disabled={(rowBusy && busy?.action === "stop") || undefined}
                          >
                            {rowBusy && busy?.action === "stop" ? "Stopping…" : `Stop ${ROLE_LABELS[role]}`}
                          </Button>
                        ))}
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => runAction(endpoint.id, "delete")}
                      aria-disabled={(rowBusy && busy?.action === "delete") || undefined}
                    >
                      Remove
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardHeader>
    </Card>
  );
}
