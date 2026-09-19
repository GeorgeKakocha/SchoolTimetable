import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TeachingAssignmentsPage from "./TeachingAssignmentsPage";
import { ApiError } from "../api/client";
import {
  createTeachingAssignment,
  deleteTeachingAssignment,
  getTeachingAssignments,
  updateTeachingAssignment,
} from "../api/teachingAssignments";
import { AppConfigError, loadAppConfig } from "../config/appConfig";
import type { TeachingAssignmentsProjectionResponse, ValidationDiagnostic } from "../api/types";

// `../api/teachingAssignments` and `../config/appConfig` are mocked with
// only their network/env-reading functions replaced -- `ApiError`/
// `AppConfigError` stay the REAL classes, matching `TimetablePage.test.tsx`'s
// existing mocking discipline exactly.
vi.mock("../api/teachingAssignments", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/teachingAssignments")>();
  return {
    ...actual,
    getTeachingAssignments: vi.fn(),
    createTeachingAssignment: vi.fn(),
    updateTeachingAssignment: vi.fn(),
    deleteTeachingAssignment: vi.fn(),
  };
});

vi.mock("../config/appConfig", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../config/appConfig")>();
  return {
    ...actual,
    loadAppConfig: vi.fn(),
  };
});

const mockedGetTeachingAssignments = vi.mocked(getTeachingAssignments);
const mockedCreateTeachingAssignment = vi.mocked(createTeachingAssignment);
const mockedUpdateTeachingAssignment = vi.mocked(updateTeachingAssignment);
const mockedDeleteTeachingAssignment = vi.mocked(deleteTeachingAssignment);
const mockedLoadAppConfig = vi.mocked(loadAppConfig);

/** A promise this test controls the resolution/rejection of, to assert
 * the intermediate loading state. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const FULL_PROJECTION: TeachingAssignmentsProjectionResponse = {
  configuration_locked: false,
  teachers: [
    { id: "t1", name: "Ms. Petrova" },
    { id: "t2", name: "Mr. Ivanov" },
  ],
  teacher_workloads: [
    { teacher_id: "t1", teacher_name: "Ms. Petrova", total_weekly_periods: 4 },
    { teacher_id: "t2", teacher_name: "Mr. Ivanov", total_weekly_periods: 0 },
  ],
  whole_class_targets: [
    { class_section_id: "8a", class_section_name: "8-A", participant_group_id: "g1", participant_group_name: "8-A" },
    { class_section_id: "8b", class_section_name: "8-B", participant_group_id: "g4", participant_group_name: "8-B" },
  ],
  activities: [
    { id: "math", name: "Mathematics" },
    { id: "art", name: "Art" },
    { id: "pe", name: "Physical Education" },
    { id: "music", name: "Music" },
    { id: "dance", name: "Dance" },
  ],
  resources: [
    { id: "res1", name: "Gym", capacity: 1 },
    { id: "res2", name: "Science Lab", capacity: 1 },
  ],
  assignments: [
    {
      id: "r1",
      teacher_id: "t1",
      teacher_name: "Ms. Petrova",
      activity_id: "math",
      activity_name: "Mathematics",
      participant_group_id: "g1",
      participant_group_name: "8-A",
      participant_group_role: "WHOLE_CLASS",
      class_sections: [{ id: "8a", name: "8-A" }],
      weekly_periods: 4,
      editable: true,
      advanced_reasons: [],
      resource_id: null,
    },
    {
      id: "r2",
      teacher_id: "t2",
      teacher_name: "Mr. Ivanov",
      activity_id: "art",
      activity_name: "Art",
      participant_group_id: "g2",
      participant_group_name: "8-A Group 1",
      participant_group_role: "SUBGROUP",
      class_sections: [{ id: "8a", name: "8-A" }],
      weekly_periods: 2,
      editable: false,
      advanced_reasons: ["participant_group_role"],
      resource_id: null,
    },
    {
      id: "r3",
      teacher_id: "t1",
      teacher_name: "Ms. Petrova",
      activity_id: "pe",
      activity_name: "Physical Education",
      participant_group_id: "g3",
      participant_group_name: "8-A & 8-B Combined",
      participant_group_role: "MERGED_CLASSES",
      class_sections: [
        { id: "8a", name: "8-A" },
        { id: "8b", name: "8-B" },
      ],
      weekly_periods: 2,
      editable: false,
      advanced_reasons: ["participant_group_role"],
      resource_id: null,
    },
    {
      id: "r4",
      teacher_id: "t2",
      teacher_name: "Mr. Ivanov",
      activity_id: "music",
      activity_name: "Music",
      participant_group_id: "g4",
      participant_group_name: "8-B",
      participant_group_role: "WHOLE_CLASS",
      class_sections: [{ id: "8b", name: "8-B" }],
      weekly_periods: 1,
      editable: false,
      advanced_reasons: ["fixed_placement"],
      resource_id: null,
    },
    {
      id: "r5",
      teacher_id: "t1",
      teacher_name: "Ms. Petrova",
      activity_id: "chem",
      activity_name: "Chemistry",
      participant_group_id: "g5",
      participant_group_name: "8-A",
      participant_group_role: "WHOLE_CLASS",
      class_sections: [{ id: "8a", name: "8-A" }],
      weekly_periods: 3,
      editable: false,
      advanced_reasons: ["distribution_policy", "time_preferences"],
      resource_id: null,
    },
    {
      id: "r6",
      teacher_id: "t1",
      teacher_name: "Ms. Petrova",
      activity_id: "dance",
      activity_name: "Dance",
      participant_group_id: "g4",
      participant_group_name: "8-B",
      participant_group_role: "WHOLE_CLASS",
      class_sections: [{ id: "8b", name: "8-B" }],
      weekly_periods: 2,
      editable: true,
      advanced_reasons: [],
      resource_id: "res1",
    },
  ],
};

const EMPTY_PROJECTION: TeachingAssignmentsProjectionResponse = {
  configuration_locked: false,
  teachers: [{ id: "t1", name: "Ms. Petrova" }],
  teacher_workloads: [{ teacher_id: "t1", teacher_name: "Ms. Petrova", total_weekly_periods: 0 }],
  whole_class_targets: [],
  activities: [],
  resources: [],
  assignments: [],
};

function rowFor(activityName: string): HTMLElement {
  return screen.getByText(activityName).closest("tr") as HTMLElement;
}

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
});

// Every mutation test below queues its own `mockResolvedValueOnce`/
// `mockRejectedValueOnce` sequence on the shared module-level mocks;
// without a reset between tests, an unconsumed queued value (e.g. a
// test that never triggers its own post-mutation refetch) or a stale
// `mock.calls` count leaks into the next test and corrupts it. `vi.fn()`
// instances are recreated once per file by the `vi.mock` factory above,
// not per test, so this reset is required -- `mockClear()` alone does
// not drop queued "Once" implementations, only `mockReset()` does.
afterEach(() => {
  vi.resetAllMocks();
});

describe("TeachingAssignmentsPage", () => {
  it("shows a loading state while the projection is being fetched", () => {
    const { promise } = deferred<TeachingAssignmentsProjectionResponse>();
    mockedGetTeachingAssignments.mockReturnValue(promise);

    render(<TeachingAssignmentsPage />);

    expect(screen.getByText("Loading teaching assignments…")).toBeInTheDocument();
  });

  it("renders the full success projection", async () => {
    mockedGetTeachingAssignments.mockResolvedValue(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);

    await screen.findByRole("heading", { name: "Teaching Assignments", level: 1 });
    expect(mockedGetTeachingAssignments).toHaveBeenCalledWith("s1", "y1", expect.any(AbortSignal));
  });

  it("renders every teacher's workload, including a zero-period teacher", async () => {
    mockedGetTeachingAssignments.mockResolvedValue(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);

    await screen.findByRole("heading", { name: "Teacher workload", level: 2 });
    // Teacher names also appear in the assignments table below, so scope
    // to the workload region specifically rather than a page-wide query.
    // Workload is a row grid (`.workload-row`), not a table -- no <tr>.
    const workload = within(screen.getByRole("region", { name: "Teacher workload" }));
    const petrovaRow = workload.getByText("Ms. Petrova").closest(".workload-row") as HTMLElement;
    expect(within(petrovaRow).getByText("4")).toBeInTheDocument();
    const ivanovRow = workload.getByText("Mr. Ivanov").closest(".workload-row") as HTMLElement;
    expect(within(ivanovRow).getByText("0")).toBeInTheDocument();
  });

  it("renders every assignment row", async () => {
    mockedGetTeachingAssignments.mockResolvedValue(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);

    await screen.findByRole("heading", { name: "Assignments", level: 2 });
    expect(screen.getByText("Mathematics")).toBeInTheDocument();
    expect(screen.getByText("Art")).toBeInTheDocument();
    expect(screen.getByText("Physical Education")).toBeInTheDocument();
    expect(screen.getByText("Music")).toBeInTheDocument();
  });

  it("represents a WHOLE_CLASS assignment by its class section name, standard status", async () => {
    mockedGetTeachingAssignments.mockResolvedValue(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");

    const row = rowFor("Mathematics");
    expect(within(row).getByText("8-A")).toBeInTheDocument();
    expect(within(row).getByText("Standard")).toBeInTheDocument();
    expect(within(row).queryByText("Advanced")).not.toBeInTheDocument();
  });

  it("represents a SUBGROUP assignment with the participant group name and a subgroup indicator", async () => {
    mockedGetTeachingAssignments.mockResolvedValue(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Art");

    const row = rowFor("Art");
    expect(within(row).getByText("8-A Group 1")).toBeInTheDocument();
    expect(within(row).getByText("Subgroup")).toBeInTheDocument();
    expect(within(row).getByText("Advanced")).toBeInTheDocument();
  });

  it("represents a MERGED_CLASSES assignment with the participant group name and a merged indicator", async () => {
    mockedGetTeachingAssignments.mockResolvedValue(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Physical Education");

    const row = rowFor("Physical Education");
    expect(within(row).getByText("8-A & 8-B Combined")).toBeInTheDocument();
    expect(within(row).getByText("Merged classes")).toBeInTheDocument();
    expect(within(row).getByText("Advanced")).toBeInTheDocument();
  });

  it("keeps an advanced row visible with no alert/error role, and shows its friendly reason", async () => {
    mockedGetTeachingAssignments.mockResolvedValue(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Music");

    const row = rowFor("Music");
    expect(within(row).getByText("Advanced")).toBeInTheDocument();
    expect(within(row).getByText("Fixed placement")).toBeInTheDocument();
    expect(within(row).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("maps a non-whole-class target's advanced reason to a friendly label", async () => {
    mockedGetTeachingAssignments.mockResolvedValue(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Art");

    const row = rowFor("Art");
    expect(within(row).getByText("Non-whole-class target")).toBeInTheDocument();
  });

  it("renders multiple advanced reasons as separate chips, not one joined sentence", async () => {
    mockedGetTeachingAssignments.mockResolvedValue(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Chemistry");

    const row = rowFor("Chemistry");
    expect(within(row).getByText("Distribution rule")).toBeInTheDocument();
    expect(within(row).getByText("Preferred time")).toBeInTheDocument();
    expect(within(row).queryByText("Distribution rule, Preferred time")).not.toBeInTheDocument();
    expect(within(row).getAllByText(/Distribution rule|Preferred time/)).toHaveLength(2);
  });

  it("labels the assignments status column Configuration, not Type", async () => {
    mockedGetTeachingAssignments.mockResolvedValue(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);
    await screen.findByRole("table");

    expect(screen.getByRole("columnheader", { name: "Configuration" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Type" })).not.toBeInTheDocument();
  });

  it("shows the lock banner when configuration_locked is true", async () => {
    mockedGetTeachingAssignments.mockResolvedValue({ ...FULL_PROJECTION, configuration_locked: true });

    render(<TeachingAssignmentsPage />);

    await screen.findByText(/read-only because a schedule has already been generated/i);
  });

  it("shows no lock banner when configuration_locked is false", async () => {
    mockedGetTeachingAssignments.mockResolvedValue(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);

    await screen.findByRole("heading", { name: "Teaching Assignments", level: 1 });
    expect(screen.queryByText(/read-only because a schedule has already been generated/i)).not.toBeInTheDocument();
  });

  it("shows an explicit empty state with no assignments, while workload data still renders", async () => {
    mockedGetTeachingAssignments.mockResolvedValue(EMPTY_PROJECTION);

    render(<TeachingAssignmentsPage />);

    await screen.findByText("No teaching assignments configured yet.");
    expect(screen.getByText("Ms. Petrova")).toBeInTheDocument();
  });

  it("shows a page-level 404 error with the backend's safe detail", async () => {
    mockedGetTeachingAssignments.mockRejectedValue(new ApiError(404, "Scheduling configuration not found"));

    render(<TeachingAssignmentsPage />);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Scheduling configuration not found"),
    );
  });

  it("shows a generic error for an unrecognized/network failure", async () => {
    mockedGetTeachingAssignments.mockRejectedValue(new Error("network down"));

    render(<TeachingAssignmentsPage />);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong. Please try again."),
    );
  });

  it("retries the GET when Retry is clicked", async () => {
    mockedGetTeachingAssignments.mockRejectedValueOnce(new Error("network down"));

    render(<TeachingAssignmentsPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    const callsBeforeRetry = mockedGetTeachingAssignments.mock.calls.length;

    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await screen.findByText("Mathematics");
    expect(mockedGetTeachingAssignments.mock.calls.length).toBe(callsBeforeRetry + 1);
  });

  it("shows the frontend config error when the environment is invalid", () => {
    mockedLoadAppConfig.mockImplementation(() => {
      throw new AppConfigError("Missing required frontend configuration value: VITE_SCHOOL_ID.");
    });

    render(<TeachingAssignmentsPage />);

    expect(screen.getByRole("alert")).toHaveTextContent("No active school and academic year selected.");
  });
});

// -- Phase 3C.3b: create/edit/delete mutation UI -------------------------
//
// `r1` (Mathematics, Ms. Petrova, group `g1`) is FULL_PROJECTION's only
// plain/editable `WHOLE_CLASS` row whose `participant_group_id` also
// has a matching `whole_class_targets` entry -- the one row every
// Edit/Delete test below targets. Every other row is either advanced
// (no active controls) or, for the "missing target" test, a
// purpose-built one-off projection.

async function openAddDrawer() {
  fireEvent.click(await screen.findByRole("button", { name: "Add assignment" }));
  return screen.findByRole("dialog", { name: "Add assignment" });
}

async function fillCreateForm(teacherId: string, participantGroupId: string, activityId: string) {
  fireEvent.change(screen.getByRole("combobox", { name: "Teacher" }), { target: { value: teacherId } });
  fireEvent.change(screen.getByRole("combobox", { name: "Class" }), { target: { value: participantGroupId } });
  fireEvent.change(screen.getByRole("combobox", { name: "Activity" }), { target: { value: activityId } });
}

describe("Add assignment", () => {
  it("shows Add assignment enabled when unlocked, and visible but disabled when locked", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    expect(await screen.findByRole("button", { name: "Add assignment" })).toBeEnabled();
  });

  it("shows Add assignment visible but disabled when configuration is locked", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce({ ...FULL_PROJECTION, configuration_locked: true });
    render(<TeachingAssignmentsPage />);
    expect(await screen.findByRole("button", { name: "Add assignment" })).toBeDisabled();
  });

  it("opens an accessible dialog with Teacher/Class/Activity/Weekly periods options from the loaded projection", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await openAddDrawer();

    const teacherSelect = screen.getByRole("combobox", { name: "Teacher" }) as HTMLSelectElement;
    expect(teacherSelect.value).toBe("");
    expect(within(teacherSelect).getByText("Ms. Petrova")).toBeInTheDocument();
    expect(within(teacherSelect).getByText("Mr. Ivanov")).toBeInTheDocument();

    const classSelect = screen.getByRole("combobox", { name: "Class" }) as HTMLSelectElement;
    expect(classSelect.value).toBe("");
    expect(within(classSelect).getByText("8-A")).toBeInTheDocument();
    expect(within(classSelect).getByText("8-B")).toBeInTheDocument();

    const activitySelect = screen.getByRole("combobox", { name: "Activity" }) as HTMLSelectElement;
    expect(activitySelect.value).toBe("");
  });

  it("keeps Save disabled until Teacher/Class/Activity are all selected and weekly_periods is a positive integer", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await openAddDrawer();
    const saveButton = screen.getByRole("button", { name: "Save" });
    expect(saveButton).toBeDisabled();

    fireEvent.change(screen.getByRole("combobox", { name: "Teacher" }), { target: { value: "t1" } });
    expect(saveButton).toBeDisabled();
    await fillCreateForm("t1", "g1", "art");
    expect(saveButton).toBeEnabled(); // weekly periods defaults to "1", already valid

    fireEvent.change(screen.getByRole("spinbutton", { name: "Weekly periods" }), { target: { value: "0" } });
    expect(saveButton).toBeDisabled();
    fireEvent.change(screen.getByRole("spinbutton", { name: "Weekly periods" }), { target: { value: "" } });
    expect(saveButton).toBeDisabled();
    fireEvent.change(screen.getByRole("spinbutton", { name: "Weekly periods" }), { target: { value: "2" } });
    expect(saveButton).toBeEnabled();
  });

  it("submits the exact participant_group_id for the selected class label, never inferred from its name", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedCreateTeachingAssignment.mockResolvedValueOnce({ id: "new1", warnings: [] });
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);
    await openAddDrawer();
    await fillCreateForm("t2", "g4", "art"); // "8-B" is labeled g4 in FULL_PROJECTION
    fireEvent.change(screen.getByRole("spinbutton", { name: "Weekly periods" }), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(mockedCreateTeachingAssignment).toHaveBeenCalledWith("s1", "y1", {
        teacher_id: "t2",
        participant_group_id: "g4",
        activity_id: "art",
        weekly_periods: 3,
        resource_id: null,
      }),
    );
  });

  it("prevents a double submit while the create request is in flight", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    const deferredCreate = deferred<{ id: string; warnings: ValidationDiagnostic[] }>();
    mockedCreateTeachingAssignment.mockReturnValueOnce(deferredCreate.promise);

    render(<TeachingAssignmentsPage />);
    await openAddDrawer();
    await fillCreateForm("t1", "g1", "art");

    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);
    fireEvent.click(saveButton);
    fireEvent.click(saveButton);

    expect(mockedCreateTeachingAssignment).toHaveBeenCalledTimes(1);

    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    deferredCreate.resolve({ id: "new1", warnings: [] });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("closes the drawer and triggers exactly one authoritative refetch on success, without blanking the page, and shows warnings that survive the refresh", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedCreateTeachingAssignment.mockResolvedValueOnce({
      id: "new1",
      warnings: [{ code: "TEACHER_OVERLOADED", message: "Teacher has a high weekly load", context: {} }],
    });
    const deferredRefetch = deferred<TeachingAssignmentsProjectionResponse>();
    mockedGetTeachingAssignments.mockReturnValueOnce(deferredRefetch.promise);

    render(<TeachingAssignmentsPage />);
    await openAddDrawer();
    await fillCreateForm("t1", "g1", "art");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("Mathematics")).toBeInTheDocument();
    expect(screen.queryByText("Loading teaching assignments…")).not.toBeInTheDocument();

    deferredRefetch.resolve(FULL_PROJECTION);

    await waitFor(() =>
      expect(screen.getByText("Assignment saved, but the current configuration has warnings.")).toBeInTheDocument(),
    );
    expect(screen.getByText("Teacher workload is high")).toBeInTheDocument();
  });

  it("shows a 409 duplicate error inline in the drawer, keeping it open", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedCreateTeachingAssignment.mockRejectedValueOnce(
      new ApiError(409, "a teaching assignment already exists", "DUPLICATE_TEACHING_ASSIGNMENT", {
        code: "DUPLICATE_TEACHING_ASSIGNMENT",
        teacher_id: "t1",
        participant_group_id: "g1",
        activity_id: "math",
      }),
    );

    render(<TeachingAssignmentsPage />);
    await openAddDrawer();
    await fillCreateForm("t1", "g1", "math");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("This teacher already has an assignment for this class and activity.");
    expect(screen.getByRole("dialog", { name: "Add assignment" })).toBeInTheDocument();
  });

  it("shows a 422 INVALID_TEACHING_ASSIGNMENT error inline using the diagnostic messages", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedCreateTeachingAssignment.mockRejectedValueOnce(
      new ApiError(422, "invalid teaching assignment", "INVALID_TEACHING_ASSIGNMENT", {
        code: "INVALID_TEACHING_ASSIGNMENT",
        errors: [{ code: "SOME_CODE", message: "A specific structural problem was found.", context: {} }],
      }),
    );

    render(<TeachingAssignmentsPage />);
    await openAddDrawer();
    await fillCreateForm("t1", "g1", "math");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("A specific structural problem was found.");
    expect(screen.getByRole("dialog", { name: "Add assignment" })).toBeInTheDocument();
  });

  it("closes the drawer, shows a transient notice, and ends up locked after a 409 SCHEDULING_CONFIGURATION_LOCKED race", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedCreateTeachingAssignment.mockRejectedValueOnce(
      new ApiError(
        409,
        "Scheduling configuration is locked because a schedule already exists",
        "SCHEDULING_CONFIGURATION_LOCKED",
        { code: "SCHEDULING_CONFIGURATION_LOCKED" },
      ),
    );
    mockedGetTeachingAssignments.mockResolvedValueOnce({ ...FULL_PROJECTION, configuration_locked: true });

    render(<TeachingAssignmentsPage />);
    await openAddDrawer();
    await fillCreateForm("t1", "g1", "math");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await screen.findByText(/A schedule was generated since this page loaded/);

    await waitFor(() =>
      expect(
        screen.getByText(
          "Assignments are read-only because a schedule has already been generated for this configuration.",
        ),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Add assignment" })).toBeDisabled();
  });
});

describe("Edit assignment", () => {
  it("shows an active Edit control only for the plain editable row, not the advanced ones", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");

    expect(within(rowFor("Mathematics")).getByRole("button", { name: "Edit" })).toBeEnabled();
    expect(within(rowFor("Art")).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(within(rowFor("Art")).getByText("Read-only")).toBeInTheDocument();
  });

  it("shows Edit/Delete visibly present but disabled for a plain row when configuration is locked", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce({ ...FULL_PROJECTION, configuration_locked: true });
    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");

    const row = rowFor("Mathematics");
    expect(within(row).getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(within(row).getByRole("button", { name: "Delete" })).toBeDisabled();
    // An advanced row never grows disabled Edit/Delete controls just
    // because the page is also locked -- it stays exactly "Read-only".
    expect(within(rowFor("Art")).getByText("Read-only")).toBeInTheDocument();
    expect(within(rowFor("Art")).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("prepopulates all fields from the target assignment, mapping the class by participant_group_id", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");

    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Edit" }));
    await screen.findByRole("dialog", { name: "Edit assignment" });

    expect(screen.getByRole("combobox", { name: "Teacher" })).toHaveValue("t1");
    expect(screen.getByRole("combobox", { name: "Class" })).toHaveValue("g1");
    expect(screen.getByRole("combobox", { name: "Activity" })).toHaveValue("math");
    expect(screen.getByRole("spinbutton", { name: "Weekly periods" })).toHaveValue(4);
    expect(screen.getByRole("combobox", { name: "Resource" })).toHaveValue("");
  });

  it("prepopulates the Resource select with the assignment's current Resource", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await screen.findByText("Dance");

    fireEvent.click(within(rowFor("Dance")).getByRole("button", { name: "Edit" }));
    await screen.findByRole("dialog", { name: "Edit assignment" });

    expect(screen.getByRole("combobox", { name: "Resource" })).toHaveValue("res1");
  });

  it("PUTs the exact full-replacement body, with the requirement ID only in the URL and never in the body", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedUpdateTeachingAssignment.mockResolvedValueOnce({ id: "r1", warnings: [] });
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");
    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Edit" }));
    await screen.findByRole("dialog", { name: "Edit assignment" });

    fireEvent.change(screen.getByRole("spinbutton", { name: "Weekly periods" }), { target: { value: "6" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(mockedUpdateTeachingAssignment).toHaveBeenCalledWith("s1", "y1", "r1", {
        teacher_id: "t1",
        participant_group_id: "g1",
        activity_id: "math",
        weekly_periods: 6,
        resource_id: null,
      }),
    );
    const [, , , calledBody] = mockedUpdateTeachingAssignment.mock.calls[0] ?? [];
    expect(calledBody).not.toHaveProperty("id");
  });

  it("prevents a double submit while the update request is in flight", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    const deferredUpdate = deferred<{ id: string; warnings: ValidationDiagnostic[] }>();
    mockedUpdateTeachingAssignment.mockReturnValueOnce(deferredUpdate.promise);

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");
    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Edit" }));
    await screen.findByRole("dialog", { name: "Edit assignment" });

    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);
    fireEvent.click(saveButton);

    expect(mockedUpdateTeachingAssignment).toHaveBeenCalledTimes(1);

    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    deferredUpdate.resolve({ id: "r1", warnings: [] });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("fails safe -- no Edit control, no guessed form -- when an editable assignment's participant_group_id is missing from whole_class_targets", async () => {
    const projection: TeachingAssignmentsProjectionResponse = {
      ...FULL_PROJECTION,
      assignments: [
        {
          id: "orphan1",
          teacher_id: "t1",
          teacher_name: "Ms. Petrova",
          activity_id: "math",
          activity_name: "Mathematics",
          participant_group_id: "vanished-group",
          participant_group_name: "8-A",
          participant_group_role: "WHOLE_CLASS",
          class_sections: [{ id: "8a", name: "8-A" }],
          weekly_periods: 4,
          editable: true,
          advanced_reasons: [],
          resource_id: null,
        },
      ],
    };
    mockedGetTeachingAssignments.mockResolvedValueOnce(projection);
    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");

    const row = rowFor("Mathematics");
    expect(within(row).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(within(row).getByText("This assignment can no longer be edited here.")).toBeInTheDocument();
    // Delete never depends on the class-target mapping -- only Edit does.
    expect(within(row).getByRole("button", { name: "Delete" })).toBeEnabled();
  });

  it("shows inline errors on update failure, refetches and ends locked on a lock-race", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedUpdateTeachingAssignment.mockRejectedValueOnce(
      new ApiError(409, "a teaching assignment already exists", "DUPLICATE_TEACHING_ASSIGNMENT", {
        code: "DUPLICATE_TEACHING_ASSIGNMENT",
      }),
    );

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");
    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Edit" }));
    await screen.findByRole("dialog", { name: "Edit assignment" });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("This teacher already has an assignment for this class and activity.");
    expect(screen.getByRole("dialog", { name: "Edit assignment" })).toBeInTheDocument();

    mockedUpdateTeachingAssignment.mockRejectedValueOnce(
      new ApiError(409, "locked", "SCHEDULING_CONFIGURATION_LOCKED", { code: "SCHEDULING_CONFIGURATION_LOCKED" }),
    );
    mockedGetTeachingAssignments.mockResolvedValueOnce({ ...FULL_PROJECTION, configuration_locked: true });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await screen.findByText(/A schedule was generated since this page loaded/);
    await waitFor(() => expect(screen.getByRole("button", { name: "Add assignment" })).toBeDisabled());
  });
});

describe("Resources B1: fixed Resource on an ordinary assignment", () => {
  it("renders the Resource select with 'No resource' as the first option, and every Resource by name, never a raw ID", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await openAddDrawer();

    const resourceSelect = screen.getByRole("combobox", { name: "Resource" }) as HTMLSelectElement;
    const optionLabels = Array.from(resourceSelect.options).map((option) => option.textContent);
    expect(optionLabels[0]).toBe("No resource");
    expect(optionLabels).toContain("Gym");
    expect(optionLabels).toContain("Science Lab");
    expect(resourceSelect.value).toBe("");
    // No raw Resource IDs ever appear as visible option text.
    expect(optionLabels).not.toContain("res1");
    expect(optionLabels).not.toContain("res2");
  });

  it("still permits create/edit when the Resource catalog is empty, offering only 'No resource'", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce({ ...FULL_PROJECTION, resources: [] });
    render(<TeachingAssignmentsPage />);
    await openAddDrawer();

    const resourceSelect = screen.getByRole("combobox", { name: "Resource" }) as HTMLSelectElement;
    expect(resourceSelect.options.length).toBe(1);
    expect(resourceSelect.options[0]?.textContent).toBe("No resource");
    expect(resourceSelect).toBeEnabled();
  });

  it("creates an assignment with a selected Resource", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedCreateTeachingAssignment.mockResolvedValueOnce({ id: "new1", warnings: [] });
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);
    await openAddDrawer();
    await fillCreateForm("t1", "g1", "art");
    fireEvent.change(screen.getByRole("combobox", { name: "Resource" }), { target: { value: "res1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(mockedCreateTeachingAssignment).toHaveBeenCalledWith("s1", "y1", {
        teacher_id: "t1",
        participant_group_id: "g1",
        activity_id: "art",
        weekly_periods: 1,
        resource_id: "res1",
      }),
    );
  });

  it("creates an assignment with no Resource selected, sending resource_id: null explicitly", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedCreateTeachingAssignment.mockResolvedValueOnce({ id: "new1", warnings: [] });
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);
    await openAddDrawer();
    await fillCreateForm("t1", "g1", "art");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(mockedCreateTeachingAssignment).toHaveBeenCalledWith(
        "s1", "y1",
        expect.objectContaining({ resource_id: null }),
      ),
    );
  });

  it("changes an assignment from no Resource to a selected Resource", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedUpdateTeachingAssignment.mockResolvedValueOnce({ id: "r1", warnings: [] });
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");
    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Edit" }));
    await screen.findByRole("dialog", { name: "Edit assignment" });

    fireEvent.change(screen.getByRole("combobox", { name: "Resource" }), { target: { value: "res2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(mockedUpdateTeachingAssignment).toHaveBeenCalledWith(
        "s1", "y1", "r1",
        expect.objectContaining({ resource_id: "res2" }),
      ),
    );
  });

  it("clears an assignment's Resource back to 'No resource'", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedUpdateTeachingAssignment.mockResolvedValueOnce({ id: "r6", warnings: [] });
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Dance");
    fireEvent.click(within(rowFor("Dance")).getByRole("button", { name: "Edit" }));
    await screen.findByRole("dialog", { name: "Edit assignment" });
    expect(screen.getByRole("combobox", { name: "Resource" })).toHaveValue("res1");

    fireEvent.change(screen.getByRole("combobox", { name: "Resource" }), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(mockedUpdateTeachingAssignment).toHaveBeenCalledWith(
        "s1", "y1", "r6",
        expect.objectContaining({ resource_id: null }),
      ),
    );
  });

  it("shows the Resource name in the row when assigned", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await screen.findByText("Dance");

    expect(within(rowFor("Dance")).getByText("Gym")).toBeInTheDocument();
  });

  it("shows 'No resource' in the row when unassigned", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");

    expect(within(rowFor("Mathematics")).getByText("No resource")).toBeInTheDocument();
  });

  it("shows a safe 'Unknown resource' fallback, without exposing the raw ID, and blocks Edit for that row", async () => {
    const projection: TeachingAssignmentsProjectionResponse = {
      ...FULL_PROJECTION,
      assignments: [
        {
          id: "orphan-resource",
          teacher_id: "t1",
          teacher_name: "Ms. Petrova",
          activity_id: "art",
          activity_name: "Art",
          participant_group_id: "g1",
          participant_group_name: "8-A",
          participant_group_role: "WHOLE_CLASS",
          class_sections: [{ id: "8a", name: "8-A" }],
          weekly_periods: 2,
          editable: true,
          advanced_reasons: [],
          resource_id: "vanished-resource",
        },
      ],
    };
    mockedGetTeachingAssignments.mockResolvedValueOnce(projection);
    render(<TeachingAssignmentsPage />);
    await screen.findByText("Art");

    const row = rowFor("Art");
    expect(within(row).getByText("Unknown resource")).toBeInTheDocument();
    expect(within(row).queryByText("vanished-resource")).not.toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(within(row).getByText("This assignment can no longer be edited here.")).toBeInTheDocument();
    // Delete never depends on the resource mapping -- only Edit does.
    expect(within(row).getByRole("button", { name: "Delete" })).toBeEnabled();
  });

  it("shows an active Edit and Delete for an otherwise-plain resource-bearing row", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await screen.findByText("Dance");

    const row = rowFor("Dance");
    expect(within(row).getByRole("button", { name: "Edit" })).toBeEnabled();
    expect(within(row).getByRole("button", { name: "Delete" })).toBeEnabled();
    expect(within(row).queryByText("Read-only")).not.toBeInTheDocument();
  });

  it("disables the Resource select while a submit is in flight, matching every other field", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    const deferredCreate = deferred<{ id: string; warnings: ValidationDiagnostic[] }>();
    mockedCreateTeachingAssignment.mockReturnValueOnce(deferredCreate.promise);

    render(<TeachingAssignmentsPage />);
    await openAddDrawer();
    await fillCreateForm("t1", "g1", "art");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(screen.getByRole("combobox", { name: "Resource" })).toBeDisabled();

    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    deferredCreate.resolve({ id: "new1", warnings: [] });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("Delete assignment", () => {
  it("shows an inline confirmation identifying the assignment on the first Delete click, without deleting", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");

    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Delete" }));

    expect(mockedDeleteTeachingAssignment).not.toHaveBeenCalled();
    const confirmText = within(rowFor("Mathematics")).getByText(/Delete Ms\. Petrova/);
    expect(confirmText.textContent).toContain("Ms. Petrova");
    expect(confirmText.textContent).toContain("8-A");
    expect(confirmText.textContent).toContain("Mathematics");
    expect(within(rowFor("Mathematics")).getByRole("button", { name: "Confirm delete" })).toBeInTheDocument();
  });

  it("does not expose an active Delete control for an advanced row, or a disabled one for a locked plain row", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");
    expect(within(rowFor("Art")).queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(within(rowFor("Mathematics")).getByRole("button", { name: "Delete" })).toBeEnabled();
  });

  it("Cancel returns to the normal controls without deleting", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");
    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Delete" }));
    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Cancel" }));

    expect(mockedDeleteTeachingAssignment).not.toHaveBeenCalled();
    expect(within(rowFor("Mathematics")).getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("issues exactly one DELETE on Confirm (double-confirm prevented), with no request body, then refetches and shows warnings", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    const deferredDelete = deferred<{ deleted_id: string; warnings: ValidationDiagnostic[] }>();
    mockedDeleteTeachingAssignment.mockReturnValueOnce(deferredDelete.promise);

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");
    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Delete" }));
    const confirmButton = within(rowFor("Mathematics")).getByRole("button", { name: "Confirm delete" });
    fireEvent.click(confirmButton);
    fireEvent.click(confirmButton);

    expect(mockedDeleteTeachingAssignment).toHaveBeenCalledTimes(1);
    expect(mockedDeleteTeachingAssignment).toHaveBeenCalledWith("s1", "y1", "r1");

    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    deferredDelete.resolve({
      deleted_id: "r1",
      warnings: [{ code: "CLASS_OCCUPANCY_MISMATCH", message: "Occupancy does not match.", context: {} }],
    });

    await waitFor(() => expect(screen.getByText("Class occupancy mismatch")).toBeInTheDocument());
  });

  it("keeps the confirmation open and shows the error inline on a delete failure", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedDeleteTeachingAssignment.mockRejectedValueOnce(
      new ApiError(409, "not plain/editable", "ADVANCED_REQUIREMENT_NOT_EDITABLE", {
        code: "ADVANCED_REQUIREMENT_NOT_EDITABLE",
        advanced_reasons: ["block_policy"],
      }),
    );

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");
    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Delete" }));
    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Confirm delete" }));

    await screen.findByText("This assignment is no longer plain/editable -- it now has advanced configuration.");
    expect(within(rowFor("Mathematics")).getByRole("button", { name: "Confirm delete" })).toBeInTheDocument();
  });

  it("collapses the confirmation, shows the lock-race notice, and refetches into a locked state on a 409 SCHEDULING_CONFIGURATION_LOCKED", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedDeleteTeachingAssignment.mockRejectedValueOnce(
      new ApiError(409, "locked", "SCHEDULING_CONFIGURATION_LOCKED", { code: "SCHEDULING_CONFIGURATION_LOCKED" }),
    );
    mockedGetTeachingAssignments.mockResolvedValueOnce({ ...FULL_PROJECTION, configuration_locked: true });

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");
    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Delete" }));
    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Confirm delete" }));

    await screen.findByText(/A schedule was generated since this page loaded/);
    await waitFor(() => expect(within(rowFor("Mathematics")).getByRole("button", { name: "Edit" })).toBeDisabled());
    expect(screen.queryByRole("button", { name: "Confirm delete" })).not.toBeInTheDocument();
  });
});

describe("Successful write, failed authoritative refresh (stale-projection safety)", () => {
  it("[A] a create success is never reported as failed when the post-save refresh fails -- old data stays visible, controls disable, Retry recovers", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedCreateTeachingAssignment.mockResolvedValueOnce({ id: "new1", warnings: [] });
    mockedGetTeachingAssignments.mockRejectedValueOnce(new Error("network down"));

    render(<TeachingAssignmentsPage />);
    await openAddDrawer();
    await fillCreateForm("t1", "g1", "art");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await screen.findByText(/could not be refreshed/);
    expect(screen.queryByRole("alert", { name: /save.*fail/i })).not.toBeInTheDocument();
    expect(screen.getByText("Mathematics")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Add assignment" })).toBeDisabled();
    expect(within(rowFor("Mathematics")).getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(within(rowFor("Mathematics")).getByRole("button", { name: "Delete" })).toBeDisabled();

    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(screen.queryByText(/could not be refreshed/)).not.toBeInTheDocument());
    expect(within(rowFor("Mathematics")).getByRole("button", { name: "Edit" })).toBeEnabled();
  });

  it("[B] an update success is never reported as failed when the post-save refresh fails, and Retry recovers", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedUpdateTeachingAssignment.mockResolvedValueOnce({ id: "r1", warnings: [] });
    mockedGetTeachingAssignments.mockRejectedValueOnce(new Error("network down"));

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");
    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Edit" }));
    await screen.findByRole("dialog", { name: "Edit assignment" });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await screen.findByText(/could not be refreshed/);
    expect(screen.getByText("Mathematics")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add assignment" })).toBeDisabled();

    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(screen.queryByText(/could not be refreshed/)).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Add assignment" })).toBeEnabled();
  });

  it("[C] a delete success is never reported as failed when the post-save refresh fails, and Retry recovers", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedDeleteTeachingAssignment.mockResolvedValueOnce({ deleted_id: "r1", warnings: [] });
    mockedGetTeachingAssignments.mockRejectedValueOnce(new Error("network down"));

    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");
    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Delete" }));
    fireEvent.click(within(rowFor("Mathematics")).getByRole("button", { name: "Confirm delete" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "Confirm delete" })).not.toBeInTheDocument());
    await screen.findByText(/could not be refreshed/);
    // The (now possibly-outdated) old row data is still shown -- a
    // failed refresh never means the deleted row vanishes without
    // authoritative confirmation.
    expect(screen.getByText("Mathematics")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add assignment" })).toBeDisabled();

    mockedGetTeachingAssignments.mockResolvedValueOnce({
      ...FULL_PROJECTION,
      assignments: FULL_PROJECTION.assignments.filter((assignment) => assignment.id !== "r1"),
    });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(screen.queryByText("Mathematics")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Add assignment" })).toBeEnabled();
  });
});

describe("Add/Edit drawer accessibility", () => {
  it("has dialog role, aria-modal, and an accessible heading", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    const dialog = await openAddDrawer();
    expect(dialog).toHaveAttribute("aria-modal", "true");
  });

  it("labels every form control", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await openAddDrawer();

    expect(screen.getByRole("combobox", { name: "Teacher" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Class" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Activity" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Weekly periods" })).toBeInTheDocument();
  });

  it("places initial focus inside the drawer on open", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await openAddDrawer();

    await waitFor(() => expect(screen.getByRole("combobox", { name: "Teacher" })).toHaveFocus());
  });

  it("closes on Escape", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await openAddDrawer();

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("closes on the Close button and the Cancel button", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    await openAddDrawer();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await openAddDrawer();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("returns focus to the triggering button when closed", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    const addButton = await screen.findByRole("button", { name: "Add assignment" });
    fireEvent.click(addButton);
    await screen.findByRole("dialog", { name: "Add assignment" });

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(addButton).toHaveFocus());
  });

  it("blocks background interaction with an overlay while open, closing only on a direct overlay click", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    const dialog = await openAddDrawer();

    const overlay = dialog.parentElement as HTMLElement;
    fireEvent.mouseDown(overlay);

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("does not close on a click that originates inside the drawer content", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    render(<TeachingAssignmentsPage />);
    const dialog = await openAddDrawer();

    fireEvent.mouseDown(dialog);

    expect(screen.getByRole("dialog", { name: "Add assignment" })).toBeInTheDocument();
  });

  it("gives a disabled locked control an accessible pointer to the lock explanation", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce({ ...FULL_PROJECTION, configuration_locked: true });
    render(<TeachingAssignmentsPage />);
    await screen.findByText("Mathematics");

    const editButton = within(rowFor("Mathematics")).getByRole("button", { name: "Edit" });
    expect(editButton).toHaveAttribute("aria-describedby", "lock-banner-text");
    expect(document.getElementById("lock-banner-text")).toHaveTextContent(
      "Assignments are read-only because a schedule has already been generated for this configuration.",
    );
  });

  it("uses accessible status semantics for the warning banner, conveying meaning through text, not color alone", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedCreateTeachingAssignment.mockResolvedValueOnce({
      id: "new1",
      warnings: [{ code: "TEACHER_OVERLOADED", message: "Teacher has a high weekly load", context: {} }],
    });
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);

    render(<TeachingAssignmentsPage />);
    await openAddDrawer();
    await fillCreateForm("t1", "g1", "art");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent("Assignment saved, but the current configuration has warnings.");
    expect(banner).toHaveTextContent("Teacher workload is high");
  });

  it("uses alert semantics (not status) for the stale-lock-race notice", async () => {
    mockedGetTeachingAssignments.mockResolvedValueOnce(FULL_PROJECTION);
    mockedCreateTeachingAssignment.mockRejectedValueOnce(
      new ApiError(409, "locked", "SCHEDULING_CONFIGURATION_LOCKED", { code: "SCHEDULING_CONFIGURATION_LOCKED" }),
    );
    mockedGetTeachingAssignments.mockResolvedValueOnce({ ...FULL_PROJECTION, configuration_locked: true });

    render(<TeachingAssignmentsPage />);
    await openAddDrawer();
    await fillCreateForm("t1", "g1", "art");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const notice = await screen.findByText(/A schedule was generated since this page loaded/);
    expect(notice.closest('[role="alert"]')).not.toBeNull();
  });
});
