import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ClassTimetableEditor from "./ClassTimetableEditor";
import { ApiError } from "../api/client";
import {
  getActiveSchedule,
  lockOccurrence,
  moveScheduleEntry,
  reoptimizeSchedule,
  unlockOccurrence,
} from "../api/scheduleEditing";
import type { ActiveScheduleResponse, ClassTimetableResponse } from "../api/types";

vi.mock("../api/scheduleEditing", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/scheduleEditing")>();
  return {
    ...actual,
    getActiveSchedule: vi.fn(),
    moveScheduleEntry: vi.fn(),
    lockOccurrence: vi.fn(),
    unlockOccurrence: vi.fn(),
    reoptimizeSchedule: vi.fn(),
  };
});

const mockedGetActiveSchedule = vi.mocked(getActiveSchedule);
const mockedMove = vi.mocked(moveScheduleEntry);
const mockedLock = vi.mocked(lockOccurrence);
const mockedUnlock = vi.mocked(unlockOccurrence);
const mockedReoptimize = vi.mocked(reoptimizeSchedule);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const EMPTY_ACTIVE: ActiveScheduleResponse = {
  version_number: 1,
  solver_status: "OPTIMAL",
  total_soft_penalty: 0,
  created_at: "2026-01-01T00:00:00Z",
  is_active: true,
  entries: [],
  locked_occurrences: [],
};

const TIMETABLE: ClassTimetableResponse = {
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
  days: [
    { id: "mon", name: "Monday" },
    { id: "tue", name: "Tuesday" },
  ],
  rows: [
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
              teacher_id: "t_math",
              teacher_name: "Teacher Math",
              participant_group_id: "g1",
              participant_group_name: "8-A",
              requirement_id: "math_8a",
              reserved_block_id: null,
              resource_id: null,
            },
          ],
        },
        {
          day_id: "tue",
          entries: [
            {
              source: "REQUIREMENT",
              activity_id: "art",
              activity_name: "Art",
              teacher_id: "t_art",
              teacher_name: "Teacher Art",
              participant_group_id: "g1",
              participant_group_name: "8-A",
              requirement_id: "art_8a",
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
              teacher_id: "t_german",
              teacher_name: "Teacher German",
              participant_group_id: "g_de",
              participant_group_name: "8-A German",
              requirement_id: "german_8a",
              reserved_block_id: null,
              resource_id: null,
            },
            {
              source: "REQUIREMENT",
              activity_id: "russian",
              activity_name: "Russian",
              teacher_id: "t_russian",
              teacher_name: "Teacher Russian",
              participant_group_id: "g_ru",
              participant_group_name: "8-A Russian",
              requirement_id: "russian_8a",
              reserved_block_id: null,
              resource_id: null,
            },
          ],
        },
        {
          day_id: "tue",
          entries: [
            {
              source: "RESERVED_BLOCK",
              activity_id: "club_chess",
              activity_name: "Chess Club",
              teacher_id: null,
              teacher_name: null,
              participant_group_id: null,
              participant_group_name: null,
              requirement_id: null,
              reserved_block_id: "club_chess",
              resource_id: null,
            },
          ],
        },
      ],
    },
  ],
};

beforeEach(() => {
  mockedGetActiveSchedule.mockResolvedValue(EMPTY_ACTIVE);
});

afterEach(() => {
  vi.clearAllMocks();
});

function editPanel(): HTMLElement {
  return document.querySelector(".lesson-edit-panel") as HTMLElement;
}

function renderEditor(timetable: ClassTimetableResponse = TIMETABLE, onMutationSuccess = vi.fn()) {
  const utils = render(
    <ClassTimetableEditor
      schoolId="s1"
      academicYearId="y1"
      timetable={timetable}
      onMutationSuccess={onMutationSuccess}
    />,
  );
  return { ...utils, onMutationSuccess };
}

describe("ClassTimetableEditor", () => {
  it("A: clicking an ordinary lesson opens the edit panel with its details", async () => {
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: /Edit Mathematics/ }));

    expect(screen.getByRole("heading", { name: "Mathematics" })).toBeInTheDocument();
    expect(within(editPanel()).getByText("8-A")).toBeInTheDocument();
    expect(within(editPanel()).getByText("Teacher Math")).toBeInTheDocument();
    expect(within(editPanel()).getByText(/Monday, Period 1/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Move" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lock" })).toBeInTheDocument();
  });

  it("B: a Reserved Activity offers no Move/Lock actions and is not clickable", async () => {
    renderEditor();
    await screen.findByText("Chess Club");

    // Not rendered as a button at all.
    expect(screen.queryByRole("button", { name: /Chess Club/ })).not.toBeInTheDocument();
    expect(screen.getByText("Chess Club").closest("button")).toBeNull();
  });

  it("C: a multi-entry split cell lets the user target the exact occurrence clicked", async () => {
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: /Edit Russian/ }));

    expect(screen.getByRole("heading", { name: "Russian" })).toBeInTheDocument();
    expect(within(editPanel()).getByText("Teacher Russian")).toBeInTheDocument();
    expect(within(editPanel()).queryByText("Teacher German")).not.toBeInTheDocument();
  });

  it("D: entering and cancelling Move mode", async () => {
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: /Edit Mathematics/ }));
    fireEvent.click(screen.getByRole("button", { name: "Move" }));

    expect(screen.getByText(/Choose a destination slot/)).toBeInTheDocument();
    // Target buttons now exist for every instructional cell.
    expect(screen.getByRole("button", { name: /Move to Tuesday, Period 1/ })).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "Cancel" })[0]!);
    expect(screen.queryByText(/Choose a destination slot/)).not.toBeInTheDocument();
    // Selection panel remains open after cancelling target selection.
    expect(screen.getByRole("heading", { name: "Mathematics" })).toBeInTheDocument();
  });

  it("E: move success sends the exact request payload and triggers a refresh", async () => {
    mockedMove.mockResolvedValue({ ...EMPTY_ACTIVE, version_number: 2 });
    const { onMutationSuccess } = renderEditor();

    fireEvent.click(screen.getByRole("button", { name: /Edit Mathematics/ }));
    fireEvent.click(screen.getByRole("button", { name: "Move" }));
    fireEvent.click(screen.getByRole("button", { name: /Move to Tuesday, Period 1/ }));

    const confirmText = document.querySelector(".lesson-edit-panel-confirm")?.textContent ?? "";
    expect(confirmText).toContain("Mathematics");
    expect(confirmText).toContain("Monday, Period 1");
    expect(confirmText).toContain("Tuesday, Period 1");

    fireEvent.click(screen.getByRole("button", { name: "Confirm move" }));

    await waitFor(() => {
      expect(mockedMove).toHaveBeenCalledWith("s1", "y1", {
        base_version_number: 1,
        requirement_id: "math_8a",
        source_day_id: "mon",
        source_period_id: "p1",
        target_day_id: "tue",
        target_period_id: "p1",
      });
    });
    expect(onMutationSuccess).toHaveBeenCalledTimes(1);
  });

  it("F: MOVE_NOT_ALLOWED shows the backend violation reasons and does not call onMutationSuccess", async () => {
    mockedMove.mockRejectedValue(
      new ApiError(409, "move not allowed", "MOVE_NOT_ALLOWED", {
        violations: [{ code: "TEACHER_CONFLICT", message: "Teacher Math is already teaching Art at this time" }],
      }),
    );
    const { onMutationSuccess } = renderEditor();

    fireEvent.click(screen.getByRole("button", { name: /Edit Mathematics/ }));
    fireEvent.click(screen.getByRole("button", { name: "Move" }));
    fireEvent.click(screen.getByRole("button", { name: /Move to Tuesday, Period 1/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm move" }));

    await screen.findByText("Teacher Math is already teaching Art at this time");
    expect(screen.getByText("This move isn't allowed.")).toBeInTheDocument();
    expect(onMutationSuccess).not.toHaveBeenCalled();
  });

  it("G: lock success sends the exact request payload and triggers a refresh", async () => {
    mockedLock.mockResolvedValue({ ...EMPTY_ACTIVE, version_number: 2 });
    const { onMutationSuccess } = renderEditor();

    fireEvent.click(screen.getByRole("button", { name: /Edit Mathematics/ }));
    fireEvent.click(screen.getByRole("button", { name: "Lock" }));

    await waitFor(() => {
      expect(mockedLock).toHaveBeenCalledWith("s1", "y1", {
        base_version_number: 1,
        requirement_id: "math_8a",
        day_id: "mon",
        period_id: "p1",
      });
    });
    expect(onMutationSuccess).toHaveBeenCalledTimes(1);
  });

  it("H: unlock success sends the exact request payload", async () => {
    mockedGetActiveSchedule.mockResolvedValue({
      ...EMPTY_ACTIVE,
      locked_occurrences: [{ requirement_id: "math_8a", day_id: "mon", anchor_period_id: "p1" }],
    });
    mockedUnlock.mockResolvedValue({ ...EMPTY_ACTIVE, version_number: 2 });
    const { onMutationSuccess } = renderEditor();

    await screen.findByText(/Locked/);
    fireEvent.click(screen.getByRole("button", { name: /Edit Mathematics/ }));
    fireEvent.click(screen.getByRole("button", { name: "Unlock" }));

    await waitFor(() => {
      expect(mockedUnlock).toHaveBeenCalledWith("s1", "y1", {
        base_version_number: 1,
        requirement_id: "math_8a",
        day_id: "mon",
        period_id: "p1",
      });
    });
    expect(onMutationSuccess).toHaveBeenCalledTimes(1);
  });

  it("I: a locked occurrence shows a visible locked badge in the grid", async () => {
    mockedGetActiveSchedule.mockResolvedValue({
      ...EMPTY_ACTIVE,
      locked_occurrences: [{ requirement_id: "math_8a", day_id: "mon", anchor_period_id: "p1" }],
    });
    renderEditor();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Edit Mathematics, Teacher Math, locked/ })).toBeInTheDocument();
    });
    // Move is disabled while locked; the hint explains why.
    fireEvent.click(screen.getByRole("button", { name: /Edit Mathematics/ }));
    expect(screen.getByRole("button", { name: "Move" })).toBeDisabled();
    expect(screen.getByText("Unlock this lesson before moving it.")).toBeInTheDocument();
  });

  it("J: re-optimize shows a confirmation before running, and succeeds", async () => {
    mockedReoptimize.mockResolvedValue({ ...EMPTY_ACTIVE, version_number: 2 });
    const { onMutationSuccess } = renderEditor();

    fireEvent.click(screen.getByRole("button", { name: "Re-optimize timetable" }));
    expect(screen.getByText(/keeps every locked lesson fixed/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Confirm re-optimize" }));

    await waitFor(() => {
      expect(mockedReoptimize).toHaveBeenCalledWith("s1", "y1", { base_version_number: 1 });
    });
    expect(onMutationSuccess).toHaveBeenCalledTimes(1);
  });

  it("K: re-optimize infeasible shows the backend message and keeps the timetable unchanged", async () => {
    mockedReoptimize.mockRejectedValue(
      new ApiError(409, "infeasible", "REOPTIMIZATION_INFEASIBLE"),
    );
    const { onMutationSuccess } = renderEditor();

    fireEvent.click(screen.getByRole("button", { name: "Re-optimize timetable" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm re-optimize" }));

    await screen.findByText(/No feasible re-optimized timetable exists/);
    expect(onMutationSuccess).not.toHaveBeenCalled();
    // The grid is still showing the original timetable's content.
    expect(screen.getByRole("button", { name: /Edit Mathematics/ })).toBeInTheDocument();
  });

  it("L: a stale base version notifies the user and triggers a refresh without replaying the mutation", async () => {
    mockedLock.mockRejectedValue(new ApiError(409, "stale", "STALE_SCHEDULE_VERSION"));
    const { onMutationSuccess } = renderEditor();

    fireEvent.click(screen.getByRole("button", { name: /Edit Mathematics/ }));
    fireEvent.click(screen.getByRole("button", { name: "Lock" }));

    await screen.findByText(/timetable changed since this page loaded/);
    expect(mockedLock).toHaveBeenCalledTimes(1);
    expect(onMutationSuccess).toHaveBeenCalledTimes(1);
  });

  it("L (continued): a fresh version prop clears the stale notice and any open selection", async () => {
    mockedLock.mockRejectedValue(new ApiError(409, "stale", "STALE_SCHEDULE_VERSION"));
    const onMutationSuccess = vi.fn();
    const { rerender } = render(
      <ClassTimetableEditor schoolId="s1" academicYearId="y1" timetable={TIMETABLE} onMutationSuccess={onMutationSuccess} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Edit Mathematics/ }));
    fireEvent.click(screen.getByRole("button", { name: "Lock" }));
    await screen.findByText(/timetable changed since this page loaded/);

    rerender(
      <ClassTimetableEditor
        schoolId="s1"
        academicYearId="y1"
        timetable={{ ...TIMETABLE, version_number: 2 }}
        onMutationSuccess={onMutationSuccess}
      />,
    );

    await waitFor(() => {
      expect(screen.queryByText(/timetable changed since this page loaded/)).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("heading", { name: "Mathematics" })).not.toBeInTheDocument();
  });

  it("M: prevents duplicate submission while a move request is pending", async () => {
    const { promise, resolve } = deferred<ActiveScheduleResponse>();
    mockedMove.mockReturnValue(promise);
    renderEditor();

    fireEvent.click(screen.getByRole("button", { name: /Edit Mathematics/ }));
    fireEvent.click(screen.getByRole("button", { name: "Move" }));
    fireEvent.click(screen.getByRole("button", { name: /Move to Tuesday, Period 1/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm move" }));

    expect(screen.getByRole("button", { name: "Moving…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Moving…" }));
    expect(mockedMove).toHaveBeenCalledTimes(1);

    resolve({ ...EMPTY_ACTIVE, version_number: 2 });
    await waitFor(() => expect(mockedMove).toHaveBeenCalledTimes(1));
  });
});
