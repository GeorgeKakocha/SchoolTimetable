import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import TeachersPanel from "../components/setup/TeachersPanel";
import ClassesPanel from "../components/setup/ClassesPanel";
import SubjectsPanel from "../components/setup/SubjectsPanel";
import SpecialActivitiesPanel from "../components/setup/SpecialActivitiesPanel";
import RoomsResourcesPanel from "../components/setup/RoomsResourcesPanel";

/**
 * Real-School Setup MVP Slice E: `/configuration/setup`, the School
 * Setup page. Manages Teachers, Classes, Subjects, and (Reserved
 * Activities Slice A1/Reserved B) Special Activities via four local
 * tabs -- no nested tab routes (`/configuration/setup/teachers` etc.),
 * matching the locked design-gate contract that tabs are page state
 * only, not router state.
 *
 * Resources C adds a fifth tab, "Rooms & Resources" (the user-facing
 * label for the existing `Resource` catalog), following the exact
 * same self-contained-panel architecture as the other four -- no
 * change to the tab mechanism itself.
 *
 * Only the active tab's panel is ever mounted: the tabpanel below
 * renders exactly one panel component per render, so switching tabs
 * unmounts the previous panel (discarding its own local fetch/state)
 * and mounts the new one, which performs its own independent GET on
 * mount. This is a deliberate simplicity choice (design gate #11/#21):
 * no cross-tab cache, no global store, no requirement that all four
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
 *
 * Reserved B's corrected frontend contract adds one narrow, typed,
 * one-shot navigation mechanism so another page (Reserved Activities'
 * own prerequisite surface) can land here with a specific tab already
 * active, without introducing nested routes, a persistent global tab
 * store, or query/hash parameters. A caller navigates via
 * `navigate("/configuration/setup", { state: { requestedTab: "..." }
 * satisfies SchoolSetupNavigationState })`; this page reads that
 * `location.state` once, on mount, to seed its initial `activeTab`,
 * then immediately replaces the history entry with `state: null` so
 * the hint never survives a later Back/reload -- a direct visit, a
 * reload, or a normal in-page tab click are all completely unaffected
 * and default/stay on "teachers"/whatever tab is locally active.
 */

export type TabKey = "teachers" | "classes" | "subjects" | "special-activities" | "rooms-resources";

export interface SchoolSetupNavigationState {
  requestedTab?: TabKey;
}

const VALID_TAB_KEYS: readonly TabKey[] = [
  "teachers",
  "classes",
  "subjects",
  "special-activities",
  "rooms-resources",
];

export function isSchoolSetupTabKey(value: unknown): value is TabKey {
  return typeof value === "string" && (VALID_TAB_KEYS as readonly string[]).includes(value);
}

interface TabDefinition {
  key: TabKey;
  label: string;
}

const TABS: readonly TabDefinition[] = [
  { key: "teachers", label: "Teachers" },
  { key: "classes", label: "Classes" },
  { key: "subjects", label: "Subjects" },
  { key: "special-activities", label: "Special Activities" },
  { key: "rooms-resources", label: "Rooms & Resources" },
];

function tabId(key: TabKey): string {
  return `school-setup-tab-${key}`;
}

function panelId(key: TabKey): string {
  return `school-setup-panel-${key}`;
}

function SchoolSetupPage() {
  const location = useLocation();
  const navigate = useNavigate();

  const [activeTab, setActiveTab] = useState<TabKey>(() => {
    const requestedTab = (location.state as SchoolSetupNavigationState | null)?.requestedTab;
    return isSchoolSetupTabKey(requestedTab) ? requestedTab : "teachers";
  });
  const tabRefs = useRef<Partial<Record<TabKey, HTMLButtonElement | null>>>({});

  // One-shot consumption of a `requestedTab` navigation hint: the
  // initial `activeTab` above already applied it, so this effect only
  // clears `location.state` (replacing this history entry) so a later
  // Back navigation or a reload of this exact entry never re-applies
  // it. Runs at most once per mount -- a normal in-page tab click
  // never touches `location.state` at all.
  useEffect(() => {
    const requestedTab = (location.state as SchoolSetupNavigationState | null)?.requestedTab;
    if (isSchoolSetupTabKey(requestedTab)) {
      navigate(location.pathname, { replace: true, state: null });
    }
  }, []);

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
        {activeTab === "special-activities" && <SpecialActivitiesPanel />}
        {activeTab === "rooms-resources" && <RoomsResourcesPanel />}
      </div>
    </div>
  );
}

export default SchoolSetupPage;
