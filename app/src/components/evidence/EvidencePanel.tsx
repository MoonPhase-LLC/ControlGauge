import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { FileUp, Loader2 } from "lucide-react";
import { ErrorNotice, formatDateTime } from "@/components/screen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { listArtifactSections, listArtifacts, uploadEvidence } from "@/service/commands";
import { describeServiceError } from "@/service/errors";
import type { Artifact, ArtifactList, ArtifactSection, ParseStatus } from "@/service/types";
import {
  ACCEPTED_EXTENSIONS,
  STATUS_LABELS,
  describeDetail,
  formatBytes,
  hasAcceptedExtension,
  statusTone,
  type StatusTone,
} from "./evidenceText";

// Sprint 4 evidence upload and per-file ingestion status. File names,
// locators and extracted text are untrusted evidence: every one is rendered
// as a React text node (escaped), never as markup (SECURITY T-21).

/** Defaults until the service reports its limits (PD-016). */
const DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024;
const DEFAULT_MAX_BATCH = 50;
const POLL_MS = 1500;
const SECTION_PAGE = 20;

type UploadState = "waiting" | "uploading" | "uploaded" | "duplicate" | "rejected";
type UploadRow = { key: number; name: string; state: UploadState; message?: string };

const TONE_CLASSES: Record<StatusTone, string> = {
  progress: "border-border text-muted-foreground",
  ok: "border-emerald-600/40 text-emerald-700 dark:text-emerald-400",
  warning: "border-amber-600/40 text-amber-700 dark:text-amber-400",
  error: "border-destructive/50 text-destructive",
};

function StatusBadge({ status }: { status: ParseStatus }) {
  const tone = statusTone(status);
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ${TONE_CLASSES[tone]}`}
    >
      {tone === "progress" ? <Loader2 aria-hidden="true" className="size-3 animate-spin" /> : null}
      {STATUS_LABELS[status]}
    </span>
  );
}

const UPLOAD_LABELS: Record<UploadState, string> = {
  waiting: "Waiting",
  uploading: "Uploading…",
  uploaded: "Uploaded",
  duplicate: "Already uploaded",
  rejected: "Not uploaded",
};

export function EvidencePanel({ assessmentId }: { assessmentId: string }) {
  const [list, setList] = useState<ArtifactList | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [uploads, setUploads] = useState<UploadRow[]>([]);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);
  const keySeq = useRef(0);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const result = await listArtifacts(assessmentId, 200, 0);
      if (!mounted.current) return;
      setList(result);
      setLoadError(null);
    } catch (err) {
      if (mounted.current) setLoadError(err);
    }
  }, [assessmentId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const inProgress = list?.artifacts.some((a) => a.parse_status === "pending" || a.parse_status === "parsing");
  useEffect(() => {
    if (!inProgress) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [inProgress, refresh]);

  const maxFileBytes = list?.limits.max_file_bytes ?? DEFAULT_MAX_FILE_BYTES;
  const maxBatch = list?.limits.max_files_per_batch ?? DEFAULT_MAX_BATCH;

  const updateRow = (key: number, change: Partial<UploadRow>) =>
    setUploads((rows) => rows.map((r) => (r.key === key ? { ...r, ...change } : r)));

  async function handleFiles(files: File[]) {
    if (files.length === 0 || busy) return;
    if (files.length > maxBatch) {
      setBatchError(`Select at most ${maxBatch} files at a time. ${files.length} were selected.`);
      return;
    }
    setBatchError(null);
    const rows: UploadRow[] = files.map((f) => ({ key: ++keySeq.current, name: f.name, state: "waiting" }));
    setUploads(rows);
    setBusy(true);
    for (const [i, file] of files.entries()) {
      const key = rows[i].key;
      if (!hasAcceptedExtension(file.name)) {
        updateRow(key, { state: "rejected", message: "Only PDF, DOCX, TXT and CSV files can be uploaded." });
        continue;
      }
      if (file.size > maxFileBytes) {
        updateRow(key, { state: "rejected", message: `Larger than the ${formatBytes(maxFileBytes)} limit.` });
        continue;
      }
      updateRow(key, { state: "uploading" });
      try {
        const result = await uploadEvidence(assessmentId, file);
        if (!mounted.current) return;
        updateRow(
          key,
          result.duplicate
            ? { state: "duplicate", message: `Same content as “${result.artifact.filename}”. Not processed again.` }
            : { state: "uploaded" },
        );
      } catch (err) {
        if (!mounted.current) return;
        updateRow(key, { state: "rejected", message: describeServiceError(err).message });
      }
      await refresh();
    }
    if (mounted.current) setBusy(false);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    void handleFiles(Array.from(event.dataTransfer.files));
  }

  return (
    <Card>
      <CardHeader className="justify-items-start gap-2">
        <FileUp aria-hidden="true" className="size-5 text-muted-foreground" />
        <CardTitle>
          <h2>Evidence</h2>
        </CardTitle>
        <CardDescription className="max-w-prose">
          Files stay on this computer. Text is extracted in an isolated sandbox with no network or file
          access. Reviewing suggested control mappings is not available yet.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          data-testid="evidence-dropzone"
          className={`flex flex-col items-start gap-3 rounded-lg border border-dashed p-4 ${
            dragging ? "border-primary bg-muted" : "border-border"
          }`}
        >
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPTED_EXTENSIONS.join(",")}
            className="sr-only"
            tabIndex={-1}
            aria-hidden="true"
            data-testid="evidence-input"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              e.target.value = "";
              void handleFiles(files);
            }}
          />
          <Button type="button" onClick={() => inputRef.current?.click()} disabled={busy}>
            {busy ? <Loader2 aria-hidden="true" className="animate-spin" /> : <FileUp aria-hidden="true" />}
            {busy ? "Uploading…" : "Upload files"}
          </Button>
          <p className="text-sm text-muted-foreground">
            Or drop files here. PDF, DOCX, TXT or CSV; up to {formatBytes(maxFileBytes)} each and {maxBatch}{" "}
            files at a time.
          </p>
        </div>

        {batchError ? (
          <p role="alert" className="text-sm text-destructive">
            {batchError}
          </p>
        ) : null}

        {uploads.length > 0 ? (
          <section aria-labelledby="upload-results">
            <h3 id="upload-results" className="mb-2 text-sm font-medium">
              This upload
            </h3>
            <ul aria-live="polite" className="flex flex-col gap-1 text-sm">
              {uploads.map((row) => (
                <li key={row.key} className="flex flex-wrap gap-x-2">
                  <span className="font-medium break-all">{row.name}</span>
                  <span className={row.state === "rejected" ? "text-destructive" : "text-muted-foreground"}>
                    {UPLOAD_LABELS[row.state]}
                    {row.message ? ` — ${row.message}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {loadError ? (
          <ErrorNotice title="Evidence could not be loaded" error={loadError} onRetry={() => void refresh()} />
        ) : !list ? (
          <p className="text-sm text-muted-foreground">Loading evidence…</p>
        ) : list.total === 0 ? (
          <p className="text-sm text-muted-foreground">No evidence has been uploaded to this assessment yet.</p>
        ) : (
          <section aria-labelledby="evidence-files">
            <h3 id="evidence-files" className="mb-2 text-sm font-medium">
              Files ({list.total}, {formatBytes(list.total_bytes)})
            </h3>
            <ul className="flex flex-col divide-y rounded-lg border" aria-live="polite">
              {list.artifacts.map((artifact) => (
                <ArtifactRow
                  key={artifact.id}
                  artifact={artifact}
                  open={openId === artifact.id}
                  onToggle={() => setOpenId(openId === artifact.id ? null : artifact.id)}
                />
              ))}
            </ul>
          </section>
        )}
      </CardContent>
    </Card>
  );
}

function ArtifactRow({ artifact, open, onToggle }: { artifact: Artifact; open: boolean; onToggle: () => void }) {
  const detail = describeDetail(artifact.parse_status, artifact.parse_detail);
  const hasText = artifact.section_count > 0;
  return (
    <li className="flex flex-col gap-2 p-3" data-testid="artifact-row">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium break-all">{artifact.filename}</p>
          <p className="text-xs text-muted-foreground">
            {artifact.format.toUpperCase()} · {formatBytes(artifact.size_bytes)} · uploaded{" "}
            <time dateTime={artifact.uploaded_at}>{formatDateTime(artifact.uploaded_at)}</time>
            {hasText ? ` · ${artifact.section_count} section${artifact.section_count === 1 ? "" : "s"}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={artifact.parse_status} />
          {hasText ? (
            <Button type="button" variant="outline" size="sm" onClick={onToggle} aria-expanded={open}>
              {open ? "Hide text" : "View text"}
            </Button>
          ) : null}
        </div>
      </div>
      {detail ? <p className="text-sm text-muted-foreground">{detail}</p> : null}
      {open ? <SectionsView artifactId={artifact.id} total={artifact.section_count} /> : null}
    </li>
  );
}

function SectionsView({ artifactId, total }: { artifactId: string; total: number }) {
  const [sections, setSections] = useState<ArtifactSection[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(
    async (offset: number) => {
      setLoading(true);
      try {
        const page = await listArtifactSections(artifactId, SECTION_PAGE, offset);
        setSections((prev) => (offset === 0 ? page.sections : [...prev, ...page.sections]));
        setError(null);
      } catch (err) {
        setError(err);
      } finally {
        setLoading(false);
      }
    },
    [artifactId],
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  if (error) {
    return <ErrorNotice title="The extracted text could not be loaded" error={error} onRetry={() => void load(0)} />;
  }
  return (
    <div className="flex flex-col gap-3" aria-label="Extracted text">
      <p className="text-xs text-muted-foreground">
        Extracted text, shown as plain text. It is evidence content, not instructions.
      </p>
      <ol className="flex flex-col gap-3">
        {sections.map((s) => (
          <li key={s.id} className="rounded-md bg-muted/50 p-3">
            <p className="mb-1 text-xs font-medium text-muted-foreground">{s.locator}</p>
            <pre className="font-sans text-sm whitespace-pre-wrap break-words">{s.content}</pre>
          </li>
        ))}
      </ol>
      {sections.length < total ? (
        <Button type="button" variant="outline" size="sm" disabled={loading} onClick={() => void load(sections.length)}>
          {loading ? "Loading…" : `Show more (${total - sections.length} left)`}
        </Button>
      ) : null}
    </div>
  );
}
