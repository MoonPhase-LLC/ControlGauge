import { useEffect, useState } from "react";
import { ErrorNotice } from "@/components/screen";
import { Button } from "@/components/ui/button";
import { getDownloadStatus } from "@/service/commands";
import type { DownloadStatus } from "@/service/types";
import { formatBytes } from "./format";

/** Poll cadence for an in-flight download. */
export const DOWNLOAD_POLL_MS = 750;

/** States that keep the poller running; anything else is terminal. */
const ACTIVE_STATES = new Set<DownloadStatus["state"]>(["pending", "downloading", "verifying"]);

const STATE_LABELS: Record<DownloadStatus["state"], string> = {
  pending: "Starting…",
  downloading: "Downloading…",
  verifying: "Verifying checksums…",
  completed: "Download completed and verified.",
  failed: "The download failed.",
  cancelled: "Download cancelled.",
};

/**
 * Live progress for one started download: polls `getDownloadStatus` on a
 * 750 ms interval while the state is active, stops on the first terminal
 * state, and cleans up on unmount. The poll is structural (in-memory records
 * on the service side); a transport failure stops polling and shows the
 * fixed-code error with a retry. Per-file byte counts only -- V0.1 has no
 * speed or ETA display.
 */
export function DownloadProgress({
  downloadId,
  onCompleted,
  onDismiss,
  onRetry,
}: {
  downloadId: string;
  /** Called once when the download reaches `completed` (e.g. to reload the registry). */
  onCompleted?: () => void;
  /** Removes the progress panel from its parent. */
  onDismiss: () => void;
  /** Starts the same download again after a terminal failure (the parent re-approves nothing: the entry was already approved). */
  onRetry?: () => void;
}) {
  const [status, setStatus] = useState<DownloadStatus | null>(null);
  const [pollError, setPollError] = useState<unknown>(null);
  // Bumped by "retry" to restart polling after a transport failure.
  const [attempt, setAttempt] = useState(0);

  const active = status === null || ACTIVE_STATES.has(status.state);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const interval = setInterval(() => {
      getDownloadStatus(downloadId)
        .then((next) => {
          if (cancelled) return;
          setPollError(null);
          setStatus(next);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          // Stop polling on a transport failure: the panel becomes a
          // fixed-code error the user can retry.
          setPollError(err);
        });
    }, DOWNLOAD_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
    // `downloadId` changes only with a new download; `active` gates the loop.
  }, [downloadId, active, attempt]);

  useEffect(() => {
    if (status?.state === "completed") onCompleted?.();
    // The completion side effect runs once per terminal transition.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.state]);

  if (pollError !== null) {
    return (
      <div className="flex flex-col gap-3">
        <ErrorNotice
          title="The download status could not be read"
          error={pollError}
          onRetry={() => {
            setPollError(null);
            setAttempt((n) => n + 1);
          }}
        />
        <Button type="button" variant="outline" onClick={onDismiss}>
          Close
        </Button>
      </div>
    );
  }

  if (status === null) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Starting…
      </p>
    );
  }

  const failed = status.state === "failed";

  return (
    <div className="flex flex-col gap-3">
      <p
        role="status"
        className={
          failed
            ? "text-sm font-medium text-destructive"
            : "text-sm font-medium"
        }
      >
        {STATE_LABELS[status.state]}
      </p>
      {failed ? (
        <ErrorNotice
          title="The download failed"
          error={{
            kind: "api_error",
            status: 0,
            code: status.error_code ?? "download_failed",
            message: "The files were checked and nothing was registered. The partial data was kept for resuming.",
            fields: [],
          }}
          onRetry={onRetry}
        />
      ) : (
        <ul className="flex flex-col gap-1">
          {status.files.map((file) => (
            <li key={file.filename} className="flex min-w-0 flex-col text-sm">
              <span className="truncate font-mono text-xs">{file.filename}</span>
              <span className="text-muted-foreground">
                {formatBytes(file.bytes_downloaded)}
                {file.bytes_total > 0 && ` of ${formatBytes(file.bytes_total)}`}
              </span>
            </li>
          ))}
        </ul>
      )}
      {!active && (
        <div>
          <Button type="button" variant="outline" onClick={onDismiss}>
            Close
          </Button>
        </div>
      )}
    </div>
  );
}
