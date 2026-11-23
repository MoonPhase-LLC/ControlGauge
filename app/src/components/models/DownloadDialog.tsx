import { useState } from "react";
import { Download } from "lucide-react";
import { ErrorNotice } from "@/components/screen";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { CatalogEntry } from "@/service/types";
import { formatBytes } from "./format";

/**
 * The approval gate for a catalog download (S6-07): everything the user is
 * approving, shown before anything is fetched -- the source repository URL and
 * revision verbatim from the manifest (inert text, T-21 -- never a link), and
 * the exact per-file names, sizes, and SHA-256 digests the service will
 * enforce. Confirming fires the one POST that IS the approval; cancelling
 * makes no request at all.
 */
export function DownloadDialog({
  entry,
  onConfirm,
  onCancel,
}: {
  entry: CatalogEntry;
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function confirm() {
    if (confirming) return;
    setConfirming(true);
    setError(null);
    try {
      await onConfirm();
    } catch (err) {
      setError(err);
    } finally {
      setConfirming(false);
    }
  }

  return (
    <Card role="region" aria-label={`Review download: ${entry.display_name}`}>
      <CardHeader className="justify-items-start gap-3">
        <Download aria-hidden="true" className="size-6 text-muted-foreground" />
        <CardTitle>Review download: {entry.display_name}</CardTitle>
        <CardDescription className="max-w-prose">
          Downloading fetches these files over the network. This is the only step that uses the
          network; nothing else in ControlGauge does. The files are saved to the app&apos;s model
          directory on this computer and their checksums are verified against the values below
          before the model is registered.
        </CardDescription>
        <dl className="flex min-w-0 flex-col gap-2 text-sm">
          <div className="flex min-w-0 flex-col gap-0.5">
            <dt className="font-medium">Source repository</dt>
            <dd className="min-w-0 break-all font-mono text-xs">{entry.repository_url}</dd>
          </div>
          <div className="flex min-w-0 flex-col gap-0.5">
            <dt className="font-medium">Revision</dt>
            <dd className="min-w-0 break-all font-mono text-xs">{entry.revision}</dd>
          </div>
          <div className="flex min-w-0 flex-col gap-1">
            <dt className="font-medium">Files ({entry.files.length})</dt>
            {entry.files.map((file) => (
              <dd key={file.filename} className="min-w-0 rounded-md bg-muted p-2">
                <span className="block break-all font-mono text-xs">{file.filename}</span>
                <span className="block text-xs text-muted-foreground">
                  {formatBytes(file.size_bytes)} · SHA-256
                </span>
                <span className="block break-all font-mono text-xs">{file.sha256}</span>
              </dd>
            ))}
          </div>
        </dl>
        {error !== null && (
          <ErrorNotice title="The download could not be started" error={error} onRetry={confirm} />
        )}
        <div className="flex flex-wrap gap-2">
          <Button type="button" onClick={confirm} aria-disabled={confirming || undefined}>
            {confirming ? "Starting…" : "Download"}
          </Button>
          <Button type="button" variant="outline" onClick={onCancel} aria-disabled={confirming || undefined}>
            Cancel
          </Button>
        </div>
      </CardHeader>
    </Card>
  );
}
