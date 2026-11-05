import { useState } from "react";
import { ClipboardList, House, Library, Settings } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { SidebarNav, type NavItem } from "@/components/layout/SidebarNav";
import { ServiceStatusIndicator } from "@/components/service/ServiceStatus";
import { AssessmentDetailScreen } from "@/screens/AssessmentDetailScreen";
import { AssessmentsScreen } from "@/screens/AssessmentsScreen";
import { CatalogScreen, EMPTY_FILTERS, type CatalogFilters } from "@/screens/CatalogScreen";
import { ControlDetailScreen } from "@/screens/ControlDetailScreen";
import { HomeScreen } from "@/screens/HomeScreen";
import { NewAssessmentScreen } from "@/screens/NewAssessmentScreen";
import { useLocalService } from "@/service/useLocalService";

const NAV_ITEMS: NavItem[] = [
  { id: "home", label: "Home", icon: House, available: true },
  { id: "assessments", label: "Assessments", icon: ClipboardList, available: true },
  { id: "catalog", label: "Framework catalog", icon: Library, available: true },
  // Placeholder for a future screen; not navigable yet (see SidebarNav).
  { id: "settings", label: "Settings", icon: Settings, available: false },
];

type View =
  | { screen: "home" }
  | { screen: "assessments" }
  | { screen: "new-assessment" }
  | { screen: "assessment"; id: string }
  | { screen: "catalog" }
  | { screen: "control"; id: string };

/** Which navigation item a view belongs to. */
function section(view: View): string {
  switch (view.screen) {
    case "home":
      return "home";
    case "catalog":
    case "control":
      return "catalog";
    default:
      return "assessments";
  }
}

function viewFor(navId: string): View {
  if (navId === "home") return { screen: "home" };
  if (navId === "catalog") return { screen: "catalog" };
  return { screen: "assessments" };
}

function App() {
  const service = useLocalService();
  const [view, setView] = useState<View>({ screen: "home" });
  // Focus moves to the new screen's heading only after the user navigates,
  // never on first load (the skip link and normal tab order apply then).
  const [navigated, setNavigated] = useState(false);
  // Catalog filters survive opening a control and coming back.
  const [catalogFilters, setCatalogFilters] = useState<CatalogFilters>(EMPTY_FILTERS);

  function go(next: View) {
    setNavigated(true);
    setView(next);
  }

  let content;
  switch (view.screen) {
    case "home":
      content = (
        <HomeScreen
          service={service}
          focusOnMount={navigated}
          onOpenAssessments={() => go({ screen: "assessments" })}
        />
      );
      break;
    case "assessments":
      content = (
        <AssessmentsScreen
          status={service.status}
          focusOnMount={navigated}
          onCreate={() => go({ screen: "new-assessment" })}
          onOpen={(id) => go({ screen: "assessment", id })}
        />
      );
      break;
    case "new-assessment":
      content = (
        <NewAssessmentScreen
          status={service.status}
          focusOnMount={navigated}
          onCancel={() => go({ screen: "assessments" })}
          onCreated={(created) => go({ screen: "assessment", id: created.id })}
        />
      );
      break;
    case "assessment":
      content = (
        <AssessmentDetailScreen
          // A fresh instance per id, so no state leaks between assessments.
          key={view.id}
          id={view.id}
          status={service.status}
          focusOnMount={navigated}
          onBack={() => go({ screen: "assessments" })}
        />
      );
      break;
    case "catalog":
      content = (
        <CatalogScreen
          status={service.status}
          focusOnMount={navigated}
          filters={catalogFilters}
          onFiltersChange={setCatalogFilters}
          onOpenControl={(id) => go({ screen: "control", id })}
        />
      );
      break;
    case "control":
      content = (
        <ControlDetailScreen
          key={view.id}
          id={view.id}
          status={service.status}
          focusOnMount={navigated}
          onBack={() => go({ screen: "catalog" })}
          onOpenControl={(id) => go({ screen: "control", id })}
        />
      );
      break;
  }

  return (
    <AppShell
      navigation={
        <SidebarNav
          items={NAV_ITEMS}
          current={section(view)}
          onNavigate={(id) => go(viewFor(id))}
        />
      }
      sidebarFooter={<ServiceStatusIndicator status={service.status} />}
    >
      {content}
    </AppShell>
  );
}

export default App;
