import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TeachingAssignmentsPage from "./TeachingAssignmentsPage";
import { ApiError } from "../api/client";
import { getTeachingAssignments } from "../api/teachingAssignments";
import { AppConfigError, loadAppConfig } from "../config/appConfig";
import type { TeachingAssignmentsProjectionResponse } from "../api/types";

// `../api/teachingAssignments` and `../config/appConfig` are mocked with
// only their network/env-reading functions replaced -- `ApiError`/
// `AppConfigError` stay the REAL classes, matching `TimetablePage.test.tsx`'s
// existing mocking discipline exactly.
vi.mock("../api/teachingAssignments", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/teachingAssignments")>();
  return {
    ...actual,
    getTeachingAssignments: vi.fn(),
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
    },
  ],
};

const EMPTY_PROJECTION: TeachingAssignmentsProjectionResponse = {
  configuration_locked: false,
  teachers: [{ id: "t1", name: "Ms. Petrova" }],
  teacher_workloads: [{ teacher_id: "t1", teacher_name: "Ms. Petrova", total_weekly_periods: 0 }],
  whole_class_targets: [],
  activities: [],
  assignments: [],
};

function rowFor(activityName: string): HTMLElement {
  return screen.getByText(activityName).closest("tr") as HTMLElement;
}

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
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

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Missing required frontend configuration value: VITE_SCHOOL_ID.",
    );
  });
});
