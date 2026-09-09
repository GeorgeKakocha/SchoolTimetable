import { useCallback, useRef, useState } from "react";
import TeachersPanel from "../components/setup/TeachersPanel";
import ClassesPanel from "../components/setup/ClassesPanel";
import SubjectsPanel from "../components/setup/SubjectsPanel";

/**
 * Real-School Setup MVP Slice E: `/configuration/setup`, the School
 * Setup page. Manages exactly Teachers, Classes, and Subjects via three
 * local tabs -- no nested tab routes (`/configuration/setup/teachers`
 * etc.), matching the locked design-gate contract that tabs are page
 * state only, not router state.
 *
 * Only the active tab's panel is ever mounted: `ACTIVE_PANELS` below
 * renders exactly one panel component per render, so switching tabs
 * unmounts the previous panel (discarding its own local fetch/state)
 * and mounts the new one, which performs its own independent GET on
 * mount. This is a deliberate simplicity choice (design gate #11/#21):
 * no cross-tab cache, no global store, no requirement that all three
 * resource projections load on initial page view -- each panel is
 * exactly as self-contained as `TeachingAssignmentsPage` already is.
 *
 * Tabs follow the WAI-ARIA "Tabs (Automatic Activation)" pattern:
 * `role="tablist"`/`role="tab"`/`role="tabpanel"`, `aria-selected`,
 * `aria-controls`/`aria-labelledby`, roving `tabIndex` (only the active
 * tab is in the regular tab order), and ArrowLeft/ArrowRight/Home/End
 * both move focus AND change the active tab (automatic activation) --
 * the standard behavior for a tablist that doesn't fetch anything the
 * user finds costly on every keypress.
 */

type TabKey = "teachers" | "classes" | "subjects";

interface TabDefinition {
  key: TabKey;
  label: string;
}

const TABS: readonly TabDefinition[] = [
  { key: "teachers", label: "Teachers" },
  { key: "classes", label: "Classes" },
  { key: "subjects", label: "Subjects" },
];

function tabId(key: TabKey): string {
  return `school-setup-tab-${key}`;
}

function panelId(key: TabKey): string {
  return `school-setup-panel-${key}`;
}

function SchoolSetupPage() {
  const [activeTab, setActiveTab] = useState<TabKey>("teachers");
  const tabRefs = useRef<Partial<Record<TabKey, HTMLButtonElement | null>>>({});

  const activateTab = useCallback((key: TabKey, focus: boolean) => {
    setActiveTab(key);
    if (focus) {
      tabRefs.current[key]?.focus();
    }
  }, []);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const currentIndex = TABS.findIndex((tab) => tab.key === activeTab);
      if (currentIndex === -1) {
        return;
      }
      let nextIndex: number | null = null;
      switch (event.key) {
        case "ArrowRight":
          nextIndex = (currentIndex + 1) % TABS.length;
          break;
        case "ArrowLeft":
          nextIndex = (currentIndex - 1 + TABS.length) % TABS.length;
          break;
        case "Home":
          nextIndex = 0;
          break;
        case "End":
          nextIndex = TABS.length - 1;
          break;
        default:
          return;
      }
      event.preventDefault();
      const nextTab = TABS[nextIndex];
      if (nextTab !== undefined) {
        activateTab(nextTab.key, true);
      }
    },
    [activeTab, activateTab],
  );

  return (
    <div className="school-setup-page">
      <h1>School Setup</h1>
      <p className="page-subtitle">Manage the teachers, classes, and subjects used to build the timetable.</p>

      <div className="setup-tablist" role="tablist" aria-label="School Setup sections" onKeyDown={handleKeyDown}>
        {TABS.map((tab) => {
          const isActive = tab.key === activeTab;
          return (
            <button
              key={tab.key}
              ref={(element) => {
                tabRefs.current[tab.key] = element;
              }}
              type="button"
              role="tab"
              id={tabId(tab.key)}
              aria-selected={isActive}
              aria-controls={panelId(tab.key)}
              tabIndex={isActive ? 0 : -1}
              className={isActive ? "setup-tab setup-tab-active" : "setup-tab"}
              onClick={() => activateTab(tab.key, false)}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      <div
        className="setup-tabpanel"
        role="tabpanel"
        id={panelId(activeTab)}
        aria-labelledby={tabId(activeTab)}
        tabIndex={0}
      >
        {activeTab === "teachers" && <TeachersPanel />}
        {activeTab === "classes" && <ClassesPanel />}
        {activeTab === "subjects" && <SubjectsPanel />}
      </div>
    </div>
  );
}

export default SchoolSetupPage;
