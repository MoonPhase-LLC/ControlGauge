import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";

export type NavItem = {
  id: string;
  label: string;
  icon: LucideIcon;
  /** `false`: a placeholder for a screen that does not exist yet. */
  available: boolean;
};

/**
 * Main navigation. Available items switch the main screen and mark the
 * current one with `aria-current="page"`. Unavailable items stay visible and
 * focusable (so keyboard and screen-reader users can discover them) but are
 * marked `aria-disabled`, labelled "Not available yet", and do nothing when
 * activated.
 */
export function SidebarNav({
  items,
  current,
  onNavigate,
}: {
  items: NavItem[];
  current: string;
  onNavigate: (id: string) => void;
}) {
  return (
    <nav aria-label="Main">
      <ul className="flex flex-wrap gap-1 md:flex-col">
        {items.map(({ id, label, icon: Icon, available }) => (
          <li key={id}>
            {available ? (
              <Button
                type="button"
                variant="ghost"
                aria-current={current === id ? "page" : undefined}
                onClick={() => onNavigate(id)}
                className="h-auto w-full justify-start gap-2.5 py-2 text-left aria-[current=page]:bg-sidebar-accent aria-[current=page]:text-sidebar-accent-foreground"
              >
                <Icon aria-hidden="true" className="text-muted-foreground" />
                <span>{label}</span>
              </Button>
            ) : (
              <Button
                type="button"
                variant="ghost"
                aria-disabled="true"
                className="h-auto w-full justify-start gap-2.5 py-1.5 text-left aria-disabled:cursor-not-allowed aria-disabled:hover:bg-transparent"
              >
                <Icon aria-hidden="true" className="text-muted-foreground" />
                <span className="flex flex-col">
                  <span className="text-sidebar-foreground/70">{label}</span>
                  <span className="text-xs font-normal text-muted-foreground">Not available yet</span>
                </span>
              </Button>
            )}
          </li>
        ))}
      </ul>
    </nav>
  );
}
