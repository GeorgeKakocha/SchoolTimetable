import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { getClassTimetable, getSchedulingConfigIndex } from "./api/client";
import { getTeachingAssignments } from "./api/teachingAssignments";
import { getReservedActivities } from "./api/reservedActivities";
import { loadAppConfig } from "./config/appConfig";
import type {
  ClassTimetableResponse,
  SchedulingConfigIndexResponse,
  TeachingAssignmentsProjectionResponse,
} from "./api/types";
import type { ReservedActivitiesProjectionResponse } from "./api/reservedActivities";

// Phase 3C.3a routing/shell tests -- `App.tsx` is now only the router
// root, so this file covers route wiring, the shared shell's nav/active
// state, and the not-found fallback. Each page's own data-loading
// behavior is covered by its own co-located test file
// (`pages/TimetablePage.test.tsx`, `pages/TeachingAssignmentsPage.test.tsx`).
vi.mock("./api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/client")>();
  return {
    ...actual,
    getSchedulingConfigIndex: vi.fn(),
    getClassTimetable: vi.fn(),
  };
});

vi.mock("./api/teachingAssignments", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/teachingAssignments")>();
  return {
    ...actual,
    getTeachingAssignments: vi.fn(),
  };
});

vi.mock("./api/reservedActivities", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/reservedActivities")>();
  return {
    ...actual,
    getReservedActivities: vi.fn(),
  };
});

vi.mock("./config/appConfig", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./config/appConfig")>();
  return {
    ...actual,
    loadAppConfig: vi.fn(),
  };
});

const mockedGetSchedulingConfigIndex = vi.mocked(getSchedulingConfigIndex);
const mockedGetClassTimetable = vi.mocked(getClassTimetable);
const mockedGetTeachingAssignments = vi.mocked(getTeachingAssignments);
const mockedGetReservedActivities = vi.mocked(getReservedActivities);
const mockedLoadAppConfig = vi.mocked(loadAppConfig);

const CONFIG_INDEX: SchedulingConfigIndexResponse = {
  school: { id: "s1", name: "Pilot School" },
  academic_year: { id: "y1", label: "2025/2026" },
  class_sections: [{ id: "8a", name: "8-A" }],
  teachers: [{ id: "t1", name: "Teacher One" }],
};

const TIMETABLE_8A: ClassTimetableResponse = {
  school_id: "s1",
  school_name: "Pilot School",
  academic_year_id: "y1",
  academic_year_label: "2025/2026",
  class_section_id: "8a",
  class_section_name: "8-A",
  version_number: 1,
  solver_status: "OPTIMAL",
  total_soft_penalty: 0,
  created_at: "2026-01-01T00:00:00Z",
  is_active: true,
  days: [{ id: "mon", name: "Monday" }],
  rows: [{ period_id: "p1", period_name: "Period 1", cells: [{ day_id: "mon", entries: [] }] }],
};

const EMPTY_PROJECTION: TeachingAssignmentsProjectionResponse = {
  configuration_locked: false,
  assignments: [],
  teachers: [],
  whole_class_targets: [],
  activities: [],
  teacher_workloads: [],
};

const EMPTY_RESERVED_ACTIVITIES_PROJECTION: ReservedActivitiesProjectionResponse = {
  configuration_locked: false,
  special_activities: [],
  teachers: [],
  class_sections: [],
  days: [],
  periods: [],
  reserved_activities: [],
};

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
  mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
  mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
  mockedGetTeachingAssignments.mockResolvedValue(EMPTY_PROJECTION);
  mockedGetReservedActivities.mockResolvedValue(EMPTY_RESERVED_ACTIVITIES_PROJECTION);
  window.history.pushState({}, "", "/");
});

describe("App routing", () => {
  it("redirects / to /timetable", async () => {
    render(<App />);

    await screen.findByRole("heading", { name: "Timetable", level: 1 });
    expect(window.location.pathname).toBe("/timetable");
  });

  it("renders the existing timetable experience at /timetable", async () => {
    window.history.pushState({}, "", "/timetable");

    render(<App />);

    await screen.findByRole("heading", { name: "Timetable", level: 1 });
    await screen.findByRole("combobox", { name: "Class" });
  });

  it("renders the School Setup page at /configuration/setup", async () => {
    window.history.pushState({}, "", "/configuration/setup");

    render(<App />);

    await screen.findByRole("heading", { name: "School Setup", level: 1 });
  });

  it("shows the exact five nav links in order, with Reserved Activities last and no Special Activities entry", async () => {
    window.history.pushState({}, "", "/timetable");

    render(<App />);

    const links = await screen.findAllByRole("link");
    const labels = links.map((link) => link.textContent);
    expect(labels).toEqual([
      "Timetable",
      "School Setup",
      "Teacher Availability",
      "Teaching Assignments",
      "Reserved Activities",
    ]);
    expect(screen.getAllByRole("link", { name: "School Setup" })).toHaveLength(1);
    expect(screen.queryByRole("link", { name: "Special Activities" })).not.toBeInTheDocument();
  });

  it("keeps the nav container's app-nav class (the narrow-width wrap CSS hook) and all five links inside it", async () => {
    window.history.pushState({}, "", "/timetable");

    render(<App />);

    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(nav.className).toContain("app-nav");
    const linksInNav = within(nav).getAllByRole("link");
    expect(linksInNav).toHaveLength(5);
  });

  it("renders the Teacher Availability page at /configuration/teacher-availability", async () => {
    window.history.pushState({}, "", "/configuration/teacher-availability");

    render(<App />);

    await screen.findByRole("heading", { name: "Teacher Availability", level: 1 });
  });

  it("shows exactly one Teacher Availability nav link", async () => {
    window.history.pushState({}, "", "/timetable");

    render(<App />);

    await screen.findAllByRole("link");
    expect(screen.getAllByRole("link", { name: "Teacher Availability" })).toHaveLength(1);
  });

  it("renders the Teaching Assignments page at /configuration/teaching-assignments", async () => {
    window.history.pushState({}, "", "/configuration/teaching-assignments");

    render(<App />);

    await screen.findByRole("heading", { name: "Teaching Assignments", level: 1 });
    expect(mockedGetTeachingAssignments).toHaveBeenCalledWith("s1", "y1", expect.any(AbortSignal));
  });

  it("renders the Reserved Activities page at /configuration/reserved-activities", async () => {
    window.history.pushState({}, "", "/configuration/reserved-activities");

    render(<App />);

    await screen.findByRole("heading", { name: "Reserved Activities", level: 1 });
  });

  it("shows exactly one Reserved Activities nav link", async () => {
    window.history.pushState({}, "", "/timetable");

    render(<App />);

    await screen.findAllByRole("link");
    expect(screen.getAllByRole("link", { name: "Reserved Activities" })).toHaveLength(1);
  });

  it("renders a not-found state for an unknown route", async () => {
    window.history.pushState({}, "", "/does-not-exist");

    render(<App />);

    await screen.findByRole("heading", { name: "Page not found", level: 1 });
  });

  it("marks the active nav link and updates it on navigation", async () => {
    window.history.pushState({}, "", "/timetable");

    render(<App />);
    const timetableLink = await screen.findByRole("link", { name: "Timetable" });
    const teachingAssignmentsLink = screen.getByRole("link", { name: "Teaching Assignments" });

    expect(timetableLink.className).toContain("app-nav-link-active");
    expect(teachingAssignmentsLink.className).not.toContain("app-nav-link-active");

    fireEvent.click(teachingAssignmentsLink);

    await screen.findByRole("heading", { name: "Teaching Assignments", level: 1 });
    expect(teachingAssignmentsLink.className).toContain("app-nav-link-active");
    expect(timetableLink.className).not.toContain("app-nav-link-active");
  });

  it("shows the shared school/year context in the shell, exactly once, on every page", async () => {
    window.history.pushState({}, "", "/timetable");

    render(<App />);

    // `findByText` throws on more than one match, so this also proves
    // `TimetablePage` no longer renders its own duplicate copy of this
    // text now that `AppShell` owns it.
    await screen.findByText("Pilot School · 2025/2026");

    fireEvent.click(screen.getByRole("link", { name: "Teaching Assignments" }));

    await screen.findByRole("heading", { name: "Teaching Assignments", level: 1 });
    await screen.findByText("Pilot School · 2025/2026");
  });
});
