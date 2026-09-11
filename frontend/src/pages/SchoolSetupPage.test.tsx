import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SchoolSetupPage from "./SchoolSetupPage";
import { getTeachers } from "../api/teachers";
import { getClasses } from "../api/classes";
import { getSubjects } from "../api/subjects";
import { getSpecialActivities } from "../api/specialActivities";
import { getResources } from "../api/resources";
import { getCalendar } from "../api/calendar";
import { loadAppConfig } from "../config/appConfig";
import type { ClassesProjectionResponse, SubjectsProjectionResponse, TeachersProjectionResponse } from "../api/types";
import type { SpecialActivitiesProjectionResponse } from "../api/specialActivities";
import type { ResourcesProjectionResponse } from "../api/resources";
import type { CalendarProjectionResponse } from "../api/calendar";

vi.mock("../api/teachers", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/teachers")>();
  return { ...actual, getTeachers: vi.fn() };
});
vi.mock("../api/classes", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/classes")>();
  return { ...actual, getClasses: vi.fn() };
});
vi.mock("../api/subjects", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/subjects")>();
  return { ...actual, getSubjects: vi.fn() };
});
vi.mock("../api/specialActivities", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/specialActivities")>();
  return { ...actual, getSpecialActivities: vi.fn() };
});
vi.mock("../api/resources", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/resources")>();
  return { ...actual, getResources: vi.fn() };
});
vi.mock("../api/calendar", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/calendar")>();
  return { ...actual, getCalendar: vi.fn() };
});
vi.mock("../config/appConfig", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../config/appConfig")>();
  return { ...actual, loadAppConfig: vi.fn() };
});

const mockedGetTeachers = vi.mocked(getTeachers);
const mockedGetClasses = vi.mocked(getClasses);
const mockedGetSubjects = vi.mocked(getSubjects);
const mockedGetSpecialActivities = vi.mocked(getSpecialActivities);
const mockedGetResources = vi.mocked(getResources);
const mockedGetCalendar = vi.mocked(getCalendar);
const mockedLoadAppConfig = vi.mocked(loadAppConfig);

const TEACHERS_PROJECTION: TeachersProjectionResponse = {
  configuration_locked: false,
  teachers: [{ id: "teacher_1", first_name: "Ada", last_name: "Lovelace", name: "Ada Lovelace" }],
};
const CLASSES_PROJECTION: ClassesProjectionResponse = { configuration_locked: false, classes: [{ id: "class_8a", name: "8-A" }] };
const SUBJECTS_PROJECTION: SubjectsProjectionResponse = {
  configuration_locked: false,
  subjects: [{ id: "activity_math", name: "Mathematics" }],
};
const SPECIAL_ACTIVITIES_PROJECTION: SpecialActivitiesProjectionResponse = {
  configuration_locked: false,
  special_activities: [{ id: "club_debate", name: "Debate Club" }],
};
const RESOURCES_PROJECTION: ResourcesProjectionResponse = {
  configuration_locked: false,
  resources: [{ id: "resource_gym", name: "Gym", capacity: 1 }],
};
const CALENDAR_PROJECTION: CalendarProjectionResponse = {
  configuration_locked: false,
  days: [{ id: "day_mon", name: "Monday", index: 0 }],
  periods: [
    {
      id: "period_1",
      name: "1",
      index: 0,
      start_time: "09:00",
      end_time: "09:40",
      starts_new_block: true,
      is_instructional: true,
    },
  ],
};

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
  mockedGetTeachers.mockResolvedValue(TEACHERS_PROJECTION);
  mockedGetClasses.mockResolvedValue(CLASSES_PROJECTION);
  mockedGetSubjects.mockResolvedValue(SUBJECTS_PROJECTION);
  mockedGetSpecialActivities.mockResolvedValue(SPECIAL_ACTIVITIES_PROJECTION);
  mockedGetResources.mockResolvedValue(RESOURCES_PROJECTION);
  mockedGetCalendar.mockResolvedValue(CALENDAR_PROJECTION);
});

afterEach(() => {
  vi.clearAllMocks();
});

/** Every render needs a Router ancestor now that the page reads
 * `useLocation`/`useNavigate` for the tab-target navigation contract.
 * `initialState` seeds `location.state` exactly the way a real
 * `navigate("/configuration/setup", { state: {...} })` caller would. */
function renderPage(initialState?: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/configuration/setup", state: initialState }]}>
      <SchoolSetupPage />
    </MemoryRouter>,
  );
}

/** Test-only: a sibling `useLocation()` reader, never exported from
 * production code. `SchoolSetupPage` itself only reads `location.state`
 * once, at mount, so it never observably re-renders in response to its
 * own one-shot `navigate(path, {replace:true, state:null})` consuming
 * call -- this probe is what actually proves that call happened, by
 * independently re-rendering (as any real `useLocation()` consumer
 * would) whenever the shared router location changes. */
function LocationStateProbe() {
  const location = useLocation();
  return <div data-testid="location-state-probe">{JSON.stringify(location.state)}</div>;
}

function renderPageWithLocationProbe(initialState?: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/configuration/setup", state: initialState }]}>
      <LocationStateProbe />
      <SchoolSetupPage />
    </MemoryRouter>,
  );
}

describe("SchoolSetupPage", () => {
  it("renders the page heading and subtitle", async () => {
    renderPage();

    expect(screen.getByRole("heading", { name: "School Setup", level: 1 })).toBeInTheDocument();
    expect(
      screen.getByText("Manage the teachers, classes, and subjects used to build the timetable."),
    ).toBeInTheDocument();
  });

  it("defaults to the Teachers tab and loads only the Teachers projection", async () => {
    renderPage();

    await screen.findByText("Ada Lovelace");
    expect(mockedGetTeachers).toHaveBeenCalledTimes(1);
    expect(mockedGetClasses).not.toHaveBeenCalled();
    expect(mockedGetSubjects).not.toHaveBeenCalled();
    expect(mockedGetSpecialActivities).not.toHaveBeenCalled();
    expect(mockedGetResources).not.toHaveBeenCalled();
    expect(mockedGetCalendar).not.toHaveBeenCalled();
  });

  it("switching to Classes loads the Classes projection only at that point, not on initial mount", async () => {
    renderPage();
    await screen.findByText("Ada Lovelace");
    expect(mockedGetClasses).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("tab", { name: "Classes" }));

    await screen.findByText("8-A");
    expect(mockedGetClasses).toHaveBeenCalledTimes(1);
  });

  it("switching to Subjects loads the Subjects projection", async () => {
    renderPage();
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getByRole("tab", { name: "Subjects" }));

    await screen.findByText("Mathematics");
    expect(mockedGetSubjects).toHaveBeenCalledTimes(1);
  });

  it("switching to Special Activities loads the Special Activities projection", async () => {
    renderPage();
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getByRole("tab", { name: "Special Activities" }));

    await screen.findByText("Debate Club");
    expect(mockedGetSpecialActivities).toHaveBeenCalledTimes(1);
  });

  it("switching to Rooms & Resources loads the Resources projection", async () => {
    renderPage();
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getByRole("tab", { name: "Rooms & Resources" }));

    await screen.findByText("Gym");
    expect(mockedGetResources).toHaveBeenCalledTimes(1);
  });

  it("switching to Calendar & Bell Schedule loads only the Calendar projection", async () => {
    renderPage();
    await screen.findByText("Ada Lovelace");
    expect(mockedGetCalendar).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("tab", { name: "Calendar & Bell Schedule" }));

    await screen.findByText("Monday");
    expect(mockedGetCalendar).toHaveBeenCalledTimes(1);
    expect(mockedGetTeachers).toHaveBeenCalledTimes(1);
    expect(mockedGetClasses).not.toHaveBeenCalled();
    expect(mockedGetSubjects).not.toHaveBeenCalled();
    expect(mockedGetSpecialActivities).not.toHaveBeenCalled();
    expect(mockedGetResources).not.toHaveBeenCalled();
  });

  it("shows exactly six tabs in the exact order Teachers, Classes, Subjects, Special Activities, Rooms & Resources, Calendar & Bell Schedule", async () => {
    renderPage();
    await screen.findByText("Ada Lovelace");

    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Teachers",
      "Classes",
      "Subjects",
      "Special Activities",
      "Rooms & Resources",
      "Calendar & Bell Schedule",
    ]);
  });

  it("the inactive panel's content is not present in the document", async () => {
    renderPage();
    await screen.findByText("Ada Lovelace");
    expect(screen.queryByText("8-A")).not.toBeInTheDocument();
    expect(screen.queryByText("Mathematics")).not.toBeInTheDocument();
    expect(screen.queryByText("Debate Club")).not.toBeInTheDocument();
    expect(screen.queryByText("Gym")).not.toBeInTheDocument();
    expect(screen.queryByText("Monday")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Classes" }));
    await screen.findByText("8-A");
    expect(screen.queryByText("Ada Lovelace")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Calendar & Bell Schedule" }));
    await screen.findByText("Monday");
    expect(screen.queryByText("8-A")).not.toBeInTheDocument();
  });

  it("sets correct ARIA state on tabs and the tabpanel", async () => {
    renderPage();
    await screen.findByText("Ada Lovelace");

    const teachersTab = screen.getByRole("tab", { name: "Teachers" });
    const classesTab = screen.getByRole("tab", { name: "Classes" });
    const subjectsTab = screen.getByRole("tab", { name: "Subjects" });

    expect(teachersTab).toHaveAttribute("aria-selected", "true");
    expect(classesTab).toHaveAttribute("aria-selected", "false");
    expect(subjectsTab).toHaveAttribute("aria-selected", "false");
    expect(teachersTab).toHaveAttribute("tabindex", "0");
    expect(classesTab).toHaveAttribute("tabindex", "-1");

    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAttribute("aria-labelledby", teachersTab.id);
    expect(teachersTab).toHaveAttribute("aria-controls", panel.id);

    fireEvent.click(classesTab);
    await screen.findByText("8-A");
    expect(classesTab).toHaveAttribute("aria-selected", "true");
    expect(teachersTab).toHaveAttribute("aria-selected", "false");
  });

  it("ArrowRight/ArrowLeft move focus and activate the adjacent tab, wrapping at the ends", async () => {
    renderPage();
    await screen.findByText("Ada Lovelace");

    const teachersTab = screen.getByRole("tab", { name: "Teachers" });
    const classesTab = screen.getByRole("tab", { name: "Classes" });
    const subjectsTab = screen.getByRole("tab", { name: "Subjects" });
    const specialActivitiesTab = screen.getByRole("tab", { name: "Special Activities" });
    const roomsResourcesTab = screen.getByRole("tab", { name: "Rooms & Resources" });
    const calendarTab = screen.getByRole("tab", { name: "Calendar & Bell Schedule" });

    teachersTab.focus();
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    await screen.findByText("8-A");
    expect(classesTab).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(classesTab);

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    await screen.findByText("Mathematics");
    expect(subjectsTab).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    await screen.findByText("Debate Club");
    expect(specialActivitiesTab).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    await screen.findByText("Gym");
    expect(roomsResourcesTab).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    await screen.findByText("Monday");
    expect(calendarTab).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    await screen.findByText("Ada Lovelace");
    expect(teachersTab).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    await screen.findByText("Monday");
    expect(calendarTab).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(calendarTab);
  });

  it("Home/End jump to the first/last tab", async () => {
    renderPage();
    await screen.findByText("Ada Lovelace");

    const teachersTab = screen.getByRole("tab", { name: "Teachers" });
    const calendarTab = screen.getByRole("tab", { name: "Calendar & Bell Schedule" });

    teachersTab.focus();
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "End" });
    await screen.findByText("Monday");
    expect(calendarTab).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(calendarTab);

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "Home" });
    await screen.findByText("Ada Lovelace");
    expect(teachersTab).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(teachersTab);
  });

  it("remains stable (no crash, no stale data) across repeated tab switches", async () => {
    renderPage();
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getByRole("tab", { name: "Classes" }));
    await screen.findByText("8-A");
    fireEvent.click(screen.getByRole("tab", { name: "Subjects" }));
    await screen.findByText("Mathematics");
    fireEvent.click(screen.getByRole("tab", { name: "Teachers" }));
    await screen.findByText("Ada Lovelace");

    await waitFor(() => {
      expect(mockedGetTeachers).toHaveBeenCalledTimes(2);
      expect(mockedGetClasses).toHaveBeenCalledTimes(1);
      expect(mockedGetSubjects).toHaveBeenCalledTimes(1);
    });
  });

  describe("tab-target navigation (location.state)", () => {
    it("a direct visit with no state defaults to Teachers", async () => {
      renderPage(undefined);
      await screen.findByText("Ada Lovelace");
      expect(screen.getByRole("tab", { name: "Teachers" })).toHaveAttribute("aria-selected", "true");
    });

    it("a reload-shaped visit (null state) defaults to Teachers", async () => {
      renderPage(null);
      await screen.findByText("Ada Lovelace");
      expect(screen.getByRole("tab", { name: "Teachers" })).toHaveAttribute("aria-selected", "true");
    });

    it("requestedTab: 'classes' opens on the Classes tab", async () => {
      renderPage({ requestedTab: "classes" });
      await screen.findByText("8-A");
      expect(screen.getByRole("tab", { name: "Classes" })).toHaveAttribute("aria-selected", "true");
      expect(mockedGetTeachers).not.toHaveBeenCalled();
    });

    it("requestedTab: 'special-activities' opens on the Special Activities tab", async () => {
      renderPage({ requestedTab: "special-activities" });
      await screen.findByText("Debate Club");
      expect(screen.getByRole("tab", { name: "Special Activities" })).toHaveAttribute("aria-selected", "true");
    });

    it("requestedTab: 'rooms-resources' opens on the Rooms & Resources tab", async () => {
      renderPage({ requestedTab: "rooms-resources" });
      await screen.findByText("Gym");
      expect(screen.getByRole("tab", { name: "Rooms & Resources" })).toHaveAttribute("aria-selected", "true");
      expect(mockedGetTeachers).not.toHaveBeenCalled();
    });

    it("requestedTab: 'calendar-bell-schedule' opens on the Calendar & Bell Schedule tab", async () => {
      renderPage({ requestedTab: "calendar-bell-schedule" });
      await screen.findByText("Monday");
      expect(screen.getByRole("tab", { name: "Calendar & Bell Schedule" })).toHaveAttribute("aria-selected", "true");
      expect(mockedGetTeachers).not.toHaveBeenCalled();
    });

    it("an invalid requestedTab value falls back safely to Teachers", async () => {
      renderPage({ requestedTab: "not-a-real-tab" });
      await screen.findByText("Ada Lovelace");
      expect(screen.getByRole("tab", { name: "Teachers" })).toHaveAttribute("aria-selected", "true");
    });

    it("a malformed state object (no requestedTab field) falls back safely to Teachers", async () => {
      renderPage({ somethingElse: true });
      await screen.findByText("Ada Lovelace");
      expect(screen.getByRole("tab", { name: "Teachers" })).toHaveAttribute("aria-selected", "true");
    });

    it("consumes and clears the navigation state so it is not replayed", async () => {
      const { container } = renderPage({ requestedTab: "special-activities" });
      await screen.findByText("Debate Club");

      // The one-shot consumption replaces the current history entry's
      // state with `null` -- normal in-page tab switching afterward
      // must remain fully local and never re-derive from a stale hint.
      fireEvent.click(screen.getByRole("tab", { name: "Teachers" }));
      await screen.findByText("Ada Lovelace");
      expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("Teachers");
    });

    it("actually clears location.state to null after consuming a valid requestedTab (executable proof)", async () => {
      renderPageWithLocationProbe({ requestedTab: "special-activities" });

      // A: the requested tab is activated on mount, from the seeded state.
      await screen.findByText("Debate Club");
      expect(screen.getByRole("tab", { name: "Special Activities" })).toHaveAttribute("aria-selected", "true");

      // B: the sibling `useLocation()` probe -- fully independent of
      // SchoolSetupPage's own render output -- observes the shared
      // router location's `state` field actually become `null`, proving
      // the `navigate(path, {replace:true, state:null})` call inside
      // SchoolSetupPage's consume effect really executed and really
      // replaced the history entry, not merely that some later local
      // click happens to look unaffected.
      await waitFor(() => {
        expect(screen.getByTestId("location-state-probe")).toHaveTextContent("null");
      });
    });
  });
});
