import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TimetablePage from "./TimetablePage";
import { ApiError, generateSchedule, getClassTimetable, getSchedulingConfigIndex } from "../api/client";
import { AppConfigError, loadAppConfig } from "../config/appConfig";
import type { ClassTimetableResponse, SchedulingConfigIndexResponse } from "../api/types";

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

function metaText(): string | null {
  return document.querySelector(".timetable-meta")?.textContent ?? null;
}

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
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
