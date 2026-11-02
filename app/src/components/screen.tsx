import { useEffect, useRef, type ReactNode } from "react";
import { AlertCircle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { describeServiceError } from "@/service/errors";
import type { SupervisorState } from "@/service/types";

/**
 * A screen's `h1`. When `focusOnMount` is set (the user navigated here), focus
 * moves to it so keyboard and screen-reader users land at the new content
 * instead of staying on the control that triggered navigation.
 */
export function ScreenHeading({ children, focusOnMount }: { children: ReactNode; focusOnMount: boolean }) {
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (focusOnMount) ref.current?.focus();
  }, [focusOnMount]);
  return (
    <h1 ref={ref} tabIndex={-1} className="text-2xl font-semibold tracking-tight outline-none">
      {children}
    </h1>
  );
}

/** Shown in place of data while the local service is not `ready`. */
export function ServiceNotReady({ status }: { status: SupervisorState }) {
  const failed = status.state === "failed" || status.state === "stopped";
  return (
    <Card>
      <CardHeader>
        <CardTitle>{failed ? "The local service is unavailable" : "Waiting for the local service"}</CardTitle>
        <CardDescription className="max-w-prose">
          {failed
            ? "Assessments are stored by the local service, which is not running. Restart ControlGauge to try again."
            : "Assessments will appear as soon as the local service has started."}
        </CardDescription>
      </CardHeader>
    </Card>
  );
}

export function LoadingNotice({ label }: { label: string }) {
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 aria-hidden="true" className="size-4 animate-spin" />
      {label}
    </p>
  );
}

export function ErrorNotice({ error, title, onRetry }: { error: unknown; title: string; onRetry?: () => void }) {
  const { message, code } = describeServiceError(error);
  return (
    <div
      role="alert"
      className="flex flex-col gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm"
    >
      <div className="flex gap-2">
        <AlertCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-destructive" />
        <div className="flex flex-col gap-1">
          <p className="font-medium">{title}</p>
          <p className="text-muted-foreground">{message}</p>
          <p className="font-mono text-xs text-muted-foreground">{code}</p>
        </div>
      </div>
      {onRetry && (
        <div>
          <Button type="button" variant="outline" size="sm" onClick={onRetry}>
            Try again
          </Button>
        </div>
      )}
    </div>
  );
}

const DATE_TIME = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

/** Local-time display of an ISO 8601 timestamp; falls back to the raw value. */
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : DATE_TIME.format(date);
}
