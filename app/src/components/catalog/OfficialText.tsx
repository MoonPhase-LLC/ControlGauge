import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import type { CatalogLink, CatalogParam, CatalogPart, LinkTarget } from "@/service/types";

// Rendering of verbatim framework text (Sprint 3). Every string from the
// catalog is rendered as React text nodes -- never as HTML -- so markup or
// script-like content in a source file stays inert (SECURITY.md T-21). The
// only structure recognized inside prose is the source format's own:
//
//   {{ insert: param, <id> }}  -> a parameter placeholder chip
//   [text](#fragment)          -> the link text (the fragment is not followed)
//   *text*                     -> emphasis
//
// Everything else is shown exactly as stored. Nothing here is NIST-specific:
// part names, link relations and statuses are shown as the source states them.

const TOKEN = /\{\{\s*insert:\s*param,\s*([^\s}]+)\s*\}\}|\[([^\]\n]+)\]\(#[^)\s]*\)|\*([^*\n]+)\*/g;

export type ParamIndex = ReadonlyMap<string, CatalogParam>;

export function indexParams(...lists: CatalogParam[][]): ParamIndex {
  const index = new Map<string, CatalogParam>();
  for (const list of lists) for (const param of list) index.set(param.source_id, param);
  return index;
}

/** One line of the placeholder text: the parameter's own label, or its choices. */
function placeholderText(
  param: CatalogParam | undefined,
  id: string,
  depth: number,
  params: ParamIndex | undefined,
): ReactNode {
  if (!param) return id;
  if (param.choices && param.choices.length > 0) {
    return param.choices.map((choice, i) => (
      <span key={i}>
        {i > 0 && <span aria-hidden="true"> | </span>}
        {depth < 2 ? <Prose text={choice} params={params} depth={depth + 1} /> : choice}
      </span>
    ));
  }
  return param.label ?? id;
}

function ParamPlaceholder({ id, params, depth }: { id: string; params: ParamIndex | undefined; depth: number }) {
  const param = params?.get(id);
  return (
    <span
      data-param={id}
      className="mx-0.5 rounded-sm border border-dashed border-primary/40 bg-primary/5 px-1 py-px text-[0.95em] text-foreground"
    >
      <span className="sr-only">Parameter: </span>
      <span aria-hidden="true" className="text-muted-foreground">
        [
      </span>
      {placeholderText(param, id, depth, params)}
      <span aria-hidden="true" className="text-muted-foreground">
        ]
      </span>
    </span>
  );
}

/** Verbatim prose with the source format's inline structure rendered. */
export function Prose({
  text,
  params,
  depth = 0,
}: {
  text: string;
  params: ParamIndex | undefined;
  depth?: number;
}) {
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(TOKEN)) {
    const start = match.index ?? 0;
    if (start > last) nodes.push(text.slice(last, start));
    const [, paramId, linkText, emphasis] = match;
    if (paramId !== undefined) {
      nodes.push(<ParamPlaceholder key={start} id={paramId} params={params} depth={depth} />);
    } else if (linkText !== undefined) {
      nodes.push(linkText);
    } else if (emphasis !== undefined) {
      nodes.push(<em key={start}>{emphasis}</em>);
    }
    last = start + match[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return <>{nodes}</>;
}

/** `assessment-objective` -> `Assessment objective`. Display only. */
export function humanize(name: string): string {
  const spaced = name.replace(/[-_]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Part names OSCAL defines for a catalog's primary text; shown expanded. */
const PRIMARY_PARTS = new Set(["overview", "statement", "guidance"]);

function PartItems({ parts, params }: { parts: CatalogPart[]; params: ParamIndex }) {
  if (parts.length === 0) return null;
  return (
    <ul className="flex flex-col gap-2 pl-4">
      {parts.map((part, i) => (
        <li key={i} className="flex flex-col gap-2">
          <p className="leading-relaxed whitespace-pre-wrap">
            {part.label && <span className="mr-1.5 font-medium">{part.label}</span>}
            {part.title && <span className="mr-1.5 font-medium">{part.title}</span>}
            {part.prose && <Prose text={part.prose} params={params} />}
          </p>
          <PartItems parts={part.parts} params={params} />
        </li>
      ))}
    </ul>
  );
}

/**
 * Top-level parts in source order. Each is a disclosure section headed by
 * its own title or its source part name; primary parts start open.
 */
export function PartSections({
  parts,
  params,
  headingLevel = 3,
}: {
  parts: CatalogPart[];
  params: ParamIndex;
  headingLevel?: 3 | 4;
}) {
  const Heading = headingLevel === 3 ? "h3" : "h4";
  return (
    <div className="flex flex-col gap-3">
      {parts.map((part, i) => (
        <details key={i} open={PRIMARY_PARTS.has(part.name)} className="group/part rounded-lg border border-border/70">
          <summary className="flex cursor-pointer list-none items-center gap-2 rounded-lg px-4 py-2.5 hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground">
            <ChevronRight
              aria-hidden="true"
              className="size-4 shrink-0 text-muted-foreground transition-transform group-open/part:rotate-90"
            />
            <Heading className="inline text-sm font-semibold">
              {part.title ?? humanize(part.name)}
              {part.label && <span className="ml-1.5 font-normal text-muted-foreground">{part.label}</span>}
            </Heading>
          </summary>
          <div className="flex flex-col gap-2 px-4 pb-4 text-sm">
            {part.prose && (
              <p className="leading-relaxed whitespace-pre-wrap">
                <Prose text={part.prose} params={params} />
              </p>
            )}
            <PartItems parts={part.parts} params={params} />
          </div>
        </details>
      ))}
    </div>
  );
}

export function ParameterList({ params, index }: { params: CatalogParam[]; index: ParamIndex }) {
  if (params.length === 0) return null;
  return (
    <dl className="flex flex-col gap-3 text-sm">
      {params.map((param) => (
        <div key={param.source_id} className="flex flex-col gap-1 rounded-lg border border-border/70 p-3">
          <dt className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-mono text-xs text-muted-foreground">{param.source_id}</span>
            {param.label && <span className="font-medium">{param.label}</span>}
          </dt>
          {param.guidelines.map((guideline, i) => (
            <dd key={i} className="whitespace-pre-wrap">
              <Prose text={guideline} params={index} />
            </dd>
          ))}
          {param.choices && (
            <dd className="flex flex-col gap-1">
              {param.select_how_many && (
                <span className="text-xs text-muted-foreground">
                  Choices ({humanize(param.select_how_many).toLowerCase()}):
                </span>
              )}
              <ul className="list-disc pl-5">
                {param.choices.map((choice, i) => (
                  <li key={i}>
                    <Prose text={choice} params={index} />
                  </li>
                ))}
              </ul>
            </dd>
          )}
        </div>
      ))}
    </dl>
  );
}

function targetLabel(target: LinkTarget): string {
  const name = [target.identifier, target.title].filter(Boolean).join(" ");
  if (target.kind === "part" && target.part_label) return `${name}, item ${target.part_label}`;
  return name;
}

/** Links grouped by relation, in source order within each group. */
export function LinkList({
  links,
  onOpenControl,
}: {
  links: CatalogLink[];
  onOpenControl: (controlId: string) => void;
}) {
  if (links.length === 0) return null;
  const groups = new Map<string, CatalogLink[]>();
  for (const link of links) {
    const rel = link.rel ?? "link";
    groups.set(rel, [...(groups.get(rel) ?? []), link]);
  }
  return (
    <div className="flex flex-col gap-4 text-sm">
      {[...groups.entries()].map(([rel, items]) => (
        <section key={rel} className="flex flex-col gap-2">
          <h4 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{humanize(rel)}</h4>
          <ul className="flex flex-wrap gap-2">
            {items.map((link, i) => {
              const { target } = link;
              if (target.control_id && (target.kind === "control" || target.kind === "enhancement" || target.kind === "part")) {
                const controlId = target.control_id;
                return (
                  <li key={i}>
                    <button
                      type="button"
                      onClick={() => onOpenControl(controlId)}
                      className="rounded-md border border-border px-2 py-1 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground"
                    >
                      {targetLabel(target)}
                    </button>
                  </li>
                );
              }
              if (target.kind === "resource") {
                return (
                  <li key={i} className="w-full rounded-md border border-border/70 px-3 py-2">
                    <p className="font-medium">{target.title}</p>
                    {target.citation && (
                      <p className="text-muted-foreground">
                        <Prose text={target.citation} params={undefined} />
                      </p>
                    )}
                    {target.href && <p className="font-mono text-xs break-all text-muted-foreground">{target.href}</p>}
                  </li>
                );
              }
              return (
                <li key={i} className="rounded-md border border-border/70 px-2 py-1 text-muted-foreground">
                  {target.kind === "family" ? targetLabel(target) : (target.href ?? link.href)}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
