import { Fragment, useEffect, useId, useState, type FormEvent } from "react";
import { ChevronRight, Library, Search } from "lucide-react";
import { ErrorNotice, LoadingNotice, ScreenHeading, ServiceNotReady, formatDateTime } from "@/components/screen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { OfficialTextRegion } from "@/components/catalog/OfficialTextRegion";
import { PartSections, indexParams } from "@/components/catalog/OfficialText";
import { getCatalogRelease, listCatalogControls, listFrameworks } from "@/service/commands";
import type { CatalogRelease, ControlSummary, SupervisorState } from "@/service/types";
import { useServiceQuery } from "@/service/useServiceQuery";

export const CONTROL_PAGE_SIZE = 100;
const NO_PARAMS = indexParams();

/** Browsing state kept by `App`, so returning from a control restores it. */
export type CatalogFilters = {
  frameworkId: string | null;
  familyId: string | null;
  baselineId: string | null;
  search: string;
};

export const EMPTY_FILTERS: CatalogFilters = { frameworkId: null, familyId: null, baselineId: null, search: "" };

/**
 * Browse a framework's current catalog release: where it came from, its
 * families and published baselines, and its controls (filter by family or
 * baseline, search by identifier or title).
 */
export function CatalogScreen({
  status,
  focusOnMount,
  filters,
  onFiltersChange,
  onOpenControl,
}: {
  status: SupervisorState;
  focusOnMount: boolean;
  filters: CatalogFilters;
  onFiltersChange: (next: CatalogFilters) => void;
  onOpenControl: (id: string) => void;
}) {
  const ready = status.state === "ready";
  const frameworks = useServiceQuery(listFrameworks, "frameworks", ready);
  const withCatalog = (frameworks.data ?? []).filter((f) => f.current_release !== null);
  const framework = withCatalog.find((f) => f.id === filters.frameworkId) ?? withCatalog[0] ?? null;
  const releaseId = framework?.current_release?.id ?? null;
  const release = useServiceQuery(
    () => getCatalogRelease(releaseId ?? ""),
    `release:${releaseId}`,
    ready && releaseId !== null,
  );
  const selectId = useId();

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <ScreenHeading focusOnMount={focusOnMount}>Framework catalog</ScreenHeading>
        <p className="max-w-prose text-muted-foreground">
          Controls exactly as published by the framework&apos;s source. ControlGauge does not change this
          text.
        </p>
      </header>

      {!ready ? (
        <ServiceNotReady status={status} />
      ) : frameworks.loading && !frameworks.data ? (
        <LoadingNotice label="Loading frameworks…" />
      ) : frameworks.error ? (
        <ErrorNotice title="Frameworks could not be loaded" error={frameworks.error} onRetry={frameworks.reload} />
      ) : framework === null ? (
        <Card>
          <CardHeader className="justify-items-start gap-3">
            <Library aria-hidden="true" className="size-6 text-muted-foreground" />
            <CardTitle>No catalog is loaded</CardTitle>
            <CardDescription className="max-w-prose">
              None of the installed frameworks has an imported control catalog. Check that the
              application was installed completely.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <>
          {withCatalog.length > 1 && (
            <div className="flex max-w-sm flex-col gap-2">
              <Label htmlFor={selectId}>Framework</Label>
              <NativeSelect
                id={selectId}
                className="w-full"
                value={framework.id}
                onChange={(e) => onFiltersChange({ ...EMPTY_FILTERS, frameworkId: e.target.value })}
              >
                {withCatalog.map((f) => (
                  <NativeSelectOption key={f.id} value={f.id}>
                    {f.name} {f.version}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </div>
          )}
          {release.loading && !release.data ? (
            <LoadingNotice label="Loading catalog…" />
          ) : release.error ? (
            <ErrorNotice title="The catalog could not be loaded" error={release.error} onRetry={release.reload} />
          ) : release.data ? (
            <>
              <ReleaseProvenance release={release.data} />
              <ControlBrowser
                key={release.data.id}
                release={release.data}
                filters={filters}
                onFiltersChange={onFiltersChange}
                onOpenControl={onOpenControl}
              />
            </>
          ) : null}
        </>
      )}
    </div>
  );
}

function ReleaseProvenance({ release }: { release: CatalogRelease }) {
  const { source } = release;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>
            {release.framework.name} {release.framework.version} · Release {release.version}
          </h2>
        </CardTitle>
        <CardDescription className="max-w-prose">{release.title}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        <p className="max-w-prose">{release.attribution}</p>
        <p className="text-muted-foreground">
          {release.control_count} controls and {release.enhancement_count} enhancements in{" "}
          {release.families.length} families.
        </p>
        <details className="rounded-lg border border-border/70">
          <summary className="cursor-pointer rounded-lg px-4 py-2.5 font-medium hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground">
            Source and license
          </summary>
          <dl className="grid gap-x-6 gap-y-2 px-4 pb-4 sm:grid-cols-[max-content_1fr]">
            <dt className="text-muted-foreground">Format</dt>
            <dd>
              {source.format}
              {source.format_version && ` (${source.format_version})`}
            </dd>
            {source.tag && (
              <>
                <dt className="text-muted-foreground">Upstream release</dt>
                <dd>{source.tag}</dd>
              </>
            )}
            {source.commit && (
              <>
                <dt className="text-muted-foreground">Upstream commit</dt>
                <dd className="font-mono text-xs break-all">{source.commit}</dd>
              </>
            )}
            {source.release_url && (
              <>
                <dt className="text-muted-foreground">Release page</dt>
                <dd className="font-mono text-xs break-all">{source.release_url}</dd>
              </>
            )}
            <dt className="text-muted-foreground">File</dt>
            <dd className="font-mono text-xs break-all">{source.file_name}</dd>
            <dt className="text-muted-foreground">SHA-256</dt>
            <dd className="font-mono text-xs break-all">{source.sha256}</dd>
            {source.last_modified && (
              <>
                <dt className="text-muted-foreground">Source last modified</dt>
                <dd>{source.last_modified}</dd>
              </>
            )}
            <dt className="text-muted-foreground">Imported</dt>
            <dd>
              <time dateTime={release.imported_at}>{formatDateTime(release.imported_at)}</time>
            </dd>
            <dt className="text-muted-foreground">License</dt>
            <dd className="max-w-prose">{release.license_notice}</dd>
            {release.baselines.map((b) => (
              <Fragment key={b.id}>
                <dt className="text-muted-foreground">Baseline {b.identifier}</dt>
                <dd className="flex flex-col gap-0.5">
                  <span>
                    {b.title} ({b.member_count} controls and enhancements)
                  </span>
                  <span className="font-mono text-xs break-all text-muted-foreground">
                    {b.source_file_name} · SHA-256 {b.source_sha256}
                  </span>
                </dd>
              </Fragment>
            ))}
          </dl>
        </details>
      </CardContent>
    </Card>
  );
}

function ControlBrowser({
  release,
  filters,
  onFiltersChange,
  onOpenControl,
}: {
  release: CatalogRelease;
  filters: CatalogFilters;
  onFiltersChange: (next: CatalogFilters) => void;
  onOpenControl: (id: string) => void;
}) {
  const ids = { family: useId(), baseline: useId(), search: useId() };
  // Filters only apply if they belong to this release.
  const familyId = release.families.some((f) => f.id === filters.familyId) ? filters.familyId : null;
  const baselineId = release.baselines.some((b) => b.id === filters.baselineId) ? filters.baselineId : null;
  const [draft, setDraft] = useState(filters.search);
  const queryKey = JSON.stringify([release.id, familyId, baselineId, filters.search]);
  const query = { familyId, baselineId, search: filters.search.trim() || null };
  const firstPage = useServiceQuery(
    () => listCatalogControls(release.id, { ...query, limit: CONTROL_PAGE_SIZE, offset: 0 }),
    queryKey,
    true,
  );
  const [more, setMore] = useState<ControlSummary[]>([]);
  const [moreError, setMoreError] = useState<unknown>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    setMore([]);
    setMoreError(null);
  }, [firstPage.data]);

  const items = [...(firstPage.data?.controls ?? []), ...more];
  const total = firstPage.data?.total ?? 0;
  const families = new Map(release.families.map((f) => [f.id, f]));
  const selectedFamily = familyId ? families.get(familyId) : undefined;

  async function showMore() {
    if (loadingMore) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await listCatalogControls(release.id, {
        ...query,
        limit: CONTROL_PAGE_SIZE,
        offset: items.length,
      });
      setMore((prev) => [...prev, ...page.controls]);
    } catch (err) {
      setMoreError(err);
    } finally {
      setLoadingMore(false);
    }
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    onFiltersChange({ ...filters, search: draft });
  }

  return (
    <section aria-labelledby="controls-heading" className="flex flex-col gap-4">
      <h2 id="controls-heading" className="text-lg font-semibold">
        Controls
      </h2>
      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.4fr)]">
        <div className="flex flex-col gap-2">
          <Label htmlFor={ids.family}>Family</Label>
          <NativeSelect
            id={ids.family}
            className="w-full"
            value={familyId ?? ""}
            onChange={(e) => onFiltersChange({ ...filters, familyId: e.target.value || null })}
          >
            <NativeSelectOption value="">All families</NativeSelectOption>
            {release.families.map((f) => (
              <NativeSelectOption key={f.id} value={f.id}>
                {f.identifier} · {f.name} ({f.control_count})
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor={ids.baseline}>Published baseline</Label>
          <NativeSelect
            id={ids.baseline}
            className="w-full"
            value={baselineId ?? ""}
            onChange={(e) => onFiltersChange({ ...filters, baselineId: e.target.value || null })}
            aria-describedby={`${ids.baseline}-hint`}
          >
            <NativeSelectOption value="">Any</NativeSelectOption>
            {release.baselines.map((b) => (
              <NativeSelectOption key={b.id} value={b.id}>
                {b.identifier} ({b.member_count})
              </NativeSelectOption>
            ))}
          </NativeSelect>
          <p id={`${ids.baseline}-hint`} className="text-xs text-muted-foreground">
            For browsing only. Assessments are not scoped by baseline.
          </p>
        </div>
        <form role="search" onSubmit={submitSearch} className="flex flex-col gap-2">
          <Label htmlFor={ids.search}>Search identifiers and titles</Label>
          <div className="flex gap-2">
            <Input
              id={ids.search}
              type="search"
              value={draft}
              maxLength={100}
              onChange={(e) => setDraft(e.target.value)}
              className="flex-1"
            />
            <Button type="submit" variant="outline">
              <Search aria-hidden="true" />
              Search
            </Button>
          </div>
        </form>
      </div>

      {selectedFamily && selectedFamily.parts.length > 0 && (
        <OfficialTextRegion source={release.attribution}>
          <PartSections parts={selectedFamily.parts} params={NO_PARAMS} />
        </OfficialTextRegion>
      )}

      {firstPage.loading && !firstPage.data ? (
        <LoadingNotice label="Loading controls…" />
      ) : firstPage.error ? (
        <ErrorNotice title="Controls could not be loaded" error={firstPage.error} onRetry={firstPage.reload} />
      ) : (
        <>
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {total === 0 ? "No controls match." : `Showing ${items.length} of ${total}`}
          </p>
          <ul aria-labelledby="controls-heading" className="flex flex-col gap-2">
            {items.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => onOpenControl(c.id)}
                  className="group flex w-full items-center gap-4 rounded-xl bg-card p-3.5 text-left shadow-xs ring-1 ring-foreground/10 transition-colors hover:bg-muted/60 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground"
                >
                  <span className="w-20 shrink-0 font-mono text-sm font-medium">{c.identifier}</span>{" "}
                  <span className="flex min-w-0 flex-1 flex-col gap-1">
                    <span className={c.status ? "text-muted-foreground" : undefined}>{c.title}</span>
                    <span className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                      {c.status && <StatusBadge status={c.status} />}
                      {c.family_id && families.get(c.family_id) && !familyId && (
                        <span>{families.get(c.family_id)?.name}</span>
                      )}
                      {c.enhancement_count > 0 && (
                        <span>
                          {c.enhancement_count} enhancement{c.enhancement_count === 1 ? "" : "s"}
                        </span>
                      )}
                      {c.baselines.length > 0 && (
                        <span>Baselines: {c.baselines.join(", ")}</span>
                      )}
                    </span>
                  </span>
                  <ChevronRight
                    aria-hidden="true"
                    className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
                  />
                </button>
              </li>
            ))}
          </ul>
          {moreError !== null && <ErrorNotice title="More controls could not be loaded" error={moreError} />}
          {items.length < total && (
            <div>
              <Button type="button" variant="outline" onClick={showMore} aria-disabled={loadingMore || undefined}>
                {loadingMore ? "Loading…" : "Show more"}
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

/** A source lifecycle status (e.g. `withdrawn`), shown as the source states it. */
export function StatusBadge({ status }: { status: string }) {
  return (
    <span className="rounded-sm border border-border px-1.5 py-px text-xs font-medium text-muted-foreground">
      Status in source: {status}
    </span>
  );
}
