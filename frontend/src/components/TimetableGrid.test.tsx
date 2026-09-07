import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import TimetableGrid from "./TimetableGrid";
import type { ClassTimetableEntry, ClassTimetableResponse } from "../api/types";

function entry(overrides: Partial<ClassTimetableEntry>): ClassTimetableEntry {
  return {
    source: "REQUIREMENT",
    activity_id: "math",
    activity_name: "Mathematics",
    teacher_id: "t1",
    teacher_name: "Teacher One",
    participant_group_id: "g1",
    participant_group_name: "8-A",
    requirement_id: "req1",
    reserved_block_id: null,
    resource_id: null,
    ...overrides,
  };
}

// Deliberately a non-alphabetical 2x2 shape (not 5x8) to prove no fixed
// calendar-size assumption; day/row order here is the ONLY order the
// component may use.
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
    { id: "tue", name: "Tuesday" },
    { id: "mon", name: "Monday" },
  ],
  rows: [
    {
      period_id: "p2",
      period_name: "Period 2",
      cells: [
        { day_id: "tue", entries: [entry({})] },
        { day_id: "mon", entries: [] },
      ],
    },
    {
      period_id: "p1",
      period_name: "Period 1",
      cells: [
        {
          day_id: "tue",
          entries: [
            entry({
              activity_id: "german",
              activity_name: "German",
              teacher_id: "t_german",
              teacher_name: "Teacher German",
              participant_group_id: "g_german",
              participant_group_name: "8-A German",
              requirement_id: "german_8a",
            }),
            entry({
              activity_id: "russian",
              activity_name: "Russian",
              teacher_id: "t_russian",
              teacher_name: "Teacher Russian",
              participant_group_id: "g_russian",
              participant_group_name: "8-A Russian",
              requirement_id: "russian_8a",
            }),
          ],
        },
        {
          day_id: "mon",
          entries: [
            entry({
              source: "RESERVED_BLOCK",
              activity_id: "club_chess",
              activity_name: "Chess Club",
              teacher_id: null,
              teacher_name: null,
              participant_group_id: null,
              participant_group_name: null,
              requirement_id: null,
              reserved_block_id: "club_chess",
            }),
          ],
        },
      ],
    },
  ],
};

describe("TimetableGrid", () => {
  it("renders days in exactly the supplied (non-alphabetical) order", () => {
    render(<TimetableGrid timetable={TIMETABLE} />);
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Period", "Tuesday", "Monday"]);
  });

  it("renders periods (rows) in exactly the supplied order, not a fixed 5x8 shape", () => {
    render(<TimetableGrid timetable={TIMETABLE} />);
    const rowHeaders = screen.getAllByRole("rowheader").map((h) => h.textContent);
    expect(rowHeaders).toEqual(["Period 2", "Period 1"]);
  });

  it("renders an ordinary one-entry cell with activity/group/teacher", () => {
    render(<TimetableGrid timetable={TIMETABLE} />);
    const cells = screen.getAllByRole("cell");
    const ordinaryCell = cells[0];
    expect(ordinaryCell).toHaveTextContent("Mathematics");
    expect(ordinaryCell).toHaveTextContent("8-A");
    expect(ordinaryCell).toHaveTextContent("Teacher One");
  });

  it("allows an empty cell with no entries", () => {
    render(<TimetableGrid timetable={TIMETABLE} />);
    const cells = screen.getAllByRole("cell");
    expect(cells[1]?.textContent).toBe("");
  });

  it("renders a split cell with BOTH parallel entries distinctly, never merged", () => {
    render(<TimetableGrid timetable={TIMETABLE} />);
    const cells = screen.getAllByRole("cell");
    const splitCell = cells[2];
    expect(splitCell).toHaveTextContent("German");
    expect(splitCell).toHaveTextContent("8-A German");
    expect(splitCell).toHaveTextContent("Teacher German");
    expect(splitCell).toHaveTextContent("Russian");
    expect(splitCell).toHaveTextContent("8-A Russian");
    expect(splitCell).toHaveTextContent("Teacher Russian");
    // Two distinct entry blocks, not one combined string.
    expect(splitCell?.querySelectorAll(".timetable-entry").length).toBe(2);
    expect(splitCell?.textContent).not.toMatch(/German.*Russian.*German|Russian\/German|German\+Russian/);
  });

  it("renders a reserved block with no group/teacher cleanly", () => {
    render(<TimetableGrid timetable={TIMETABLE} />);
    const cells = screen.getAllByRole("cell");
    const reservedCell = cells[3];
    expect(reservedCell).toHaveTextContent("Chess Club");
    expect(reservedCell?.querySelector(".timetable-entry-group")).toBeNull();
    expect(reservedCell?.querySelector(".timetable-entry-teacher")).toBeNull();
  });

  it("never displays raw natural IDs in ordinary UI", () => {
    render(<TimetableGrid timetable={TIMETABLE} />);
    const grid = screen.getByRole("table");
    for (const id of [
      "req1", "german_8a", "russian_8a", "club_chess",
      "t1", "t_german", "t_russian", "g1", "g_german", "g_russian",
    ]) {
      expect(grid.textContent).not.toContain(id);
    }
  });
});
