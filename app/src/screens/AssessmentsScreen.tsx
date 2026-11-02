import { useEffect, useState } from "react";
import { ChevronRight, ClipboardList, Plus } from "lucide-react";
import { ErrorNotice, LoadingNotice, ScreenHeading, ServiceNotReady, formatDateTime } from "@/components/screen";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { listAssessments } from "@/service/commands";
import type { Assessment, SupervisorState } from "@/service/types";
import { useServiceQuery } from "@/service/useServiceQuery";

export const PAGE_SIZE = 50;

/** Lists stored assessments, newest first, with paging by "Show more". */
export function AssessmentsScreen({
  status,
  focusOnMount,
  onCreate,
  onOpen,
}: {
  status: SupervisorState;
  focusOnMount: boolean;
  onCreate: () => void;
  onOpen: (id: string) => void;
}) {
  const ready = status.state === "ready";
  const firstPage = useServiceQuery(() => listAssessments(PAGE_SIZE, 0), "first", ready);
  const [more, setMore] = useState<Assessment[]>([]);
  const [moreError, setMoreError] = useState<unknown>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  // A reload of the first page starts the list over.
  useEffect(() => {
    setMore([]);
    setMoreError(null);
  }, [firstPage.data]);

  const items = [...(firstPage.data?.assessments ?? []), ...more];
  const total = firstPage.data?.total ?? 0;

  async function showMore() {
    if (loadingMore) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await listAssessments(PAGE_SIZE, items.length);
      setMore((prev) => [...prev, ...page.assessments]);
    } catch (err) {
      setMoreError(err);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-2">
          <ScreenHeading focusOnMount={focusOnMount}>Assessments</ScreenHeading>
          <p className="max-w-prose text-muted-foreground">
            Each assessment collects evidence against one framework. Assessments are stored only on
            this computer.
          </p>
        </div>
        {ready && items.length > 0 && (
          <Button type="button" onClick={onCreate}>
            <Plus aria-hidden="true" />
            New assessment
          </Button>
        )}
      </header>

      {!ready ? (
        <ServiceNotReady status={status} />
      ) : firstPage.loading && !firstPage.data ? (
        <LoadingNotice label="Loading assessments…" />
      ) : firstPage.error ? (
        <ErrorNotice title="Assessments could not be loaded" error={firstPage.error} onRetry={firstPage.reload} />
      ) : items.length === 0 ? (
        <Card>
          <CardHeader className="justify-items-start gap-3">
            <ClipboardList aria-hidden="true" className="size-6 text-muted-foreground" />
            <CardTitle>No assessments yet</CardTitle>
            <CardDescription className="max-w-prose">
              Create an assessment to start organizing evidence for a framework.
            </CardDescription>
            <Button type="button" onClick={onCreate} className="mt-1">
              <Plus aria-hidden="true" />
              New assessment
            </Button>
          </CardHeader>
        </Card>
      ) : (
        <section aria-labelledby="assessment-list-heading" className="flex flex-col gap-3">
          <h2 id="assessment-list-heading" className="sr-only">
            Assessment list
          </h2>
          <p className="text-sm text-muted-foreground" aria-live="polite">
            Showing {items.length} of {total}
          </p>
          <ul aria-labelledby="assessment-list-heading" className="flex flex-col gap-2">
            {items.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  onClick={() => onOpen(a.id)}
                  className="group flex w-full items-center gap-4 rounded-xl bg-card p-4 text-left shadow-xs ring-1 ring-foreground/10 transition-colors hover:bg-muted/60 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground"
                >
                  <span className="flex min-w-0 flex-1 flex-col gap-1">
                    <span className="truncate font-medium">{a.name}</span>
                    <span className="text-sm text-muted-foreground">
                      {a.framework.name} {a.framework.version} · Created {formatDateTime(a.created_at)}
                    </span>
                    {a.description && (
                      <span className="line-clamp-1 text-sm text-muted-foreground">{a.description}</span>
                    )}
                  </span>
                  <ChevronRight
                    aria-hidden="true"
                    className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
                  />
                </button>
              </li>
            ))}
          </ul>
          {moreError !== null && <ErrorNotice title="More assessments could not be loaded" error={moreError} />}
          {items.length < total && (
            <div>
              <Button
                type="button"
                variant="outline"
                onClick={showMore}
                aria-disabled={loadingMore || undefined}
              >
                {loadingMore ? "Loading…" : "Show more"}
              </Button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
