import { useState } from "react";
import { ClipboardList, House, Settings } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { SidebarNav, type NavItem } from "@/components/layout/SidebarNav";
import { ServiceStatusIndicator } from "@/components/service/ServiceStatus";
import { AssessmentDetailScreen } from "@/screens/AssessmentDetailScreen";
import { AssessmentsScreen } from "@/screens/AssessmentsScreen";
import { HomeScreen } from "@/screens/HomeScreen";
import { NewAssessmentScreen } from "@/screens/NewAssessmentScreen";
import { useLocalService } from "@/service/useLocalService";

const NAV_ITEMS: NavItem[] = [
  { id: "home", label: "Home", icon: House, available: true },
  { id: "assessments", label: "Assessments", icon: ClipboardList, available: true },
  // Placeholder for a future screen; not navigable yet (see SidebarNav).
  { id: "settings", label: "Settings", icon: Settings, available: false },
];

type View =
  | { screen: "home" }
  | { screen: "assessments" }
  | { screen: "new-assessment" }
  | { screen: "assessment"; id: string };

/** Which navigation item a view belongs to. */
function section(view: View): string {
  return view.screen === "home" ? "home" : "assessments";
}

function App() {
  const service = useLocalService();
  const [view, setView] = useState<View>({ screen: "home" });
  // Focus moves to the new screen's heading only after the user navigates,
  // never on first load (the skip link and normal tab order apply then).
  const [navigated, setNavigated] = useState(false);

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
  }

  return (
    <AppShell
      navigation={
        <SidebarNav
          items={NAV_ITEMS}
          current={section(view)}
          onNavigate={(id) => go(id === "home" ? { screen: "home" } : { screen: "assessments" })}
        />
      }
      sidebarFooter={<ServiceStatusIndicator status={service.status} />}
    >
      {content}
    </AppShell>
  );
}

export default App;
