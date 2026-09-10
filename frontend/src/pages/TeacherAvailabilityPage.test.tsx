import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TeacherAvailabilityPage from "./TeacherAvailabilityPage";
import { ApiError } from "../api/client";
import { getTeacherAvailability, replaceTeacherAvailability } from "../api/teacherAvailability";
import { loadAppConfig } from "../config/appConfig";
import type { TeacherAvailabilityProjectionResponse } from "../api/types";

vi.mock("../api/teacherAvailability", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/teacherAvailability")>();
  return {
    ...actual,
    getTeacherAvailability: vi.fn(),
    replaceTeacherAvailability: vi.fn(),
  };
});

vi.mock("../config/appConfig", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../config/appConfig")>();
  return {
    ...actual,
    loadAppConfig: vi.fn(),
  };
});

const mockedGetTeacherAvailability = vi.mocked(getTeacherAvailability);
const mockedReplaceTeacherAvailability = vi.mocked(replaceTeacherAvailability);
const mockedLoadAppConfig = vi.mocked(loadAppConfig);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <TeacherAvailabilityPage />
    </MemoryRouter>,
  );
}

const BASE_PROJECTION: TeacherAvailabilityProjectionResponse = {
  configuration_locked: false,
  teachers: [
    { id: "teacher_1", name: "Ada Lovelace" },
    { id: "teacher_2", name: "Alan Turing" },
  ],
  days: [
    { id: "mon", name: "Monday", index: 0 },
    { id: "tue", name: "Tuesday", index: 1 },
  ],
  periods: [
    { id: "p1", name: "Period 1", index: 0, block_id: "morning", is_instructional: true },
    { id: "p2", name: "Period 2", index: 1, block_id: "morning", is_instructional: true },
    { id: "break", name: "Lunch", index: 2, block_id: "midday", is_instructional: false },
  ],
  exceptions: [
    { teacher_id: "teacher_1", day_id: "mon", period_id: "p1", status: "UNAVAILABLE" },
    { teacher_id: "teacher_2", day_id: "tue", period_id: "p2", status: "PREFER_NOT" },
  ],
};

const EMPTY_TEACHERS_PROJECTION: TeacherAvailabilityProjectionResponse = {
  configuration_locked: false,
  teachers: [],
  days: BASE_PROJECTION.days,
  periods: BASE_PROJECTION.periods,
  exceptions: [],
};

const LOCKED_PROJECTION: TeacherAvailabilityProjectionResponse = { ...BASE_PROJECTION, configuration_locked: true };

function desktopCell(name: RegExp) {
  return within(screen.getByTestId("availability-desktop-matrix")).getByRole("button", { name });
}

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("TeacherAvailabilityPage", () => {
  it("shows a loading state, then the page", async () => {
    const { promise, resolve } = deferred<TeacherAvailabilityProjectionResponse>();
    mockedGetTeacherAvailability.mockReturnValue(promise);

    renderPage();
    expect(screen.getByText("Loading teacher availability…")).toBeInTheDocument();

    resolve(BASE_PROJECTION);
    await screen.findByLabelText("Teacher");
  });

  it("shows the title and subtitle", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();

    await screen.findByLabelText("Teacher");
    expect(
      screen.getByText("Set when each teacher can, should preferably not, or cannot teach."),
    ).toBeInTheDocument();
  });

  it("auto-selects the first Teacher in authoritative order", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();

    await screen.findByLabelText("Teacher");
    const select = screen.getByLabelText("Teacher") as HTMLSelectElement;
    expect(select.value).toBe("teacher_1");
    const options = within(select).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["Ada Lovelace", "Alan Turing"]);
  });

  it("switching Teacher while clean causes no additional GET", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");
    expect(mockedGetTeacherAvailability).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText("Teacher"), { target: { value: "teacher_2" } });

    expect(mockedGetTeacherAvailability).toHaveBeenCalledTimes(1);
    expect(desktopCell(/Tuesday, Period 2\./).textContent).toContain("Prefer not");
  });

  it("shows the zero-Teacher empty state with a link to School Setup, never an empty matrix", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(EMPTY_TEACHERS_PROJECTION);
    renderPage();

    await screen.findByText("No teachers yet.");
    expect(screen.getByText("Add teachers in School Setup before setting availability.")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Go to School Setup" });
    expect(link).toHaveAttribute("href", "/configuration/setup");
    expect(screen.queryByTestId("availability-desktop-matrix")).not.toBeInTheDocument();
  });

  it("only instructional Periods appear as matrix rows", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");

    const desktop = within(screen.getByTestId("availability-desktop-matrix"));
    expect(desktop.getByText("Period 1")).toBeInTheDocument();
    expect(desktop.getByText("Period 2")).toBeInTheDocument();
    expect(desktop.queryByText("Lunch")).not.toBeInTheDocument();
  });

  it("Days render as desktop column headers, Periods as row headers", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");

    const desktop = within(screen.getByTestId("availability-desktop-matrix"));
    const columnHeaders = desktop.getAllByRole("columnheader").map((h) => h.textContent);
    expect(columnHeaders).toEqual(["Period", "Monday", "Tuesday"]);
    const rowHeaders = desktop.getAllByRole("rowheader").map((h) => h.textContent);
    expect(rowHeaders).toEqual(["Period 1", "Period 2"]);
  });

  it("complete matrix is generated even for a Teacher with zero exceptions", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");

    // teacher_1 has exactly one exception (mon/p1 UNAVAILABLE); every
    // other cell must still render, synthesized as Available.
    expect(desktopCell(/Monday, Period 1\./).textContent).toContain("Unavailable");
    expect(desktopCell(/Monday, Period 2\./).textContent).toContain("Available");
    expect(desktopCell(/Tuesday, Period 1\./).textContent).toContain("Available");
    expect(desktopCell(/Tuesday, Period 2\./).textContent).toContain("Available");
  });

  it("renders PREFER_NOT and UNAVAILABLE cells with their human-readable labels", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");
    fireEvent.change(screen.getByLabelText("Teacher"), { target: { value: "teacher_2" } });

    expect(desktopCell(/Tuesday, Period 2\./).textContent).toContain("Prefer not");
    expect(screen.queryByText("PREFER_NOT")).not.toBeInTheDocument();
    expect(screen.queryByText("UNAVAILABLE")).not.toBeInTheDocument();
  });

  it("cycles Available -> Prefer not -> Unavailable -> Available on repeated activation", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");

    const cell = () => desktopCell(/Monday, Period 2\./);
    expect(cell().textContent).toContain("Available");

    fireEvent.click(cell());
    expect(desktopCell(/Monday, Period 2\./).textContent).toContain("Prefer not");

    fireEvent.click(desktopCell(/Monday, Period 2\./));
    expect(desktopCell(/Monday, Period 2\./).textContent).toContain("Unavailable");

    fireEvent.click(desktopCell(/Monday, Period 2\./));
    expect(desktopCell(/Monday, Period 2\./).textContent).toContain("Available");
  });

  it("cycling a cell back to its authoritative state clears dirty", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");

    const saveButton = screen.getByRole("button", { name: "Save changes" });
    expect(saveButton).toBeDisabled();

    const cellMatcher = () => desktopCell(/Monday, Period 2\./);
    fireEvent.click(cellMatcher());
    expect(saveButton).not.toBeDisabled();

    fireEvent.click(desktopCell(/Monday, Period 2\./));
    fireEvent.click(desktopCell(/Monday, Period 2\./));
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("dynamic accessible name includes Day, Period, current status, and the next action", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");

    const cell = desktopCell(/Monday, Period 1\./);
    expect(cell).toHaveAccessibleName(
      "Monday, Period 1. Current status: Unavailable. Activate to change to Available.",
    );
  });

  it("never uses aria-pressed or aria-checked on the cell control", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");

    const cell = desktopCell(/Monday, Period 1\./);
    expect(cell).not.toHaveAttribute("aria-pressed");
    expect(cell).not.toHaveAttribute("aria-checked");
  });

  it("shows a visible symbol and text together, not color alone", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");

    const cell = desktopCell(/Monday, Period 1\./);
    expect(cell.textContent).toContain("✗");
    expect(cell.textContent).toContain("Unavailable");
  });

  it("shows the dirty hint and disables the Teacher selector while dirty", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");

    expect(screen.queryByText("Save or reset changes before switching teachers.")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Teacher")).not.toBeDisabled();

    fireEvent.click(desktopCell(/Monday, Period 2\./));

    expect(screen.getByText("Save or reset changes before switching teachers.")).toBeInTheDocument();
    expect(screen.getByLabelText("Teacher")).toBeDisabled();
  });

  it("Reset restores the draft to the latest authoritative state and makes no API call", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");

    fireEvent.click(desktopCell(/Monday, Period 1\./));
    expect(desktopCell(/Monday, Period 1\./).textContent).toContain("Available");

    fireEvent.click(screen.getByRole("button", { name: "Reset changes" }));

    expect(desktopCell(/Monday, Period 1\./).textContent).toContain("Unavailable");
    expect(screen.getByLabelText("Teacher")).not.toBeDisabled();
    expect(mockedReplaceTeacherAvailability).not.toHaveBeenCalled();
  });

  it("sends the exact sparse PUT body, containing no AVAILABLE entries", async () => {
    mockedGetTeacherAvailability.mockResolvedValueOnce(BASE_PROJECTION);
    mockedReplaceTeacherAvailability.mockResolvedValue({
      teacher_id: "teacher_1",
      exceptions: [
        { day_id: "mon", period_id: "p1", status: "UNAVAILABLE" },
        { day_id: "mon", period_id: "p2", status: "PREFER_NOT" },
      ],
    });
    mockedGetTeacherAvailability.mockResolvedValueOnce({
      ...BASE_PROJECTION,
      exceptions: [
        ...BASE_PROJECTION.exceptions,
        { teacher_id: "teacher_1", day_id: "mon", period_id: "p2", status: "PREFER_NOT" },
      ],
    });

    renderPage();
    await screen.findByLabelText("Teacher");

    fireEvent.click(desktopCell(/Monday, Period 2\./));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(mockedReplaceTeacherAvailability).toHaveBeenCalledTimes(1);
    });
    const [, , , body] = mockedReplaceTeacherAvailability.mock.calls[0]!;
    expect(body.exceptions).toEqual(
      expect.arrayContaining([
        { day_id: "mon", period_id: "p1", status: "UNAVAILABLE" },
        { day_id: "mon", period_id: "p2", status: "PREFER_NOT" },
      ]),
    );
    expect(body.exceptions.some((e) => (e as { status: string }).status === "AVAILABLE")).toBe(false);
  });

  it("Save is disabled while clean, re-enables once dirty, and disables again after a successful refetch", async () => {
    mockedGetTeacherAvailability.mockResolvedValueOnce(BASE_PROJECTION);
    mockedReplaceTeacherAvailability.mockResolvedValue({ teacher_id: "teacher_1", exceptions: [] });
    mockedGetTeacherAvailability.mockResolvedValueOnce({ ...BASE_PROJECTION, exceptions: [] });

    renderPage();
    await screen.findByLabelText("Teacher");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();

    fireEvent.click(desktopCell(/Monday, Period 1\./));
    expect(screen.getByRole("button", { name: "Save changes" })).not.toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    });
  });

  it("prevents double-submit while saving", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    const { promise, resolve } = deferred<{ teacher_id: string; exceptions: [] }>();
    mockedReplaceTeacherAvailability.mockReturnValue(promise);

    renderPage();
    await screen.findByLabelText("Teacher");

    fireEvent.click(desktopCell(/Monday, Period 1\./));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    fireEvent.click(screen.getByRole("button", { name: "Saving…" }));

    expect(mockedReplaceTeacherAvailability).toHaveBeenCalledTimes(1);
    resolve({ teacher_id: "teacher_1", exceptions: [] });
  });

  it("successful Save triggers an authoritative GET refetch and rebuilds the draft from it", async () => {
    mockedGetTeacherAvailability.mockResolvedValueOnce(BASE_PROJECTION);
    mockedReplaceTeacherAvailability.mockResolvedValue({
      teacher_id: "teacher_1",
      exceptions: [{ day_id: "mon", period_id: "p1", status: "PREFER_NOT" }],
    });
    mockedGetTeacherAvailability.mockResolvedValueOnce({
      ...BASE_PROJECTION,
      exceptions: [
        { teacher_id: "teacher_1", day_id: "mon", period_id: "p1", status: "PREFER_NOT" },
        { teacher_id: "teacher_2", day_id: "tue", period_id: "p2", status: "PREFER_NOT" },
      ],
    });

    renderPage();
    await screen.findByLabelText("Teacher");

    fireEvent.click(desktopCell(/Monday, Period 1\./)); // Unavailable -> Available
    fireEvent.click(desktopCell(/Monday, Period 1\./)); // Available -> Prefer not
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(mockedGetTeacherAvailability).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(desktopCell(/Monday, Period 1\./).textContent).toContain("Prefer not");
    });
  });

  it("an ordinary mutation failure preserves the draft and dirty state, and shows a safe message", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    mockedReplaceTeacherAvailability.mockRejectedValue(
      new ApiError(422, "invalid", "INVALID_TEACHER_AVAILABILITY", {
        errors: [{ code: "UNKNOWN_AVAILABILITY_STATUS", message: "unrecognized availability status" }],
      }),
    );

    renderPage();
    await screen.findByLabelText("Teacher");

    fireEvent.click(desktopCell(/Monday, Period 1\./));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await screen.findByText("unrecognized availability status");
    expect(desktopCell(/Monday, Period 1\./).textContent).toContain("Available");
    expect(screen.getByRole("button", { name: "Save changes" })).not.toBeDisabled();
  });

  it("initial locked state: data readable, controls disabled, lock banner visible", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(LOCKED_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");

    expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    expect(screen.getByLabelText("Teacher")).not.toBeDisabled();
    expect(desktopCell(/Monday, Period 1\./)).toBeDisabled();
    expect(desktopCell(/Monday, Period 1\./).textContent).toContain("Unavailable");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reset changes" })).toBeDisabled();
  });

  it("lock race during Save: discards the stale draft, refetches, and becomes read-only", async () => {
    mockedGetTeacherAvailability.mockResolvedValueOnce(BASE_PROJECTION);
    mockedReplaceTeacherAvailability.mockRejectedValue(
      new ApiError(409, "locked", "SCHEDULING_CONFIGURATION_LOCKED"),
    );
    mockedGetTeacherAvailability.mockResolvedValueOnce(LOCKED_PROJECTION);

    renderPage();
    await screen.findByLabelText("Teacher");

    fireEvent.click(desktopCell(/Monday, Period 1\./));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await screen.findByText(/A schedule was generated since this page loaded/);
    await waitFor(() => {
      expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    });
    expect(desktopCell(/Monday, Period 1\./)).toBeDisabled();
    expect(desktopCell(/Monday, Period 1\./).textContent).toContain("Unavailable");
  });

  it("a 404 Teacher-not-found during Save triggers a stale-client refetch", async () => {
    mockedGetTeacherAvailability.mockResolvedValueOnce(BASE_PROJECTION);
    mockedReplaceTeacherAvailability.mockRejectedValue(new ApiError(404, "Teacher not found"));
    mockedGetTeacherAvailability.mockResolvedValueOnce(BASE_PROJECTION);

    renderPage();
    await screen.findByLabelText("Teacher");

    fireEvent.click(desktopCell(/Monday, Period 1\./));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(mockedGetTeacherAvailability).toHaveBeenCalledTimes(2);
    });
  });

  it("unexpected error falls back to a generic safe message", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    mockedReplaceTeacherAvailability.mockRejectedValue(new Error("network exploded"));

    renderPage();
    await screen.findByLabelText("Teacher");

    fireEvent.click(desktopCell(/Monday, Period 1\./));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await screen.findByText("Something went wrong. Please try again.");
  });

  it("editing one Teacher never mutates another Teacher's draft", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");

    fireEvent.click(desktopCell(/Monday, Period 2\./));
    fireEvent.click(screen.getByRole("button", { name: "Reset changes" }));
    fireEvent.change(screen.getByLabelText("Teacher"), { target: { value: "teacher_2" } });

    expect(desktopCell(/Monday, Period 2\./).textContent).toContain("Available");
    expect(desktopCell(/Tuesday, Period 2\./).textContent).toContain("Prefer not");
  });

  it("both desktop and mobile markup branches exist for the CSS-only responsive toggle", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");

    expect(screen.getByTestId("availability-desktop-matrix")).toBeInTheDocument();
    expect(screen.getByTestId("availability-mobile-days")).toBeInTheDocument();
  });

  it("keyboard activation (Enter) advances the cell state via native button semantics", async () => {
    mockedGetTeacherAvailability.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByLabelText("Teacher");

    const cell = desktopCell(/Monday, Period 2\./);
    cell.focus();
    fireEvent.keyDown(cell, { key: "Enter" });
    fireEvent.click(cell); // jsdom does not synthesize a native click from Enter on <button>
    expect(desktopCell(/Monday, Period 2\./).textContent).toContain("Prefer not");
  });

  it("shows a page-level error with Retry on initial GET failure", async () => {
    mockedGetTeacherAvailability.mockRejectedValueOnce(new ApiError(404, "Scheduling configuration not found"));
    mockedGetTeacherAvailability.mockResolvedValueOnce(BASE_PROJECTION);

    renderPage();
    await screen.findByText("Scheduling configuration not found");

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByLabelText("Teacher");
  });
});

describe("Successful write, failed authoritative refresh (stale-projection safety)", () => {
  it("a Save success is never reported as failed when the post-save refresh fails -- old data stays visible, every mutation control disables, no second PUT is possible, and Retry recovers", async () => {
    mockedGetTeacherAvailability.mockResolvedValueOnce(BASE_PROJECTION);
    mockedReplaceTeacherAvailability.mockResolvedValueOnce({
      teacher_id: "teacher_1",
      exceptions: [{ day_id: "mon", period_id: "p2", status: "PREFER_NOT" }],
    });
    mockedGetTeacherAvailability.mockRejectedValueOnce(new Error("network down"));

    renderPage();
    await screen.findByLabelText("Teacher");

    fireEvent.click(desktopCell(/Monday, Period 2\./));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await screen.findByText(/could not be refreshed/);
    // The PUT itself must never be reported as a failure.
    expect(screen.queryByText("Something went wrong. Please try again.")).not.toBeInTheDocument();
    expect(screen.queryByText(/unrecognized/)).not.toBeInTheDocument();
    // The edited (now stale) data remains visible, not discarded.
    expect(desktopCell(/Monday, Period 2\./).textContent).toContain("Prefer not");

    // Every mutation entry point is disabled while authority is stale.
    expect(desktopCell(/Monday, Period 1\./)).toBeDisabled();
    expect(desktopCell(/Monday, Period 2\./)).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reset changes" })).toBeDisabled();
    expect(screen.getByLabelText("Teacher")).toBeDisabled();

    // Disabled controls cannot trigger a second PUT (the handler's own
    // defensive guard, not just the DOM `disabled` attribute).
    fireEvent.click(desktopCell(/Monday, Period 1\./));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(mockedReplaceTeacherAvailability).toHaveBeenCalledTimes(1);

    mockedGetTeacherAvailability.mockResolvedValueOnce({
      ...BASE_PROJECTION,
      exceptions: [
        { teacher_id: "teacher_1", day_id: "mon", period_id: "p1", status: "UNAVAILABLE" },
        { teacher_id: "teacher_1", day_id: "mon", period_id: "p2", status: "PREFER_NOT" },
        { teacher_id: "teacher_2", day_id: "tue", period_id: "p2", status: "PREFER_NOT" },
      ],
    });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(screen.queryByText(/could not be refreshed/)).not.toBeInTheDocument());
    expect(desktopCell(/Monday, Period 2\./).textContent).toContain("Prefer not");
    // Clean relative to the fresh authoritative state -> both disabled again.
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reset changes" })).toBeDisabled();
    expect(screen.getByLabelText("Teacher")).not.toBeDisabled();
    expect(desktopCell(/Monday, Period 1\./)).not.toBeDisabled();
  });

  it("a lock race whose post-race refresh also fails keeps every mutation control disabled until Retry loads the authoritative locked state", async () => {
    mockedGetTeacherAvailability.mockResolvedValueOnce(BASE_PROJECTION);
    mockedReplaceTeacherAvailability.mockRejectedValue(
      new ApiError(409, "locked", "SCHEDULING_CONFIGURATION_LOCKED"),
    );
    mockedGetTeacherAvailability.mockRejectedValueOnce(new Error("network down"));

    renderPage();
    await screen.findByLabelText("Teacher");

    fireEvent.click(desktopCell(/Monday, Period 1\./));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await screen.findByText(/A schedule was generated since this page loaded/);
    await screen.findByText(/could not be refreshed/);

    expect(desktopCell(/Monday, Period 1\./)).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reset changes" })).toBeDisabled();
    expect(screen.getByLabelText("Teacher")).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(mockedReplaceTeacherAvailability).toHaveBeenCalledTimes(1);

    mockedGetTeacherAvailability.mockResolvedValueOnce(LOCKED_PROJECTION);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(screen.queryByText(/could not be refreshed/)).not.toBeInTheDocument());
    expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    // Locked-but-authoritative: Teacher browsing is allowed again, but
    // every mutation control stays disabled because the configuration
    // itself is locked (not because authority is unresolved).
    expect(screen.getByLabelText("Teacher")).not.toBeDisabled();
    // The stale local edit was discarded in favor of the fresh
    // authoritative baseline once Retry succeeded.
    expect(desktopCell(/Monday, Period 1\./).textContent).toContain("Unavailable");
    expect(desktopCell(/Monday, Period 1\./)).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reset changes" })).toBeDisabled();
  });

  it("a 404 Teacher-not-found during Save whose refetch also fails leaves the page non-mutable via the same shared stale handling", async () => {
    mockedGetTeacherAvailability.mockResolvedValueOnce(BASE_PROJECTION);
    mockedReplaceTeacherAvailability.mockRejectedValue(new ApiError(404, "Teacher not found"));
    mockedGetTeacherAvailability.mockRejectedValueOnce(new Error("network down"));

    renderPage();
    await screen.findByLabelText("Teacher");

    fireEvent.click(desktopCell(/Monday, Period 1\./));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await screen.findByText(/could not be refreshed/);
    expect(desktopCell(/Monday, Period 1\./)).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reset changes" })).toBeDisabled();
    expect(screen.getByLabelText("Teacher")).toBeDisabled();
  });
});
