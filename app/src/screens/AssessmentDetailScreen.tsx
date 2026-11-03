import { ArrowLeft, FileUp } from "lucide-react";
import { ErrorNotice, LoadingNotice, ScreenHeading, ServiceNotReady, formatDateTime } from "@/components/screen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getAssessment } from "@/service/commands";
import { isNotFound } from "@/service/errors";
import type { SupervisorState } from "@/service/types";
import { useServiceQuery } from "@/service/useServiceQuery";

/** One assessment's stored details. Evidence features arrive in later sprints. */
export function AssessmentDetailScreen({
  id,
  status,
  focusOnMount,
  onBack,
}: {
  id: string;
  status: SupervisorState;
  focusOnMount: boolean;
  onBack: () => void;
}) {
  const ready = status.state === "ready";
  const query = useServiceQuery(() => getAssessment(id), id, ready);
  const assessment = query.data;

  return (
    <div className="flex flex-col gap-8">
      <div>
        <Button type="button" variant="ghost" size="sm" onClick={onBack} className="-ml-2.5">
          <ArrowLeft aria-hidden="true" />
          Back to assessments
        </Button>
      </div>

      {!ready ? (
        <>
          <ScreenHeading focusOnMount={focusOnMount}>Assessment</ScreenHeading>
          <ServiceNotReady status={status} />
        </>
      ) : query.loading && !assessment ? (
        <>
          <ScreenHeading focusOnMount={focusOnMount}>Assessment</ScreenHeading>
          <LoadingNotice label="Loading assessment…" />
        </>
      ) : query.error ? (
        <>
          <ScreenHeading focusOnMount={focusOnMount}>
            {isNotFound(query.error) ? "Assessment not found" : "Assessment"}
          </ScreenHeading>
          <ErrorNotice
            title={isNotFound(query.error) ? "This assessment does not exist" : "The assessment could not be loaded"}
            error={query.error}
            onRetry={isNotFound(query.error) ? undefined : query.reload}
          />
        </>
      ) : assessment ? (
        <>
          <header className="flex flex-col gap-2">
            <ScreenHeading focusOnMount={focusOnMount}>{assessment.name}</ScreenHeading>
            <p className="text-muted-foreground">
              {assessment.framework.name} {assessment.framework.version}
            </p>
          </header>

          <Card>
            <CardHeader>
              <CardTitle>
                <h2>Details</h2>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-[max-content_1fr]">
                <dt className="text-sm text-muted-foreground">Framework</dt>
                <dd>
                  {assessment.framework.name} {assessment.framework.version}
                </dd>
                <dt className="text-sm text-muted-foreground">Catalog release</dt>
                <dd>
                  {assessment.catalog_release ? (
                    <>
                      Release {assessment.catalog_release.version}
                      <span className="block text-sm text-muted-foreground">
                        This assessment stays on this release even if a newer one is installed.
                      </span>
                    </>
                  ) : (
                    <span className="text-muted-foreground">No control catalog was loaded for this framework</span>
                  )}
                </dd>
                <dt className="text-sm text-muted-foreground">Created</dt>
                <dd>
                  <time dateTime={assessment.created_at}>{formatDateTime(assessment.created_at)}</time>
                </dd>
                <dt className="text-sm text-muted-foreground">Description</dt>
                <dd className="whitespace-pre-wrap">
                  {assessment.description ?? <span className="text-muted-foreground">No description</span>}
                </dd>
                <dt className="text-sm text-muted-foreground">Assessment ID</dt>
                <dd className="font-mono text-xs break-all">{assessment.id}</dd>
              </dl>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="justify-items-start gap-2">
              <FileUp aria-hidden="true" className="size-5 text-muted-foreground" />
              <CardTitle>
                <h2>Evidence</h2>
              </CardTitle>
              <CardDescription className="max-w-prose">
                Uploading evidence and reviewing suggested control mappings are not available yet.
                They will be added to this assessment in a later release.
              </CardDescription>
            </CardHeader>
          </Card>
        </>
      ) : null}
    </div>
  );
}
