import { useId, type ReactNode } from "react";
import { BookOpenText } from "lucide-react";

/**
 * Frames verbatim framework text and labels it as such, with the source's
 * attribution. Anything product-written (navigation, hints, badges) stays
 * outside this region, so official wording and product text are never
 * mixed (`CLAUDE.md`: never present product guidance as official wording).
 */
export function OfficialTextRegion({
  source,
  label = "Official text",
  children,
}: {
  source: string;
  label?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section
      aria-labelledby={id}
      data-official-text=""
      className="flex flex-col gap-3 rounded-xl border-l-4 border-primary/50 bg-muted/30 p-4"
    >
      <header className="flex flex-col gap-1">
        <p id={id} className="flex items-center gap-1.5 text-xs font-semibold tracking-wide uppercase">
          <BookOpenText aria-hidden="true" className="size-3.5" />
          {label}
        </p>
        <p className="text-xs text-muted-foreground">{source}</p>
      </header>
      {children}
    </section>
  );
}
