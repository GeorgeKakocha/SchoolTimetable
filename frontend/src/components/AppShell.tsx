import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { getSchedulingConfigIndex } from "../api/client";
import { loadAppConfig } from "../config/appConfig";

/**
 * Phase 3C.3a: the shared top-navigation shell introduced now that the
 * product has multiple real pages -- a compact top bar, not a sidebar,
 * matching this product's identity as a professional scheduling tool
 * rather than a generic school LMS. Only real, routed destinations
 * appear here; no placeholder nav items for unbuilt future sections
 * (Constraints, ...).
 *
 * Real-School Setup MVP Slice E adds "School Setup" as a third flat
 * link, ordered before "Teaching Assignments" (reference data logically
 * precedes workload assignment) -- an explicitly approved design-gate
 * deviation keeps this nav flat rather than introducing a "Configuration"
 * dropdown/group container; the `/configuration/...` URL prefix stays a
 * conceptual grouping only, not a new navigation construct.
 *
 * Teacher Availability Slice B (Owner Decision #38) adds "Teacher
 * Availability" as a fourth flat link, ordered between "School Setup"
 * and "Teaching Assignments" -- it is a scheduling-constraint surface
 * on top of School Setup's reference data, and should itself be known
 * before workload (Teaching Assignments) is entered; same flat-nav
 * discipline, no new navigation construct.
 *
 * Owns the ONE visible "school · academic year" context line (never a
 * raw natural ID) -- `TimetablePage` deliberately no longer renders its
 * own copy of this text, so it appears exactly once regardless of which
 * page is active. Sourced from the same single `appConfig.ts` env point
 * and the same `getSchedulingConfigIndex` call `TimetablePage` already
 * makes for its own (unrelated) class-section-listing purpose -- this
 * is a second, independent read of the small `/config` index, not a
 * shared cache: de-duplicating it fully would mean either lifting
 * `TimetablePage`'s fetch/loading/error state machine out into an
 * `Outlet` context (a real structural change to an already-tested page,
 * out of scope for this visual-only pass) or introducing a shared
 * client-side store (explicitly out of scope -- no global state
 * library). Two lightweight `/config` reads is the accepted, documented
 * tradeoff; browser-level HTTP caching keeps the real cost negligible.
 * Best-effort/non-blocking: a failure here only blanks this line, never
 * blocks navigation or a page's own content/error handling.
 */

type ContextState =
  | { status: "loading" }
  | { status: "unavailable" }
  | { status: "ready"; label: string };

function navLinkClassName({ isActive }: { isActive: boolean }): string {
  return isActive ? "app-nav-link app-nav-link-active" : "app-nav-link";
}

function AppShell() {
  const [contextState, setContextState] = useState<ContextState>({ status: "loading" });

  useEffect(() => {
    let schoolId: string;
    let academicYearId: string;
    try {
      const config = loadAppConfig();
      schoolId = config.schoolId;
      academicYearId = config.academicYearId;
    } catch {
      setContextState({ status: "unavailable" });
      return;
    }

    const controller = new AbortController();
    getSchedulingConfigIndex(schoolId, academicYearId, controller.signal)
      .then((index) => {
        if (controller.signal.aborted) {
          return;
        }
        setContextState({ status: "ready", label: `${index.school.name} · ${index.academic_year.label}` });
      })
      .catch(() => {
        if (controller.signal.aborted) {
          return;
        }
        setContextState({ status: "unavailable" });
      });

    return () => {
      controller.abort();
    };
  }, []);

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-identity">
          <span className="app-brand">School Timetable</span>
          {contextState.status === "ready" && <span className="app-context">{contextState.label}</span>}
        </div>
        <nav className="app-nav" aria-label="Main">
          <NavLink to="/timetable" className={navLinkClassName}>
            Timetable
          </NavLink>
          <NavLink to="/configuration/setup" className={navLinkClassName}>
            School Setup
          </NavLink>
          <NavLink to="/configuration/teacher-availability" className={navLinkClassName}>
            Teacher Availability
          </NavLink>
          <NavLink to="/configuration/teaching-assignments" className={navLinkClassName}>
            Teaching Assignments
          </NavLink>
        </nav>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}

export default AppShell;
