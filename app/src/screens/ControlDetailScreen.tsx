import { ArrowLeft, ChevronRight } from "lucide-react";
import { LinkList, ParameterList, PartSections, indexParams } from "@/components/catalog/OfficialText";
import { OfficialTextRegion } from "@/components/catalog/OfficialTextRegion";
import { ErrorNotice, LoadingNotice, ScreenHeading, ServiceNotReady } from "@/components/screen";
import { Button } from "@/components/ui/button";
import { getControl } from "@/service/commands";
import { isNotFound } from "@/service/errors";
import type { ControlDetail, Enhancement, SupervisorState } from "@/service/types";
import { useServiceQuery } from "@/service/useServiceQuery";
import { StatusBadge } from "@/screens/CatalogScreen";

/** One control with its verbatim text, parameters, links and enhancements. */
export function ControlDetailScreen({
  id,
  status,
  focusOnMount,
  onBack,
  onOpenControl,
}: {
  id: string;
  status: SupervisorState;
  focusOnMount: boolean;
  onBack: () => void;
  onOpenControl: (id: string) => void;
}) {
  const ready = status.state === "ready";
  const query = useServiceQuery(() => getControl(id), id, ready);
  const control = query.data;

  return (
    <div className="flex flex-col gap-8">
      <div>
        <Button type="button" variant="ghost" size="sm" onClick={onBack} className="-ml-2.5">
          <ArrowLeft aria-hidden="true" />
          Back to catalog
        </Button>
      </div>

      {!ready ? (
        <>
          <ScreenHeading focusOnMount={focusOnMount}>Control</ScreenHeading>
          <ServiceNotReady status={status} />
        </>
      ) : query.loading && !control ? (
        <>
          <ScreenHeading focusOnMount={focusOnMount}>Control</ScreenHeading>
          <LoadingNotice label="Loading control…" />
        </>
      ) : query.error ? (
        <>
          <ScreenHeading focusOnMount={focusOnMount}>
            {isNotFound(query.error) ? "Control not found" : "Control"}
          </ScreenHeading>
          <ErrorNotice
            title={isNotFound(query.error) ? "This control does not exist" : "The control could not be loaded"}
            error={query.error}
            onRetry={isNotFound(query.error) ? undefined : query.reload}
          />
        </>
      ) : control ? (
        <ControlBody control={control} focusOnMount={focusOnMount} onOpenControl={onOpenControl} />
      ) : null}
    </div>
  );
}

function ControlBody({
  control,
  focusOnMount,
  onOpenControl,
}: {
  control: ControlDetail;
  focusOnMount: boolean;
  onOpenControl: (id: string) => void;
}) {
  const params = indexParams(control.params, ...control.enhancements.map((e) => e.params));
  const { release } = control;
  const source = release.attribution;

  return (
    <>
      <header className="flex flex-col gap-2">
        <p className="font-mono text-sm text-muted-foreground">{control.identifier}</p>
        <ScreenHeading focusOnMount={focusOnMount}>{control.title}</ScreenHeading>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
          <span>
            {release.framework.name} {release.framework.version} · Release {release.version}
          </span>
          {control.family && (
            <span>
              {control.family.identifier} · {control.family.name}
            </span>
          )}
          {control.status && <StatusBadge status={control.status} />}
          {control.baselines.length > 0 && <span>Baselines: {control.baselines.join(", ")}</span>}
        </div>
      </header>

      <OfficialTextRegion source={source}>
        {control.parts.length > 0 ? (
          <PartSections parts={control.parts} params={params} />
        ) : (
          <p className="text-sm text-muted-foreground">The source has no text for this control.</p>
        )}
      </OfficialTextRegion>

      {control.params.length > 0 && (
        <section aria-labelledby="params-heading" className="flex flex-col gap-3">
          <h2 id="params-heading" className="text-lg font-semibold">
            Parameters
          </h2>
          <p className="max-w-prose text-sm text-muted-foreground">
            Bracketed, outlined values in the text above are parameters. Their labels, guidelines and
            choices below are from the source.
          </p>
          <ParameterList params={control.params} index={params} />
        </section>
      )}

      {control.links.length > 0 && (
        <section aria-labelledby="links-heading" className="flex flex-col gap-3">
          <h2 id="links-heading" className="text-lg font-semibold">
            Links in the source
          </h2>
          <LinkList links={control.links} onOpenControl={onOpenControl} />
        </section>
      )}

      {control.enhancements.length > 0 && (
        <section aria-labelledby="enhancements-heading" className="flex flex-col gap-3">
          <h2 id="enhancements-heading" className="text-lg font-semibold">
            Enhancements ({control.enhancements.length})
          </h2>
          <ul className="flex flex-col gap-2">
            {control.enhancements.map((e) => (
              <li key={e.id}>
                <EnhancementItem
                  enhancement={e}
                  params={params}
                  source={source}
                  onOpenControl={onOpenControl}
                />
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function EnhancementItem({
  enhancement,
  params,
  source,
  onOpenControl,
}: {
  enhancement: Enhancement;
  params: ReturnType<typeof indexParams>;
  source: string;
  onOpenControl: (id: string) => void;
}) {
  return (
    <details className="group/enhancement rounded-xl bg-card ring-1 ring-foreground/10">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 rounded-xl p-3.5 hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground">
        <ChevronRight
          aria-hidden="true"
          className="size-4 shrink-0 text-muted-foreground transition-transform group-open/enhancement:rotate-90"
        />
        <h3 className="flex flex-wrap items-baseline gap-x-3">
          <span className="font-mono text-sm font-medium">{enhancement.identifier}</span>{" "}
          <span className={enhancement.status ? "text-muted-foreground" : undefined}>{enhancement.title}</span>
        </h3>
        <span className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
          {enhancement.status && <StatusBadge status={enhancement.status} />}
          {enhancement.baselines.length > 0 && <span>Baselines: {enhancement.baselines.join(", ")}</span>}
        </span>
      </summary>
      <div className="flex flex-col gap-4 px-3.5 pb-4">
        {enhancement.parts.length > 0 && (
          <OfficialTextRegion source={source}>
            <PartSections parts={enhancement.parts} params={params} headingLevel={4} />
          </OfficialTextRegion>
        )}
        {enhancement.params.length > 0 && <ParameterList params={enhancement.params} index={params} />}
        <LinkList links={enhancement.links} onOpenControl={onOpenControl} />
      </div>
    </details>
  );
}
