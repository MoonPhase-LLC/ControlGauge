import type { ApiFieldError, ServiceCallError } from "./types";

const KINDS = new Set([
  "service_not_ready",
  "service_no_longer_alive",
  "invalid_argument",
  "request_failed",
  "response_too_large",
  "response_malformed",
  "unauthorized",
  "unexpected_status",
  "api_error",
]);

/** Narrows an IPC rejection to a `ServiceCallError`, or `null` if it is not one. */
export function asServiceCallError(err: unknown): ServiceCallError | null {
  if (typeof err === "object" && err !== null && "kind" in err) {
    const kind = (err as { kind: unknown }).kind;
    if (typeof kind === "string" && KINDS.has(kind)) return err as ServiceCallError;
  }
  return null;
}

export type DescribedError = {
  /** Plain-language explanation for the user. */
  message: string;
  /** Stable code shown as secondary detail (for support), never sensitive. */
  code: string;
};

/** Plain-language wording for a failed data command. */
export function describeServiceError(err: unknown): DescribedError {
  const e = asServiceCallError(err);
  if (!e) return { message: "Something went wrong. Try again.", code: "unknown_error" };
  switch (e.kind) {
    case "api_error":
      return { message: e.message, code: e.code };
    case "service_not_ready":
      return { message: "The local service is still starting. Try again in a moment.", code: e.kind };
    case "service_no_longer_alive":
    case "request_failed":
      return {
        message: "The local service stopped responding. Restart ControlGauge to reconnect.",
        code: e.kind,
      };
    case "unexpected_status":
      return { message: "The local service could not complete the request.", code: `status_${e.status}` };
    default:
      return { message: "The local service could not complete the request.", code: e.kind };
  }
}

/** Per-field messages from a validation `api_error`, keyed by field name. */
export function fieldErrorsOf(err: unknown): Record<string, ApiFieldError> {
  const e = asServiceCallError(err);
  if (!e || e.kind !== "api_error") return {};
  return Object.fromEntries(e.fields.map((f) => [f.field, f]));
}

export function isNotFound(err: unknown): boolean {
  const e = asServiceCallError(err);
  return e?.kind === "api_error" && e.status === 404;
}
