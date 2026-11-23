import { useId, useState, type FormEvent } from "react";
import { Link2 } from "lucide-react";
import { ErrorNotice } from "@/components/screen";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { importModelUrl } from "@/service/commands";
import type { ModelRole } from "@/service/types";
import { DownloadProgress } from "./DownloadProgress";

/** Fixed provenance disclosure for direct-URL downloads (frontend text). */
export const URL_IMPORT_WARNING =
  "A direct download is not verified by the app's catalog. Provide the publisher's expected SHA-256 checksum to have it enforced, or acknowledge that the source is not verified.";

const CHECKSUM_PATTERN = /^[0-9a-fA-F]{64}$/;

/** Client-side mirror of the service's URL rule: https, or loopback http. */
export function isSupportedImportUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    if (parsed.protocol === "https:") return true;
    if (parsed.protocol === "http:") {
      return parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost" || parsed.hostname === "[::1]";
    }
    return false;
  } catch {
    return false;
  }
}

type FormErrors = { url?: string; expectedSha256?: string };

/**
 * Direct-URL model import (PD-026): the user supplies the URL and role; the
 * download becomes a registered model only after its checksum is verified.
 * Without an expected checksum the explicit "not verified" acknowledgment is
 * required to submit -- mirroring the service's fail-closed contract.
 */
export function UrlImportSection({ onChanged }: { onChanged?: () => void }) {
  const urlId = useId();
  const roleId = useId();
  const checksumId = useId();
  const ackId = useId();
  const [url, setUrl] = useState("");
  const [role, setRole] = useState<ModelRole>("generation");
  const [expectedSha256, setExpectedSha256] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [formErrors, setFormErrors] = useState<FormErrors>({});
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<unknown>(null);
  const [downloadId, setDownloadId] = useState<string | null>(null);

  const checksumProvided = expectedSha256.trim() !== "";
  const canSubmit = url.trim() !== "" && (checksumProvided || acknowledged);

  async function start() {
    if (starting) return;
    const errors: FormErrors = {};
    if (!isSupportedImportUrl(url.trim())) {
      errors.url = "Provide an https URL.";
    }
    if (checksumProvided && !CHECKSUM_PATTERN.test(expectedSha256.trim())) {
      errors.expectedSha256 = "Enter the 64-character SHA-256 checksum.";
    }
    setFormErrors(errors);
    if (Object.keys(errors).length > 0) return;
    setStarting(true);
    setStartError(null);
    try {
      const started = await importModelUrl({
        url: url.trim(),
        role,
        expectedSha256: checksumProvided ? expectedSha256.trim() : undefined,
        acknowledgeUnverified: !checksumProvided,
      });
      setDownloadId(started.download_id);
    } catch (err) {
      setStartError(err);
    } finally {
      setStarting(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await start();
  }

  return (
    <Card>
      <CardHeader className="justify-items-start gap-3">
        <Link2 aria-hidden="true" className="size-6 text-muted-foreground" />
        <CardTitle>Import from a URL</CardTitle>
        <CardDescription className="max-w-prose">
          Download a GGUF file directly from an address you trust. The file is checksum-checked
          before it is registered; without an expected checksum you must acknowledge the source is
          not verified.
        </CardDescription>
        {downloadId !== null ? (
          <div className="flex flex-col gap-2 rounded-xl bg-muted p-4">
            <p className="text-sm font-medium">Downloading from the address you provided…</p>
            <DownloadProgress
              downloadId={downloadId}
              onCompleted={onChanged}
              onDismiss={() => setDownloadId(null)}
              onRetry={() => setDownloadId(null)}
            />
          </div>
        ) : (
          <form onSubmit={submit} className="flex flex-col gap-3" noValidate>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={urlId}>URL</Label>
              <Input
                id={urlId}
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://example.org/models/model.gguf"
                aria-invalid={formErrors.url !== undefined || undefined}
                required
              />
              {formErrors.url && <p className="text-sm text-destructive">{formErrors.url}</p>}
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={roleId}>Role</Label>
              <NativeSelect
                id={roleId}
                className="min-w-44"
                value={role}
                onChange={(e) => setRole(e.target.value as ModelRole)}
              >
                <NativeSelectOption value="generation">Generation (text)</NativeSelectOption>
                <NativeSelectOption value="embedding">Embedding (vectors)</NativeSelectOption>
              </NativeSelect>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={checksumId}>Expected SHA-256 checksum (optional)</Label>
              <Input
                id={checksumId}
                value={expectedSha256}
                onChange={(e) => setExpectedSha256(e.target.value)}
                placeholder="64 hexadecimal characters"
                aria-invalid={formErrors.expectedSha256 !== undefined || undefined}
                className="font-mono"
              />
              {formErrors.expectedSha256 && (
                <p className="text-sm text-destructive">{formErrors.expectedSha256}</p>
              )}
            </div>
            {!checksumProvided && (
              <div className="flex flex-col gap-1">
                <label htmlFor={ackId} className="flex items-start gap-2 text-sm">
                  <input
                    id={ackId}
                    type="checkbox"
                    checked={acknowledged}
                    onChange={(e) => setAcknowledged(e.target.checked)}
                    className="mt-0.5"
                  />
                  This source is not verified
                </label>
                <p className="text-sm text-muted-foreground">{URL_IMPORT_WARNING}</p>
              </div>
            )}
            {startError !== null && (
              <ErrorNotice title="The download could not be started" error={startError} onRetry={start} />
            )}
            <div>
              <Button type="submit" aria-disabled={!canSubmit || starting || undefined}>
                {starting ? "Starting…" : "Download"}
              </Button>
            </div>
          </form>
        )}
      </CardHeader>
    </Card>
  );
}
