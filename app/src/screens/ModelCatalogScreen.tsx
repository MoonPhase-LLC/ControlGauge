import { useState } from "react";
import { PackageSearch } from "lucide-react";
import { ErrorNotice, LoadingNotice, ScreenHeading, ServiceNotReady } from "@/components/screen";
import { DownloadDialog } from "@/components/models/DownloadDialog";
import { DownloadProgress } from "@/components/models/DownloadProgress";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { asServiceCallError } from "@/service/errors";
import { getModelCatalog, getRecommendations, startCatalogDownload } from "@/service/commands";
import type { CatalogEntry, EntryVerdict, SupervisorState } from "@/service/types";
import { useServiceQuery } from "@/service/useServiceQuery";
import { formatBytes } from "@/components/models/format";

/**
 * Fixed disclosure (ARCHITECTURE §11): this screen is network-capable and
 * says so. Browsing the catalog is offline (it ships with the app); only the
 * explicit download step fetches over the network.
 */
export const CATALOG_NETWORK_DISCLOSURE =
  "Browsing this catalog works offline — the catalog ships with the app. Downloading a model is the only step that uses the network, and every download asks for your explicit approval first.";

/** Fixed tier labels (S6-07); catalog `CUSTOM` entries are user-managed and live on the Models screen. */
const TIER_SECTIONS: { tier: "fast" | "balanced" | "accurate"; heading: string; blurb: string }[] = [
  { tier: "fast", heading: "Fast", blurb: "Smallest models, quickest answers." },
  { tier: "balanced", heading: "Balanced", blurb: "Middle ground between speed and quality." },
  { tier: "accurate", heading: "Accurate", blurb: "Largest models, highest quality when the hardware allows." },
];

const VERDICT_LABELS: Record<EntryVerdict["verdict"], string> = {
  recommended: "Recommended",
  feasible: "Feasible",
  infeasible: "Not feasible",
  unknown: "Needs hardware info",
};

function VerdictBadge({ verdict }: { verdict: EntryVerdict["verdict"] }) {
  const tone =
    verdict === "recommended"
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200"
      : verdict === "infeasible"
        ? "border-destructive/30 bg-destructive/5 text-destructive"
        : "border-transparent bg-muted text-muted-foreground";
  return (
    <span
      title={
        verdict === "unknown"
          ? "This computer's hardware has not been fully detected yet"
          : `For this computer: ${verdict}`
      }
      className={`shrink-0 rounded-full border px-2.5 py-0.5 text-xs font-medium ${tone}`}
    >
      {VERDICT_LABELS[verdict]}
    </span>
  );
}

function InstalledBadge() {
  return (
    <span className="shrink-0 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-0.5 text-xs font-medium text-emerald-800 dark:text-emerald-200">
      Installed
    </span>
  );
}

/** Short revision display: enough to compare against the manifest, not the full digest. */
function shortRevision(revision: string): string {
  return revision.length > 12 ? `${revision.slice(0, 12)}…` : revision;
}

type ActiveDownload = { entry: CatalogEntry; downloadId: string };

/**
 * The curated model catalog (S6-03 manifest + installed state), grouped by
 * the curator's tiers, with this machine's recommendation badges (S6-06
 * report) and the full download approval flow (S6-04 transfer, S6-07 UI).
 * Catalog browsing is structural/offline; downloading is the one
 * network-capable action and is approval-gated.
 */
export function ModelCatalogScreen({
  status,
  focusOnMount,
}: {
  status: SupervisorState;
  focusOnMount: boolean;
}) {
  const ready = status.state === "ready";
  const catalog = useServiceQuery(getModelCatalog, "model-catalog", ready);
  const recommendations = useServiceQuery(getRecommendations, "recommendations", ready);
  const [approving, setApproving] = useState<CatalogEntry | null>(null);
  const [active, setActive] = useState<ActiveDownload | null>(null);

  const verdicts = new Map<string, EntryVerdict>((recommendations.data?.entries ?? []).map((v) => [v.model_id, v]));
  const recError = asServiceCallError(recommendations.error);
  const recommendationUnavailable = recError?.kind === "api_error" && recError.code === "hardware_unavailable";

  async function confirmDownload(entry: CatalogEntry) {
    const started = await startCatalogDownload(entry.model_id);
    setApproving(null);
    setActive({ entry, downloadId: started.download_id });
  }

  function retryDownload(entry: CatalogEntry) {
    // Re-fires the same approved download; the approval already happened.
    void confirmDownload(entry).catch(() => undefined);
  }

  const entries = catalog.data?.entries ?? [];
  const byTier = (tier: string) => entries.filter((e) => e.tier === tier);
  const embeddingEntries = entries.filter((e) => e.tier === null);

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <ScreenHeading focusOnMount={focusOnMount}>Model catalog</ScreenHeading>
        <p className="max-w-prose text-muted-foreground">
          A small, curated set of open-weight models the app has checked in advance.{" "}
          {CATALOG_NETWORK_DISCLOSURE} Models of the custom tier, or from your own sources, are
          managed on the Models screen.
        </p>
      </header>

      {!ready ? (
        <ServiceNotReady
          status={status}
          waitingDescription="The catalog appears as soon as the local service has started."
          failedDescription="The catalog is served by the local service, which is not running. Restart ControlGauge to try again."
        />
      ) : catalog.loading && !catalog.data ? (
        <LoadingNotice label="Loading the catalog…" />
      ) : catalog.error ? (
        <ErrorNotice title="The catalog could not be loaded" error={catalog.error} onRetry={catalog.reload} />
      ) : (
        <>
          {active !== null && (
            <Card>
              <CardHeader className="justify-items-start gap-3">
                <CardTitle>Downloading {active.entry.display_name}</CardTitle>
                <CardDescription className="max-w-prose">
                  You can keep working while the download runs. The model is registered only after
                  every file&apos;s checksum verified.
                </CardDescription>
                <DownloadProgress
                  downloadId={active.downloadId}
                  onCompleted={catalog.reload}
                  onDismiss={() => setActive(null)}
                  onRetry={() => retryDownload(active.entry)}
                />
              </CardHeader>
            </Card>
          )}

          {approving !== null && (
            <DownloadDialog
              entry={approving}
              onConfirm={() => confirmDownload(approving)}
              onCancel={() => setApproving(null)}
            />
          )}

          {recommendationUnavailable ? (
            <p className="text-sm text-muted-foreground" role="note">
              Recommendations are unavailable because this computer&apos;s hardware has not been
              detected yet. Open the Models screen to detect it.
            </p>
          ) : recommendations.error !== null ? (
            <ErrorNotice
              title="Recommendations could not be loaded"
              error={recommendations.error}
              onRetry={recommendations.reload}
            />
          ) : null}

          {TIER_SECTIONS.map(({ tier, heading, blurb }) => {
            const tierEntries = byTier(tier);
            if (tierEntries.length === 0) return null;
            return (
              <section key={tier} aria-labelledby={`tier-${tier}-heading`} className="flex flex-col gap-3">
                <div className="flex flex-col gap-1">
                  <h2 id={`tier-${tier}-heading`} className="text-lg font-semibold">
                    {heading}
                  </h2>
                  <p className="text-sm text-muted-foreground">{blurb}</p>
                </div>
                <ul aria-labelledby={`tier-${tier}-heading`} className="flex flex-col gap-2">
                  {tierEntries.map((entry) => (
                    <CatalogEntryRow
                      key={entry.model_id}
                      entry={entry}
                      verdict={verdicts.get(entry.model_id)?.verdict ?? "unknown"}
                      onDownload={() => setApproving(entry)}
                    />
                  ))}
                </ul>
              </section>
            );
          })}

          {embeddingEntries.length > 0 && (
            <section aria-labelledby="tier-embedding-heading" className="flex flex-col gap-3">
              <div className="flex flex-col gap-1">
                <h2 id="tier-embedding-heading" className="text-lg font-semibold">
                  Embedding
                </h2>
                <p className="text-sm text-muted-foreground">
                  Models that turn documents into searchable vectors.
                </p>
              </div>
              <ul aria-labelledby="tier-embedding-heading" className="flex flex-col gap-2">
                {embeddingEntries.map((entry) => (
                  <CatalogEntryRow
                    key={entry.model_id}
                    entry={entry}
                    verdict={verdicts.get(entry.model_id)?.verdict ?? "unknown"}
                    onDownload={() => setApproving(entry)}
                  />
                ))}
              </ul>
            </section>
          )}

          {entries.length === 0 && (
            <Card>
              <CardHeader className="justify-items-start gap-3">
                <PackageSearch aria-hidden="true" className="size-6 text-muted-foreground" />
                <CardTitle>The catalog is empty</CardTitle>
                <CardDescription className="max-w-prose">
                  No curated models shipped with this installation. Models can still be imported on
                  the Models screen.
                </CardDescription>
              </CardHeader>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function CatalogEntryRow({
  entry,
  verdict,
  onDownload,
}: {
  entry: CatalogEntry;
  verdict: EntryVerdict["verdict"];
  onDownload: () => void;
}) {
  return (
    <li className="flex flex-col gap-2 rounded-xl bg-card p-4 shadow-xs ring-1 ring-foreground/10">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="truncate font-medium">{entry.display_name}</span>
          <span className="text-sm text-muted-foreground">
            {formatBytes(entry.total_bytes)} · {entry.license} · revision{" "}
            <span className="font-mono text-xs">{shortRevision(entry.revision)}</span>
            {entry.embedding_dimensions !== null && ` · ${entry.embedding_dimensions} dimensions`}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <VerdictBadge verdict={verdict} />
          {entry.installed ? <InstalledBadge /> : null}
        </div>
      </div>
      {entry.evaluation_status === "evaluation_suite_pending" && (
        <p className="text-sm text-amber-700 dark:text-amber-300">
          Quality evaluation for this model is still pending — treat its results with extra care.
        </p>
      )}
      {!entry.installed && (
        <div>
          <Button type="button" variant="outline" onClick={onDownload}>
            Download…
          </Button>
        </div>
      )}
    </li>
  );
}
