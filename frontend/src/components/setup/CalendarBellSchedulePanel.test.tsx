import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CalendarBellSchedulePanel from "./CalendarBellSchedulePanel";
import { ApiError } from "../../api/client";
import {
  createDay,
  createPeriod,
  deleteDay,
  deletePeriod,
  getCalendar,
  moveDay,
  movePeriod,
  updateDay,
  updatePeriod,
} from "../../api/calendar";
import { loadAppConfig } from "../../config/appConfig";
import type { CalendarProjectionResponse } from "../../api/calendar";

vi.mock("../../api/calendar", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/calendar")>();
  return {
    ...actual,
    getCalendar: vi.fn(),
    createDay: vi.fn(),
    updateDay: vi.fn(),
    deleteDay: vi.fn(),
    moveDay: vi.fn(),
    createPeriod: vi.fn(),
    updatePeriod: vi.fn(),
    deletePeriod: vi.fn(),
    movePeriod: vi.fn(),
  };
});

vi.mock("../../config/appConfig", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../config/appConfig")>();
  return { ...actual, loadAppConfig: vi.fn() };
});

const mockedGetCalendar = vi.mocked(getCalendar);
const mockedCreateDay = vi.mocked(createDay);
const mockedUpdateDay = vi.mocked(updateDay);
const mockedDeleteDay = vi.mocked(deleteDay);
const mockedMoveDay = vi.mocked(moveDay);
const mockedCreatePeriod = vi.mocked(createPeriod);
const mockedUpdatePeriod = vi.mocked(updatePeriod);
const mockedDeletePeriod = vi.mocked(deletePeriod);
const mockedMovePeriod = vi.mocked(movePeriod);
const mockedLoadAppConfig = vi.mocked(loadAppConfig);

function bellScheduleSection(): HTMLElement {
  return screen.getByRole("heading", { name: "Bell Schedule" }).closest("section")!;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const TWO_DAYS: CalendarProjectionResponse["days"] = [
  { id: "day_mon", name: "Monday", index: 0 },
  { id: "day_tue", name: "Tuesday", index: 1 },
];

const TWO_PERIODS: CalendarProjectionResponse["periods"] = [
  {
    id: "period_1",
    name: "1",
    index: 0,
    start_time: "09:00",
    end_time: "09:40",
    starts_new_block: true,
    is_instructional: true,
  },
  {
    id: "period_2",
    name: "2",
    index: 1,
    start_time: null,
    end_time: null,
    starts_new_block: false,
    is_instructional: true,
  },
];

const BASE_PROJECTION: CalendarProjectionResponse = {
  configuration_locked: false,
  days: TWO_DAYS,
  periods: TWO_PERIODS,
};

const EMPTY_PROJECTION: CalendarProjectionResponse = { configuration_locked: false, days: [], periods: [] };

const LOCKED_PROJECTION: CalendarProjectionResponse = {
  configuration_locked: true,
  days: TWO_DAYS,
  periods: TWO_PERIODS,
};

const LEGACY_PERIOD: CalendarProjectionResponse["periods"][number] = {
  id: "period_legacy",
  name: "Study Hall",
  index: 2,
  start_time: null,
  end_time: null,
  starts_new_block: false,
  is_instructional: false,
};

const PROJECTION_WITH_LEGACY: CalendarProjectionResponse = {
  configuration_locked: false,
  days: TWO_DAYS,
  periods: [...TWO_PERIODS, LEGACY_PERIOD],
};

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("CalendarBellSchedulePanel", () => {
  describe("Working Days", () => {
    it("shows a loading state, then Days in server order with no raw id/index shown", async () => {
      const { promise, resolve } = deferred<CalendarProjectionResponse>();
      mockedGetCalendar.mockReturnValue(promise);

      render(<CalendarBellSchedulePanel />);
      expect(screen.getByText("Loading calendar…")).toBeInTheDocument();

      resolve(BASE_PROJECTION);
      await screen.findByText("Monday");
      const bodyText = document.body.textContent ?? "";
      expect(bodyText).toContain("Tuesday");
      expect(bodyText).not.toContain("day_mon");
      expect(bodyText).not.toContain("day_tue");
    });

    it("shows the empty state when there are no days", async () => {
      mockedGetCalendar.mockResolvedValue(EMPTY_PROJECTION);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("No working days yet.");
    });

    it("reveals the create form only after clicking + Add day, then sends the exact create request and refetches", async () => {
      mockedGetCalendar.mockResolvedValueOnce(BASE_PROJECTION);
      mockedCreateDay.mockResolvedValue({ id: "day_wed", name: "Wednesday", index: 2 });
      mockedGetCalendar.mockResolvedValueOnce({
        ...BASE_PROJECTION,
        days: [...TWO_DAYS, { id: "day_wed", name: "Wednesday", index: 2 }],
      });

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Monday");

      expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "+ Add day" }));
      fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Wednesday" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(mockedCreateDay).toHaveBeenCalledWith("s1", "y1", { name: "Wednesday" });
      });
      await screen.findByText("Wednesday");
      expect(mockedGetCalendar).toHaveBeenCalledTimes(2);
    });

    it("maps DUPLICATE_DAY to a clear inline message", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      mockedCreateDay.mockRejectedValue(new ApiError(409, "a day named 'Monday' already exists", "DUPLICATE_DAY"));

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Monday");

      fireEvent.click(screen.getByRole("button", { name: "+ Add day" }));
      fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Monday" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await screen.findByText("A day with this name already exists.");
    });

    it("switches a Day row into inline edit mode and sends the exact update request", async () => {
      mockedGetCalendar.mockResolvedValueOnce(BASE_PROJECTION);
      mockedUpdateDay.mockResolvedValue({ id: "day_mon", name: "Monday Assembly", index: 0 });
      mockedGetCalendar.mockResolvedValueOnce({
        ...BASE_PROJECTION,
        days: [{ id: "day_mon", name: "Monday Assembly", index: 0 }, TWO_DAYS[1]!],
      });

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Monday");

      fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);
      const nameInputs = screen.getAllByLabelText("Name");
      fireEvent.change(nameInputs[nameInputs.length - 1]!, { target: { value: "Monday Assembly" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(mockedUpdateDay).toHaveBeenCalledWith("s1", "y1", "day_mon", { name: "Monday Assembly" });
      });
      await screen.findByText("Monday Assembly");
    });

    it("Move Monday up is disabled on the first day; Move Tuesday down is disabled on the last day", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Monday");

      expect(screen.getByRole("button", { name: "Move Monday up" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Move Tuesday down" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Move Monday down" })).not.toBeDisabled();
      expect(screen.getByRole("button", { name: "Move Tuesday up" })).not.toBeDisabled();
    });

    it("Move Tuesday up calls moveDay with 'up' and refetches authoritatively (no optimistic reorder)", async () => {
      mockedGetCalendar.mockResolvedValueOnce(BASE_PROJECTION);
      mockedMoveDay.mockResolvedValue({
        configuration_locked: false,
        days: [TWO_DAYS[1]!, TWO_DAYS[0]!],
        periods: TWO_PERIODS,
      });
      mockedGetCalendar.mockResolvedValueOnce({
        configuration_locked: false,
        days: [{ id: "day_tue", name: "Tuesday", index: 0 }, { id: "day_mon", name: "Monday", index: 1 }],
        periods: TWO_PERIODS,
      });

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Monday");

      fireEvent.click(screen.getByRole("button", { name: "Move Tuesday up" }));

      await waitFor(() => {
        expect(mockedMoveDay).toHaveBeenCalledWith("s1", "y1", "day_tue", "up");
      });
      // The authoritative refresh (a real second GET), not the move response, drives the re-render.
      await waitFor(() => {
        expect(mockedGetCalendar).toHaveBeenCalledTimes(2);
      });
      const rows = screen.getAllByRole("row");
      expect(rows[1]?.textContent).toContain("Tuesday");
      expect(rows[2]?.textContent).toContain("Monday");
    });

    it("delete confirm/cancel: cancel leaves the day intact", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Monday");

      fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
      expect(screen.getByText("Delete Monday?")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

      expect(mockedDeleteDay).not.toHaveBeenCalled();
      expect(screen.queryByText("Delete Monday?")).not.toBeInTheDocument();
    });

    it("delete success removes the row after refetch", async () => {
      mockedGetCalendar.mockResolvedValueOnce(BASE_PROJECTION);
      mockedDeleteDay.mockResolvedValue({ deleted_id: "day_mon" });
      mockedGetCalendar.mockResolvedValueOnce({ ...BASE_PROJECTION, days: [TWO_DAYS[1]!] });

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Monday");

      fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
      fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

      await waitFor(() => {
        expect(screen.queryByText("Monday")).not.toBeInTheDocument();
      });
      expect(screen.getByText("Tuesday")).toBeInTheDocument();
    });

    it("maps the final-Day rejection (NO_CALENDAR_DAYS) to a friendly message", async () => {
      mockedGetCalendar.mockResolvedValue({ ...BASE_PROJECTION, days: [TWO_DAYS[0]!] });
      mockedDeleteDay.mockRejectedValue(
        new ApiError(422, "the Academic Year must retain at least one Day", "INVALID_DAY", {
          errors: [{ code: "NO_CALENDAR_DAYS", message: "the Academic Year must retain at least one Day", context: {} }],
        }),
      );

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Monday");

      fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
      fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

      await screen.findByText("At least one working day is required.");
    });

    it("maps DAY_IN_USE to a friendly message without leaking backend vocabulary", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      mockedDeleteDay.mockRejectedValue(
        new ApiError(409, "Day is referenced elsewhere", "DAY_IN_USE", {
          referenced_by: ["TEACHER_AVAILABILITY", "RESERVED_BLOCK"],
        }),
      );

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Monday");

      fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
      fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

      await screen.findByText("This day can't be deleted because it is used by: Teacher Availability, Reserved Activities.");
      expect(document.body.textContent ?? "").not.toContain("TEACHER_AVAILABILITY");
    });

    it("disables Add/Edit/Delete/Move when configuration is locked, but Days remain visible", async () => {
      mockedGetCalendar.mockResolvedValue(LOCKED_PROJECTION);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Monday");

      expect(screen.getAllByText(/Scheduling configuration is locked/).length).toBeGreaterThan(0);
      expect(screen.getByRole("button", { name: "+ Add day" })).toBeDisabled();
      expect(screen.getAllByRole("button", { name: "Edit" })[0]).toBeDisabled();
      expect(screen.getAllByRole("button", { name: "Delete" })[0]).toBeDisabled();
      expect(screen.getByRole("button", { name: "Move Tuesday up" })).toBeDisabled();
    });

    it("on a lock race during Day move, refetches into the locked state", async () => {
      mockedGetCalendar.mockResolvedValueOnce(BASE_PROJECTION);
      mockedMoveDay.mockRejectedValue(new ApiError(409, "locked", "SCHEDULING_CONFIGURATION_LOCKED"));
      mockedGetCalendar.mockResolvedValueOnce(LOCKED_PROJECTION);

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Monday");

      fireEvent.click(screen.getByRole("button", { name: "Move Tuesday up" }));

      await screen.findByText(/A schedule was generated since this page loaded/);
      await waitFor(() => {
        expect(screen.getAllByText(/Scheduling configuration is locked/).length).toBeGreaterThan(0);
      });
    });
  });

  describe("Bell Schedule", () => {
    it("shows Periods in server order with no raw id/index/block_id and optional times displayed", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      const bodyText = document.body.textContent ?? "";
      expect(bodyText).not.toContain("period_1");
      expect(bodyText).not.toContain("block_0");
      expect(bodyText).not.toContain("block_id");
      const rows = screen.getAllByRole("row");
      const periodTwoRow = rows.find((row) => row.textContent?.includes("2"));
      expect(periodTwoRow?.textContent).toContain("—");
    });

    it("shows the empty state when there are no periods", async () => {
      mockedGetCalendar.mockResolvedValue(EMPTY_PROJECTION);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("No periods yet.");
    });

    it("the first Period is shown as always starting the first block, with no way to turn it off", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      expect(screen.getByText("First period of the day")).toBeInTheDocument();
    });

    it("a later Period with starts_new_block=false shows 'Continues previous block'", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      expect(screen.getByText("Continues previous block")).toBeInTheDocument();
    });

    it("a later Period with starts_new_block=true shows the break-boundary indicator", async () => {
      mockedGetCalendar.mockResolvedValue({
        ...BASE_PROJECTION,
        periods: [TWO_PERIODS[0]!, { ...TWO_PERIODS[1]!, starts_new_block: true }],
      });
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      expect(screen.getByText("Starts a new block after a break")).toBeInTheDocument();
    });

    it("Add period: when the calendar is empty, forces starts_new_block=true and hides the toggle", async () => {
      mockedGetCalendar.mockResolvedValueOnce(EMPTY_PROJECTION);
      mockedCreatePeriod.mockResolvedValue({
        id: "period_1",
        name: "1",
        index: 0,
        start_time: null,
        end_time: null,
        starts_new_block: true,
        is_instructional: true,
      });
      mockedGetCalendar.mockResolvedValueOnce({
        ...EMPTY_PROJECTION,
        periods: [
          { id: "period_1", name: "1", index: 0, start_time: null, end_time: null, starts_new_block: true, is_instructional: true },
        ],
      });

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("No periods yet.");

      fireEvent.click(screen.getByRole("button", { name: "+ Add period" }));
      expect(screen.queryByLabelText("Starts a new block after a break")).not.toBeInTheDocument();
      expect(screen.getByText(/This will be the first period of the day/)).toBeInTheDocument();

      fireEvent.change(screen.getByLabelText("Name"), { target: { value: "1" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(mockedCreatePeriod).toHaveBeenCalledWith("s1", "y1", {
          name: "1",
          start_time: null,
          end_time: null,
          starts_new_block: true,
        });
      });
    });

    it("Add period: sends the exact request with times and starts_new_block, then refetches", async () => {
      mockedGetCalendar.mockResolvedValueOnce(BASE_PROJECTION);
      mockedCreatePeriod.mockResolvedValue({
        id: "period_3",
        name: "3",
        index: 2,
        start_time: "10:00",
        end_time: "10:40",
        starts_new_block: true,
        is_instructional: true,
      });
      mockedGetCalendar.mockResolvedValueOnce({
        ...BASE_PROJECTION,
        periods: [
          ...TWO_PERIODS,
          { id: "period_3", name: "3", index: 2, start_time: "10:00", end_time: "10:40", starts_new_block: true, is_instructional: true },
        ],
      });

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      fireEvent.click(screen.getByRole("button", { name: "+ Add period" }));
      fireEvent.change(screen.getByLabelText("Name"), { target: { value: "3" } });
      fireEvent.change(screen.getByLabelText("Start time"), { target: { value: "10:00" } });
      fireEvent.change(screen.getByLabelText("End time"), { target: { value: "10:40" } });
      fireEvent.click(screen.getByLabelText("Starts a new block after a break"));
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(mockedCreatePeriod).toHaveBeenCalledWith("s1", "y1", {
          name: "3",
          start_time: "10:00",
          end_time: "10:40",
          starts_new_block: true,
        });
      });
      expect(mockedGetCalendar).toHaveBeenCalledTimes(2);
    });

    it("local validation: only one of start/end time disables Save", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      fireEvent.click(screen.getByRole("button", { name: "+ Add period" }));
      fireEvent.change(screen.getByLabelText("Name"), { target: { value: "3" } });
      fireEvent.change(screen.getByLabelText("Start time"), { target: { value: "10:00" } });

      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      expect(mockedCreatePeriod).not.toHaveBeenCalled();
    });

    it("local validation: start >= end disables Save", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      fireEvent.click(screen.getByRole("button", { name: "+ Add period" }));
      fireEvent.change(screen.getByLabelText("Name"), { target: { value: "3" } });
      fireEvent.change(screen.getByLabelText("Start time"), { target: { value: "10:40" } });
      fireEvent.change(screen.getByLabelText("End time"), { target: { value: "10:00" } });

      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    });

    it("editing the first Period shows it forced to start a new block with no toggle", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      fireEvent.click(within(bellScheduleSection()).getAllByRole("button", { name: "Edit" })[0]!);
      expect(screen.getByText(/This is the first period of the day/)).toBeInTheDocument();
      expect(screen.queryByLabelText("Starts a new block after a break")).not.toBeInTheDocument();
    });

    it("editing a later Period exposes an editable starts_new_block toggle and sends it in the update", async () => {
      mockedGetCalendar.mockResolvedValueOnce(BASE_PROJECTION);
      mockedUpdatePeriod.mockResolvedValue({ ...TWO_PERIODS[1]!, starts_new_block: true });
      mockedGetCalendar.mockResolvedValueOnce({
        ...BASE_PROJECTION,
        periods: [TWO_PERIODS[0]!, { ...TWO_PERIODS[1]!, starts_new_block: true }],
      });

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      fireEvent.click(within(bellScheduleSection()).getAllByRole("button", { name: "Edit" })[1]!);
      fireEvent.click(screen.getByLabelText("Starts a new block after a break"));
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(mockedUpdatePeriod).toHaveBeenCalledWith("s1", "y1", "period_2", {
          name: "2",
          start_time: null,
          end_time: null,
          starts_new_block: true,
        });
      });
    });

    it("Move Period 1 up is disabled on the first period; Move Period 2 down is disabled on the last", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      expect(screen.getByRole("button", { name: "Move 1 up" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Move 2 down" })).toBeDisabled();
    });

    it("Move Period 2 up calls movePeriod and refetches authoritatively", async () => {
      mockedGetCalendar.mockResolvedValueOnce(BASE_PROJECTION);
      mockedMovePeriod.mockResolvedValue({ ...BASE_PROJECTION, periods: [TWO_PERIODS[1]!, TWO_PERIODS[0]!] });
      mockedGetCalendar.mockResolvedValueOnce({
        ...BASE_PROJECTION,
        periods: [{ ...TWO_PERIODS[1]!, index: 0 }, { ...TWO_PERIODS[0]!, index: 1 }],
      });

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      fireEvent.click(screen.getByRole("button", { name: "Move 2 up" }));

      await waitFor(() => {
        expect(mockedMovePeriod).toHaveBeenCalledWith("s1", "y1", "period_2", "up");
      });
      await waitFor(() => {
        expect(mockedGetCalendar).toHaveBeenCalledTimes(2);
      });
    });

    it("maps PERIOD_REORDER_BLOCKED_BY_TIME_PREFERENCES to the exact friendly message, without a force option", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      mockedMovePeriod.mockRejectedValue(
        new ApiError(409, "blocked", "PERIOD_REORDER_BLOCKED_BY_TIME_PREFERENCES"),
      );

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      fireEvent.click(screen.getByRole("button", { name: "Move 2 up" }));

      await screen.findByText("Periods cannot be reordered while teaching assignments contain time preferences.");
      expect(screen.queryByText(/force/i)).not.toBeInTheDocument();
    });

    it("delete confirmation for a Period", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      fireEvent.click(within(bellScheduleSection()).getAllByRole("button", { name: "Delete" })[0]!);
      expect(screen.getByText("Delete 1?")).toBeInTheDocument();
    });

    it("maps the final-instructional-Period rejection to a friendly message", async () => {
      mockedGetCalendar.mockResolvedValue({ ...BASE_PROJECTION, periods: [TWO_PERIODS[0]!] });
      mockedDeletePeriod.mockRejectedValue(
        new ApiError(422, "the Academic Year must retain at least one instructional Period", "INVALID_PERIOD", {
          errors: [
            { code: "NO_INSTRUCTIONAL_PERIODS", message: "the Academic Year must retain at least one instructional Period", context: {} },
          ],
        }),
      );

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      fireEvent.click(within(bellScheduleSection()).getAllByRole("button", { name: "Delete" })[0]!);
      fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

      await screen.findByText("At least one lesson period is required.");
    });

    it("maps PERIOD_IN_USE to a friendly message without leaking backend vocabulary", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      mockedDeletePeriod.mockRejectedValue(
        new ApiError(409, "Period is referenced elsewhere", "PERIOD_IN_USE", {
          referenced_by: ["RESERVED_BLOCK"],
        }),
      );

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      fireEvent.click(within(bellScheduleSection()).getAllByRole("button", { name: "Delete" })[0]!);
      fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

      await screen.findByText("This period can't be changed because it is used by: Reserved Activities.");
      expect(document.body.textContent ?? "").not.toContain("RESERVED_BLOCK");
    });

    it("maps a TIME_PREFERENCE-safety PERIOD_IN_USE delete rejection to a friendly, non-technical message", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      mockedDeletePeriod.mockRejectedValue(
        new ApiError(409, "Period is referenced elsewhere", "PERIOD_IN_USE", {
          referenced_by: ["TIME_PREFERENCE"],
        }),
      );

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      fireEvent.click(within(bellScheduleSection()).getAllByRole("button", { name: "Delete" })[0]!);
      fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

      await screen.findByText(
        "This period can't be changed because it is used by: existing time preferences in Teaching Assignments.",
      );
      expect(document.body.textContent ?? "").not.toContain("TIME_PREFERENCE");
    });

    it("disables Add/Edit/Delete/Move for Periods when configuration is locked", async () => {
      mockedGetCalendar.mockResolvedValue(LOCKED_PROJECTION);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      expect(screen.getByRole("button", { name: "+ Add period" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Move 2 up" })).toBeDisabled();
    });

    it("on a successful write whose authoritative refetch fails, shows the stale banner and blocks further mutation", async () => {
      mockedGetCalendar.mockResolvedValueOnce(BASE_PROJECTION);
      mockedCreatePeriod.mockResolvedValue({
        id: "period_3",
        name: "3",
        index: 2,
        start_time: null,
        end_time: null,
        starts_new_block: false,
        is_instructional: true,
      });
      mockedGetCalendar.mockRejectedValueOnce(new ApiError(500, "Request failed."));

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      fireEvent.click(screen.getByRole("button", { name: "+ Add period" }));
      fireEvent.change(screen.getByLabelText("Name"), { target: { value: "3" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await screen.findByText(/could not be refreshed/);
      expect(screen.getByRole("button", { name: "+ Add day" })).toBeDisabled();
    });

    it("prevents double-submit: Save is disabled while a create is in flight", async () => {
      mockedGetCalendar.mockResolvedValue(BASE_PROJECTION);
      const { promise, resolve } = deferred<{
        id: string;
        name: string;
        index: number;
        start_time: string | null;
        end_time: string | null;
        starts_new_block: boolean;
        is_instructional: boolean;
      }>();
      mockedCreatePeriod.mockReturnValue(promise);

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("09:00");

      fireEvent.click(screen.getByRole("button", { name: "+ Add period" }));
      fireEvent.change(screen.getByLabelText("Name"), { target: { value: "3" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
      expect(mockedCreatePeriod).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole("button", { name: "Saving…" }));
      expect(mockedCreatePeriod).toHaveBeenCalledTimes(1);

      resolve({
        id: "period_3",
        name: "3",
        index: 2,
        start_time: null,
        end_time: null,
        starts_new_block: false,
        is_instructional: true,
      });
    });
  });

  describe("legacy non-instructional Periods", () => {
    it("renders a legacy is_instructional=false Period, clearly labeled Non-instructional", async () => {
      mockedGetCalendar.mockResolvedValue(PROJECTION_WITH_LEGACY);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Study Hall");

      expect(screen.getByText("Non-instructional")).toBeInTheDocument();
      expect(screen.getByText("This legacy period is not available for lesson scheduling.")).toBeInTheDocument();
    });

    it("offers no UI toggle to convert a legacy Period to instructional", async () => {
      mockedGetCalendar.mockResolvedValue(PROJECTION_WITH_LEGACY);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Study Hall");

      expect(screen.queryByText(/instructional/i, { selector: "label" })).not.toBeInTheDocument();
      expect(screen.queryByRole("checkbox", { name: /instructional/i })).not.toBeInTheDocument();
    });

    it("editing a legacy Period's name/time never sends an is_instructional field, preserving false", async () => {
      mockedGetCalendar.mockResolvedValueOnce(PROJECTION_WITH_LEGACY);
      mockedUpdatePeriod.mockResolvedValue({ ...LEGACY_PERIOD, name: "Study Hall (renamed)" });
      mockedGetCalendar.mockResolvedValueOnce(PROJECTION_WITH_LEGACY);

      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Study Hall");

      fireEvent.click(within(bellScheduleSection()).getAllByRole("button", { name: "Edit" })[2]!);
      const nameInputs = screen.getAllByLabelText("Name");
      fireEvent.change(nameInputs[nameInputs.length - 1]!, { target: { value: "Study Hall (renamed)" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(mockedUpdatePeriod).toHaveBeenCalledWith("s1", "y1", "period_legacy", {
          name: "Study Hall (renamed)",
          start_time: null,
          end_time: null,
          starts_new_block: false,
        });
      });
      const sentBody = mockedUpdatePeriod.mock.calls[0]?.[3];
      expect(sentBody).not.toHaveProperty("is_instructional");
    });

    it("creating a new Period never offers an instructional-state choice", async () => {
      mockedGetCalendar.mockResolvedValue(PROJECTION_WITH_LEGACY);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Study Hall");

      fireEvent.click(screen.getByRole("button", { name: "+ Add period" }));
      expect(screen.queryByText(/instructional/i, { selector: "label" })).not.toBeInTheDocument();
    });

    it("never renders raw implementation vocabulary (block_id) anywhere on the page", async () => {
      mockedGetCalendar.mockResolvedValue(PROJECTION_WITH_LEGACY);
      render(<CalendarBellSchedulePanel />);
      await screen.findByText("Study Hall");

      expect(document.body.textContent ?? "").not.toContain("block_id");
    });
  });
});
