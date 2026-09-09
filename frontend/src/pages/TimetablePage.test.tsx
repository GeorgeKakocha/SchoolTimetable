import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TimetablePage from "./TimetablePage";
import {
  ApiError,
  generateSchedule,
  getClassTimetable,
  getSchedulingConfigIndex,
  getTeacherTimetable,
} from "../api/client";
import { AppConfigError, loadAppConfig } from "../config/appConfig";
import type { ClassTimetableResponse, SchedulingConfigIndexResponse, TeacherTimetableResponse } from "../api/types";

// `../api/client` and `../config/appConfig` are mocked with only their
// network/env-reading functions replaced -- `ApiError`/`AppConfigError`
// stay the REAL classes, so `error instanceof ApiError` etc. inside
// `TimetablePage.tsx` still works against errors constructed here.
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getSchedulingConfigIndex: vi.fn(),
    getClassTimetable: vi.fn(),
    generateSchedule: vi.fn(),
    getTeacherTimetable: vi.fn(),
  };
});

vi.mock("../config/appConfig", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../config/appConfig")>();
  return {
    ...actual,
    loadAppConfig: vi.fn(),
  };
});

const mockedGetSchedulingConfigIndex = vi.mocked(getSchedulingConfigIndex);
const mockedGetClassTimetable = vi.mocked(getClassTimetable);
const mockedGenerateSchedule = vi.mocked(generateSchedule);
const mockedGetTeacherTimetable = vi.mocked(getTeacherTimetable);
const mockedLoadAppConfig = vi.mocked(loadAppConfig);

/** A promise this test controls the resolution/rejection of, to assert
 * intermediate (loading) states and to model out-of-order responses. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const CONFIG_INDEX: SchedulingConfigIndexResponse = {
  school: { id: "s1", name: "Pilot School" },
  academic_year: { id: "y1", label: "2025/2026" },
  class_sections: [
    { id: "8a", name: "8-A" },
    { id: "8b", name: "8-B" },
  ],
  teachers: [
    { id: "t_math", name: "Teacher Math" },
    { id: "t_art", name: "Teacher Art" },
  ],
};

function timetableFor(classId: string, name: string, versionNumber: number): ClassTimetableResponse {
  return {
    school_id: "s1",
    school_name: "Pilot School",
    academic_year_id: "y1",
    academic_year_label: "2025/2026",
    class_section_id: classId,
    class_section_name: name,
    version_number: versionNumber,
    solver_status: "OPTIMAL",
    total_soft_penalty: 0,
    created_at: "2026-01-01T00:00:00Z",
    is_active: true,
    days: [{ id: "mon", name: "Monday" }],
    rows: [
      {
        period_id: "p1",
        period_name: "Period 1",
        cells: [{ day_id: "mon", entries: [] }],
      },
    ],
  };
}

const TIMETABLE_8A = timetableFor("8a", "8-A", 1);
const TIMETABLE_8B = timetableFor("8b", "8-B", 2);

function teacherTimetableFor(
  teacherId: string,
  name: string,
  versionNumber: number,
  rows: TeacherTimetableResponse["rows"] = [
    { period_id: "p1", period_name: "Period 1", cells: [{ day_id: "mon", entries: [] }] },
  ],
): TeacherTimetableResponse {
  return {
    school_id: "s1",
    school_name: "Pilot School",
    academic_year_id: "y1",
    academic_year_label: "2025/2026",
    teacher_id: teacherId,
    teacher_name: name,
    version_number: versionNumber,
    solver_status: "OPTIMAL",
    total_soft_penalty: 0,
    created_at: "2026-01-01T00:00:00Z",
    is_active: true,
    days: [{ id: "mon", name: "Monday" }],
    rows,
  };
}

// The default every existing (class-mode-focused) test implicitly
// relies on: since both the class- and teacher-timetable fetch effects
// now run regardless of which mode is visible, every test needs a safe
// default `getTeacherTimetable` resolution even when it never looks at
// Teacher mode itself -- otherwise the effect would call `.then` on
// `undefined` mid-render and throw. Teacher-mode-focused tests below
// override this per-test via `mockResolvedValueOnce`/`mockRejectedValueOnce`.
const DEFAULT_TEACHER_TIMETABLE = teacherTimetableFor("t_math", "Teacher Math", 1);

function metaText(): string | null {
  return document.querySelector(".timetable-meta")?.textContent ?? null;
}

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
  mockedGetTeacherTimetable.mockResolvedValue(DEFAULT_TEACHER_TIMETABLE);
});

describe("TimetablePage", () => {
  it("shows a loading state while the scheduling config is being fetched", () => {
    const { promise } = deferred<SchedulingConfigIndexResponse>();
    mockedGetSchedulingConfigIndex.mockReturnValue(promise);

    render(<TimetablePage />);

    expect(screen.getByText("Loading configuration…")).toBeInTheDocument();
  });

  it("populates the class selector once the config request succeeds", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);

    render(<TimetablePage />);

    await screen.findByRole("combobox", { name: "Class" });
    const options = screen.getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["8-A", "8-B"]);
  });

  it("selects the first backend-provided class by default", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);

    render(<TimetablePage />);

    const select = await screen.findByRole("combobox", { name: "Class" });
    await waitFor(() => expect(select).toHaveValue("8a"));
  });

  it("requests the first class's timetable using its natural id", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);

    render(<TimetablePage />);

    await waitFor(() =>
      expect(mockedGetClassTimetable).toHaveBeenCalledWith("s1", "y1", "8a", expect.any(AbortSignal)),
    );
  });

  it("no longer renders its own school/year subtitle line, now that the shared shell owns it", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);

    render(<TimetablePage />);

    await waitFor(() => expect(metaText()).toContain(TIMETABLE_8A.class_section_name));

    // Rendered standalone (no `AppShell`), the page itself must not
    // display the school name/year label anywhere -- that text now
    // lives exactly once, in the shared shell (see `App.test.tsx`'s
    // "shared school/year context" test), not duplicated here.
    expect(screen.queryByText(CONFIG_INDEX.school.name)).not.toBeInTheDocument();
    expect(screen.queryByText(CONFIG_INDEX.academic_year.label)).not.toBeInTheDocument();

    expect(metaText()).toContain(TIMETABLE_8A.class_section_name);
    expect(metaText()).toContain(String(TIMETABLE_8A.version_number));
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("requests a new class's timetable when the selector changes", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);

    render(<TimetablePage />);
    const select = await screen.findByRole("combobox", { name: "Class" });

    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8B);
    fireEvent.change(select, { target: { value: "8b" } });

    await waitFor(() =>
      expect(mockedGetClassTimetable).toHaveBeenCalledWith("s1", "y1", "8b", expect.any(AbortSignal)),
    );
  });

  it("does not let a stale/superseded request overwrite the newer selection", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    const first = deferred<ClassTimetableResponse>();
    mockedGetClassTimetable.mockReturnValueOnce(first.promise);

    render(<TimetablePage />);
    const select = await screen.findByRole("combobox", { name: "Class" });

    const second = deferred<ClassTimetableResponse>();
    mockedGetClassTimetable.mockReturnValueOnce(second.promise);
    fireEvent.change(select, { target: { value: "8b" } });

    await act(async () => {
      second.resolve(TIMETABLE_8B);
      await second.promise;
    });
    await waitFor(() => expect(metaText()).toContain(TIMETABLE_8B.class_section_name));

    await act(async () => {
      first.resolve(TIMETABLE_8A);
      await first.promise;
    });

    expect(metaText()).toContain(TIMETABLE_8B.class_section_name);
    expect(metaText()).toContain(String(TIMETABLE_8B.version_number));
    expect(metaText()).not.toContain(TIMETABLE_8A.class_section_name);
  });

  it("shows a loading state for the timetable while a class change is in flight", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);

    render(<TimetablePage />);
    const select = await screen.findByRole("combobox", { name: "Class" });

    const pending = deferred<ClassTimetableResponse>();
    mockedGetClassTimetable.mockReturnValueOnce(pending.promise);
    fireEvent.change(select, { target: { value: "8b" } });

    expect(await screen.findByText("Loading timetable…")).toBeInTheDocument();
    // The selector itself must stay visible/usable during the reload.
    expect(select).toBeInTheDocument();
  });

  it("shows an explicit no-classes state and no selector when none are configured", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue({ ...CONFIG_INDEX, class_sections: [] });

    render(<TimetablePage />);

    await screen.findByText("No classes are configured for this school/year yet.");
    expect(screen.queryByRole("combobox", { name: "Class" })).not.toBeInTheDocument();
  });

  it("shows the frontend config error when the environment is invalid", () => {
    mockedLoadAppConfig.mockImplementation(() => {
      throw new AppConfigError("Missing required frontend configuration value: VITE_SCHOOL_ID.");
    });

    render(<TimetablePage />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Missing required frontend configuration value: VITE_SCHOOL_ID.",
    );
  });

  it("shows a generic config API error", async () => {
    mockedGetSchedulingConfigIndex.mockRejectedValue(new Error("network down"));

    render(<TimetablePage />);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong. Please try again."),
    );
  });

  it("renders a neutral no-schedule state, not a generic error, for 'Active schedule not found'", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValue(new ApiError(404, "Active schedule not found"));

    render(<TimetablePage />);

    await screen.findByText("No schedule has been generated yet for this class.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // The schedule-generation trigger (next product slice) now lives
    // exactly here -- enabled, not an error state of its own.
    expect(screen.getByRole("button", { name: "Generate schedule" })).toBeEnabled();
  });

  it("shows a generic error message for an unrecognized timetable API failure", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValue(new ApiError(500, "Request failed."));

    render(<TimetablePage />);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Request failed."));
  });

  it("surfaces a known safe backend detail verbatim for a config-index error", async () => {
    mockedGetSchedulingConfigIndex.mockRejectedValue(new ApiError(404, "Scheduling configuration not found"));

    render(<TimetablePage />);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Scheduling configuration not found"),
    );
  });
});

// -- Schedule generation (next product slice, product-owner locked) -----
//
// `mockedGenerateSchedule` is reset after every test in this block only
// (the surrounding file's existing tests never call it, and rely on
// their own `mockResolvedValue`/`mockReturnValueOnce` setup instead --
// this scoped reset avoids disturbing that established convention
// while still preventing a queued/uncleared mock from leaking between
// these generation-specific tests).
describe("Schedule generation", () => {
  afterEach(() => {
    mockedGenerateSchedule.mockReset();
  });

  it("[A] shows Generate schedule enabled when there is no active schedule", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValue(new ApiError(404, "Active schedule not found"));

    render(<TimetablePage />);

    expect(await screen.findByRole("button", { name: "Generate schedule" })).toBeEnabled();
  });

  it("[A] shows no Generate action once a timetable is loaded", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);

    render(<TimetablePage />);

    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: /generate/i })).not.toBeInTheDocument();
  });

  it("[A] shows no Generate action in the no-classes or config-error states", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue({ ...CONFIG_INDEX, class_sections: [] });

    render(<TimetablePage />);

    await screen.findByText("No classes are configured for this school/year yet.");
    expect(screen.queryByRole("button", { name: /generate/i })).not.toBeInTheDocument();
  });

  it("[B] clicking Generate issues the exact POST with no signal/body arguments beyond school/year", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValue(new ApiError(404, "Active schedule not found"));
    const { promise, resolve } = deferred<{
      version_number: number;
      solver_status: "OPTIMAL";
      total_soft_penalty: number;
      created_at: string;
      is_active: boolean;
    }>();
    mockedGenerateSchedule.mockReturnValueOnce(promise);

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Generate schedule" }));

    expect(mockedGenerateSchedule).toHaveBeenCalledWith("s1", "y1");
    resolve({ version_number: 1, solver_status: "OPTIMAL", total_soft_penalty: 0, created_at: "2026-01-01T00:00:00Z", is_active: true });
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    await screen.findByRole("table");
  });

  it("[B] disables the button and shows an in-flight label while generation is running, preventing a duplicate POST", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValue(new ApiError(404, "Active schedule not found"));
    const { promise, resolve } = deferred<{
      version_number: number;
      solver_status: "OPTIMAL";
      total_soft_penalty: number;
      created_at: string;
      is_active: boolean;
    }>();
    mockedGenerateSchedule.mockReturnValueOnce(promise);

    render(<TimetablePage />);
    const button = await screen.findByRole("button", { name: "Generate schedule" });
    fireEvent.click(button);

    const inFlightButton = await screen.findByRole("button", { name: "Generating…" });
    expect(inFlightButton).toBeDisabled();
    fireEvent.click(inFlightButton);
    fireEvent.click(inFlightButton);

    expect(mockedGenerateSchedule).toHaveBeenCalledTimes(1);

    resolve({ version_number: 1, solver_status: "OPTIMAL", total_soft_penalty: 0, created_at: "2026-01-01T00:00:00Z", is_active: true });
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    await screen.findByRole("table");
  });

  it("[C] on success, re-fetches the class timetable and renders the grid, with Generate gone and no reload/navigation needed", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValueOnce(new ApiError(404, "Active schedule not found"));
    mockedGenerateSchedule.mockResolvedValueOnce({
      version_number: 1,
      solver_status: "OPTIMAL",
      total_soft_penalty: 0,
      created_at: "2026-01-01T00:00:00Z",
      is_active: true,
    });

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Generate schedule" }));

    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: /generate/i })).not.toBeInTheDocument();
    expect(mockedGetClassTimetable).toHaveBeenCalledWith("s1", "y1", "8a", expect.any(AbortSignal));
  });

  it("[D] treats a stale-browser SCHEDULE_ALREADY_EXISTS as a refresh trigger, not a fatal error", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValueOnce(new ApiError(404, "Active schedule not found"));
    mockedGenerateSchedule.mockRejectedValueOnce(
      new ApiError(409, "A schedule already exists for this school and academic year", "SCHEDULE_ALREADY_EXISTS"),
    );

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Generate schedule" }));

    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    await screen.findByRole("table");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(mockedGetClassTimetable).toHaveBeenCalledWith("s1", "y1", "8a", expect.any(AbortSignal));
  });

  it("[E] shows a retryable inline message for CONFIGURATION_CHANGED_DURING_GENERATION and re-enables the button for another attempt", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValue(new ApiError(404, "Active schedule not found"));
    mockedGenerateSchedule.mockRejectedValueOnce(
      new ApiError(
        409,
        "Scheduling configuration changed during generation; retry generation",
        "CONFIGURATION_CHANGED_DURING_GENERATION",
      ),
    );

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Generate schedule" }));

    await screen.findByText("Scheduling configuration changed during generation. Please try again.");
    const retryButton = screen.getByRole("button", { name: "Generate schedule" });
    expect(retryButton).toBeEnabled();

    mockedGenerateSchedule.mockResolvedValueOnce({
      version_number: 1,
      solver_status: "OPTIMAL",
      total_soft_penalty: 0,
      created_at: "2026-01-01T00:00:00Z",
      is_active: true,
    });
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    fireEvent.click(retryButton);

    expect(mockedGenerateSchedule).toHaveBeenCalledTimes(2);
    await screen.findByRole("table");
  });

  it("[F] renders INVALID_CONFIGURATION diagnostic messages, never raw JSON, and re-enables the button", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValue(new ApiError(404, "Active schedule not found"));
    mockedGenerateSchedule.mockRejectedValueOnce(
      new ApiError(422, "Scheduling configuration is invalid", "INVALID_CONFIGURATION", {
        code: "INVALID_CONFIGURATION",
        errors: [{ code: "SOME_CODE", message: "A specific structural problem was found.", context: {} }],
      }),
    );

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Generate schedule" }));

    await screen.findByText("A specific structural problem was found.");
    expect(screen.queryByText(/"code"/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"context"/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate schedule" })).toBeEnabled();
  });

  it("[F] fails safe (renders nothing extra) for a malformed/unknown diagnostic shape", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValue(new ApiError(404, "Active schedule not found"));
    mockedGenerateSchedule.mockRejectedValueOnce(
      new ApiError(422, "Scheduling configuration is invalid", "INVALID_CONFIGURATION", {
        code: "INVALID_CONFIGURATION",
        errors: [{ unexpected: "shape" }],
      }),
    );

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Generate schedule" }));

    await screen.findByText("Scheduling configuration is invalid");
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("[G] shows a safe inline message for SCHEDULE_INFEASIBLE and re-enables the button", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValue(new ApiError(404, "Active schedule not found"));
    mockedGenerateSchedule.mockRejectedValueOnce(
      new ApiError(
        409,
        "No feasible schedule exists for this school and academic year",
        "SCHEDULE_INFEASIBLE",
      ),
    );

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Generate schedule" }));

    await screen.findByText("No feasible schedule exists for this school and academic year");
    expect(screen.getByRole("button", { name: "Generate schedule" })).toBeEnabled();
  });

  it("[H] shows a safe inline message for a 404 Scheduling configuration not found", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValue(new ApiError(404, "Active schedule not found"));
    mockedGenerateSchedule.mockRejectedValueOnce(new ApiError(404, "Scheduling configuration not found"));

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Generate schedule" }));

    await screen.findByText("Scheduling configuration not found");
    expect(screen.getByRole("button", { name: "Generate schedule" })).toBeEnabled();
  });

  it("[I] shows the generic safe fallback for a network/unexpected failure, and allows retry", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValue(new ApiError(404, "Active schedule not found"));
    mockedGenerateSchedule.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Generate schedule" }));

    await screen.findByText("Something went wrong. Please try again.");
    expect(screen.getByRole("button", { name: "Generate schedule" })).toBeEnabled();
  });
});

// -- Teacher Timetable (next product slice after Phase 3C.3) ------------

const TEACHER_TIMETABLE_WITH_TARGETS: TeacherTimetableResponse = teacherTimetableFor(
  "t_math",
  "Teacher Math",
  1,
  [
    {
      period_id: "p1",
      period_name: "Period 1",
      cells: [
        {
          day_id: "mon",
          entries: [
            {
              source: "REQUIREMENT",
              activity_id: "math",
              activity_name: "Mathematics",
              participant_group_id: "g1",
              participant_group_name: "All of 8-A",
              participant_group_role: "WHOLE_CLASS",
              class_sections: [{ id: "8a", name: "8-A" }],
              requirement_id: "math_8a",
              reserved_block_id: null,
              resource_id: null,
            },
          ],
        },
      ],
    },
    {
      period_id: "p2",
      period_name: "Period 2",
      cells: [
        {
          day_id: "mon",
          entries: [
            {
              source: "REQUIREMENT",
              activity_id: "german",
              activity_name: "German",
              participant_group_id: "g_german",
              participant_group_name: "8-A German",
              participant_group_role: "SUBGROUP",
              class_sections: [{ id: "8a", name: "8-A" }],
              requirement_id: "german_8a",
              reserved_block_id: null,
              resource_id: null,
            },
          ],
        },
      ],
    },
    {
      period_id: "p3",
      period_name: "Period 3",
      cells: [
        {
          day_id: "mon",
          entries: [
            {
              source: "REQUIREMENT",
              activity_id: "history",
              activity_name: "History",
              participant_group_id: "g_merged",
              participant_group_name: "9-A + 9-B Merged History",
              participant_group_role: "MERGED_CLASSES",
              class_sections: [
                { id: "9a", name: "9-A" },
                { id: "9b", name: "9-B" },
              ],
              requirement_id: "history_merged",
              reserved_block_id: null,
              resource_id: null,
            },
          ],
        },
      ],
    },
    {
      period_id: "p4",
      period_name: "Period 4",
      cells: [{ day_id: "mon", entries: [] }],
    },
  ],
);

const ZERO_LOAD_TEACHER_TIMETABLE: TeacherTimetableResponse = teacherTimetableFor("t_art", "Teacher Art", 1);

describe("Teacher mode", () => {
  afterEach(() => {
    mockedGetTeacherTimetable.mockReset();
  });

  // -- A. Mode --

  it("[A] defaults to Class mode, with the Class | Teacher switch visible", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockResolvedValue(DEFAULT_TEACHER_TIMETABLE);

    render(<TimetablePage />);

    await screen.findByRole("combobox", { name: "Class" });
    const classButton = screen.getByRole("button", { name: "Class" });
    const teacherButton = screen.getByRole("button", { name: "Teacher" });
    expect(classButton).toHaveAttribute("aria-pressed", "true");
    expect(teacherButton).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("combobox", { name: "Teacher" })).not.toBeInTheDocument();
  });

  it("[A] switches to Teacher mode and back, Class mode behavior unaffected", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockResolvedValue(DEFAULT_TEACHER_TIMETABLE);

    render(<TimetablePage />);
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("button", { name: "Teacher" }));
    await screen.findByRole("combobox", { name: "Teacher" });
    expect(screen.queryByRole("combobox", { name: "Class" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Class" }));
    await screen.findByRole("combobox", { name: "Class" });
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Teacher" })).not.toBeInTheDocument();
  });

  // -- B. Teacher selector --

  it("[B] populates the teacher selector from the authoritative config teachers, never hard-coded", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockResolvedValue(DEFAULT_TEACHER_TIMETABLE);

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));

    const select = await screen.findByRole("combobox", { name: "Teacher" });
    const options = within(select).getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["Teacher Math", "Teacher Art"]);
  });

  it("[B] selects the first backend-provided teacher by default", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockResolvedValue(DEFAULT_TEACHER_TIMETABLE);

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));

    const select = await screen.findByRole("combobox", { name: "Teacher" });
    await waitFor(() => expect(select).toHaveValue("t_math"));
  });

  it("[B] switching teacher calls the endpoint with the new teacher ID", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockResolvedValue(DEFAULT_TEACHER_TIMETABLE);

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));
    const select = await screen.findByRole("combobox", { name: "Teacher" });

    mockedGetTeacherTimetable.mockResolvedValueOnce(ZERO_LOAD_TEACHER_TIMETABLE);
    fireEvent.change(select, { target: { value: "t_art" } });

    await waitFor(() =>
      expect(mockedGetTeacherTimetable).toHaveBeenCalledWith("s1", "y1", "t_art", expect.any(AbortSignal)),
    );
  });

  it("[B] does not let a stale/superseded teacher response overwrite the newer selection", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    const first = deferred<TeacherTimetableResponse>();
    mockedGetTeacherTimetable.mockReturnValueOnce(first.promise);

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));
    const select = await screen.findByRole("combobox", { name: "Teacher" });

    const second = deferred<TeacherTimetableResponse>();
    mockedGetTeacherTimetable.mockReturnValueOnce(second.promise);
    fireEvent.change(select, { target: { value: "t_art" } });

    await act(async () => {
      second.resolve(ZERO_LOAD_TEACHER_TIMETABLE);
      await second.promise;
    });
    await waitFor(() => expect(metaText()).toContain("Teacher Art"));

    await act(async () => {
      first.resolve(DEFAULT_TEACHER_TIMETABLE);
      await first.promise;
    });

    expect(metaText()).toContain("Teacher Art");
    expect(metaText()).not.toContain("Teacher Math");
  });

  // -- C. Loaded view --

  it("[C] shows the teacher header/name/version", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockResolvedValue(TEACHER_TIMETABLE_WITH_TARGETS);

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));

    await waitFor(() => expect(metaText()).toContain("Teacher Math"));
    expect(metaText()).toContain("Version 1");
  });

  it("[C] renders a WHOLE_CLASS entry with the activity and class name, no role badge", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockResolvedValue(TEACHER_TIMETABLE_WITH_TARGETS);

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));

    await screen.findByText("Mathematics");
    expect(screen.getByText("8-A")).toBeInTheDocument();
  });

  it("[C] renders a SUBGROUP entry with the role badge and participant-group label", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockResolvedValue(TEACHER_TIMETABLE_WITH_TARGETS);

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));

    await screen.findByText("German");
    expect(screen.getByText("8-A German")).toBeInTheDocument();
    expect(screen.getByText("Subgroup")).toBeInTheDocument();
  });

  it("[C] renders a MERGED_CLASSES entry with the role badge and participant-group label", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockResolvedValue(TEACHER_TIMETABLE_WITH_TARGETS);

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));

    await screen.findByText("History");
    expect(screen.getByText("9-A + 9-B Merged History")).toBeInTheDocument();
    expect(screen.getByText("Merged classes")).toBeInTheDocument();
  });

  it("[C] renders a free period as a blank cell, not the text 'Free'", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockResolvedValue(TEACHER_TIMETABLE_WITH_TARGETS);

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));

    await screen.findByRole("table");
    expect(screen.queryByText("Free")).not.toBeInTheDocument();
    expect(screen.queryByText(/free/i)).not.toBeInTheDocument();
  });

  it("[C] a zero-load teacher renders a valid, all-blank grid", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockResolvedValue(ZERO_LOAD_TEACHER_TIMETABLE);

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));

    await waitFor(() => expect(metaText()).toContain("Teacher Art"));
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  // -- D. States --

  it("[D] shows a loading state for the teacher timetable while a teacher change is in flight", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockResolvedValue(DEFAULT_TEACHER_TIMETABLE);

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));
    const select = await screen.findByRole("combobox", { name: "Teacher" });

    const pending = deferred<TeacherTimetableResponse>();
    mockedGetTeacherTimetable.mockReturnValueOnce(pending.promise);
    fireEvent.change(select, { target: { value: "t_art" } });

    expect(await screen.findByText("Loading timetable…")).toBeInTheDocument();
  });

  it("[D] shows an explicit no-teachers state and no selector when none are configured", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue({ ...CONFIG_INDEX, teachers: [] });
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));

    await screen.findByText("No teachers are configured for this school/year yet.");
    expect(screen.queryByRole("combobox", { name: "Teacher" })).not.toBeInTheDocument();
  });

  it("[D] renders a neutral no-schedule state for the teacher, not a generic error", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockRejectedValue(new ApiError(404, "Active schedule not found"));

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));

    await screen.findByText("No schedule has been generated yet.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("[D] shows a safe detail for an unknown teacher", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockRejectedValue(new ApiError(404, "Teacher not found"));

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Teacher not found"));
  });

  it("[D] shows a generic error message for an unrecognized teacher timetable API failure", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockRejectedValue(new ApiError(500, "Request failed."));

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Request failed."));
  });

  // -- E. Regression --

  it("[E] Class mode's selector/grid/Generate behavior is unaffected by Teacher mode existing", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockResolvedValue(DEFAULT_TEACHER_TIMETABLE);

    render(<TimetablePage />);

    const classSelect = await screen.findByRole("combobox", { name: "Class" });
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8B);
    fireEvent.change(classSelect, { target: { value: "8b" } });

    await waitFor(() =>
      expect(mockedGetClassTimetable).toHaveBeenCalledWith("s1", "y1", "8b", expect.any(AbortSignal)),
    );
    await waitFor(() => expect(metaText()).toContain(TIMETABLE_8B.class_section_name));
  });

  it("[E] Teacher mode never shows a Generate control", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);
    mockedGetTeacherTimetable.mockRejectedValue(new ApiError(404, "Active schedule not found"));

    render(<TimetablePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Teacher" }));

    await screen.findByText("No schedule has been generated yet.");
    expect(screen.queryByRole("button", { name: /generate/i })).not.toBeInTheDocument();
  });
});
