import { ArrowRight } from "lucide-react";
import { ScreenHeading } from "@/components/screen";
import { ServiceStatusPanel } from "@/components/service/ServiceStatus";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { LocalService } from "@/service/useLocalService";

/**
 * The start screen: what the app is, where assessments live, and the local
 * service's status. It shows no sample or invented assessment data.
 */
export function HomeScreen({
  service,
  focusOnMount,
  onOpenAssessments,
}: {
  service: LocalService;
  focusOnMount: boolean;
  onOpenAssessments: () => void;
}) {
  return (
    <div className="flex flex-col gap-10">
      <header className="flex flex-col gap-2">
        <ScreenHeading focusOnMount={focusOnMount}>Welcome to ControlGauge</ScreenHeading>
        <p className="max-w-prose text-muted-foreground">
          ControlGauge is a local-first tool for analyzing cybersecurity compliance evidence
          against framework controls.
        </p>
      </header>

      <section aria-labelledby="assessments-heading" className="flex flex-col gap-3">
        <h2 id="assessments-heading" className="text-lg font-semibold tracking-tight">
          Assessments
        </h2>
        <Card>
          <CardHeader className="justify-items-start gap-2">
            <CardTitle>Create and review assessments</CardTitle>
            <CardDescription className="max-w-prose">
              Set up an assessment for a framework. Uploading evidence and reviewing suggested
              control mappings will be added in later releases.
            </CardDescription>
            <Button type="button" variant="outline" onClick={onOpenAssessments} className="mt-1">
              Go to assessments
              <ArrowRight aria-hidden="true" />
            </Button>
          </CardHeader>
        </Card>
      </section>

      <section aria-labelledby="service-heading" className="flex flex-col gap-3">
        <h2 id="service-heading" className="text-lg font-semibold tracking-tight">
          Local service
        </h2>
        <ServiceStatusPanel service={service} />
      </section>
    </div>
  );
}
