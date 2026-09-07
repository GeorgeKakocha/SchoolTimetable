import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { ApiError, getClassTimetable, getSchedulingConfigIndex } from "./api/client";
import { AppConfigError, loadAppConfig } from "./config/appConfig";
import type { ClassTimetableResponse, SchedulingConfigIndexResponse } from "./api/types";

// `./api/client` and `./config/appConfig` are mocked with only their
// network/env-reading functions replaced -- `ApiError`/`AppConfigError`
// stay the REAL classes, so `error instanceof ApiError` etc. inside
// `App.tsx` still works against errors constructed here.
vi.mock("./api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/client")>();
  return {
    ...actual,
    getSchedulingConfigIndex: vi.fn(),
    getClassTimetable: vi.fn(),
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

describe("App", () => {
  it("shows a loading state while the scheduling config is being fetched", () => {
    const { promise } = deferred<SchedulingConfigIndexResponse>();
    mockedGetSchedulingConfigIndex.mockReturnValue(promise);

    render(<App />);

    expect(screen.getByText("Loading configuration…")).toBeInTheDocument();
  });

  it("populates the class selector once the config request succeeds", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);

    render(<App />);

    await screen.findByRole("combobox", { name: "Class" });
    const options = screen.getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["8-A", "8-B"]);
  });

  it("selects the first backend-provided class by default", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);

    render(<App />);

    const select = await screen.findByRole("combobox", { name: "Class" });
    await waitFor(() => expect(select).toHaveValue("8a"));
  });

  it("requests the first class's timetable using its natural id", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);

    render(<App />);

    await waitFor(() =>
      expect(mockedGetClassTimetable).toHaveBeenCalledWith("s1", "y1", "8a", expect.any(AbortSignal)),
    );
  });

  it("shows the school name and academic year exactly once each, alongside the loaded class/version", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);

    render(<App />);

    await waitFor(() => expect(metaText()).toContain(TIMETABLE_8A.class_section_name));

    // School/year must appear exactly once each on the page (the config
    // context line), never repeated in the loaded-timetable meta line too.
    const pageText = document.body.textContent ?? "";
    const countOccurrences = (needle: string) => pageText.split(needle).length - 1;
    expect(countOccurrences(CONFIG_INDEX.school.name)).toBe(1);
    expect(countOccurrences(CONFIG_INDEX.academic_year.label)).toBe(1);

    expect(metaText()).toContain(TIMETABLE_8A.class_section_name);
    expect(metaText()).toContain(String(TIMETABLE_8A.version_number));
    expect(metaText()).not.toContain(CONFIG_INDEX.school.name);
    expect(metaText()).not.toContain(CONFIG_INDEX.academic_year.label);
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("requests a new class's timetable when the selector changes", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockResolvedValue(TIMETABLE_8A);

    render(<App />);
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

    render(<App />);
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

    render(<App />);
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

    render(<App />);

    await screen.findByText("No classes are configured for this school/year yet.");
    expect(screen.queryByRole("combobox", { name: "Class" })).not.toBeInTheDocument();
  });

  it("shows the frontend config error when the environment is invalid", () => {
    mockedLoadAppConfig.mockImplementation(() => {
      throw new AppConfigError("Missing required frontend configuration value: VITE_SCHOOL_ID.");
    });

    render(<App />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Missing required frontend configuration value: VITE_SCHOOL_ID.",
    );
  });

  it("shows a generic config API error", async () => {
    mockedGetSchedulingConfigIndex.mockRejectedValue(new Error("network down"));

    render(<App />);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong. Please try again."),
    );
  });

  it("renders a neutral no-schedule state, not a generic error, for 'Active schedule not found'", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValue(new ApiError(404, "Active schedule not found"));

    render(<App />);

    await screen.findByText("No schedule has been generated yet for this class.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /generate/i })).not.toBeInTheDocument();
  });

  it("shows a generic error message for an unrecognized timetable API failure", async () => {
    mockedGetSchedulingConfigIndex.mockResolvedValue(CONFIG_INDEX);
    mockedGetClassTimetable.mockRejectedValue(new ApiError(500, "Request failed."));

    render(<App />);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Request failed."));
  });

  it("surfaces a known safe backend detail verbatim for a config-index error", async () => {
    mockedGetSchedulingConfigIndex.mockRejectedValue(new ApiError(404, "Scheduling configuration not found"));

    render(<App />);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Scheduling configuration not found"),
    );
  });
});
