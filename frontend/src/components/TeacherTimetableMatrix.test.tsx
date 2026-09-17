import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TeacherTimetableMatrixResponse } from "../api/types";
import TeacherTimetableMatrix from "./TeacherTimetableMatrix";
import { toRomanNumeral } from "./teacherMatrixPresentation";

const MATRIX: TeacherTimetableMatrixResponse = {
  school_id: "school",
  school_name: "School",
  academic_year_id: "year",
  academic_year_label: "2026/2027",
  version_number: 5,
  solver_status: "OPTIMAL",
  total_soft_penalty: 0,
  created_at: "2026-09-17T00:00:00Z",
  is_active: true,
  days: [
    { id: "third", name: "Third configured day" },
    { id: "first", name: "First configured day" },
  ],
  periods: [
    { id: "late", name: "Late lesson" },
    { id: "early", name: "Early lesson" },
    { id: "final", name: "Final lesson" },
  ],
  teachers: [
    { id: "idle", name: "Teacher Zero Load", cells: [] },
    {
      id: "teacher", name: "Teacher Scheduled", cells: [
        {
          day_id: "third", period_id: "late", entries: [
            {
              source: "REQUIREMENT", activity_id: "language", activity_name: "Language",
              participant_group_id: "subgroup", participant_group_name: "Configured subgroup 1-A",
              participant_group_role: "SUBGROUP", class_sections: [{ id: "lower", name: "1-A" }],
              requirement_id: "requirement-one", reserved_block_id: null, resource_id: null,
            },
          ],
        },
        {
          day_id: "first", period_id: "early", entries: [
            {
              source: "REQUIREMENT", activity_id: "seminar", activity_name: "Seminar",
              participant_group_id: "merged", participant_group_name: "Configured upper seminar",
              participant_group_role: "MERGED_CLASSES", class_sections: [
                { id: "lower", name: "1-A" }, { id: "upper", name: "XII" },
              ], requirement_id: "requirement-two", reserved_block_id: null, resource_id: null,
            },
            {
              source: "RESERVED_BLOCK", activity_id: "assembly", activity_name: "School Assembly",
              participant_group_id: null, participant_group_name: null, participant_group_role: null,
              class_sections: [], requirement_id: null, reserved_block_id: "reserved", resource_id: null,
            },
          ],
        },
      ],
    },
  ],
};

describe("TeacherTimetableMatrix", () => {
  it("renders ordered grouped days with the dynamic period span and repeated period order", () => {
    render(<TeacherTimetableMatrix matrix={MATRIX} />);

    const table = screen.getByRole("table", { name: "Teacher timetable matrix" });
    const columnGroups = within(table).getAllByRole("columnheader").filter(
      (header) => header.getAttribute("scope") === "colgroup",
    );
    expect(columnGroups.map((header) => header.textContent)).toEqual([
      "Third configured day", "First configured day",
    ]);
    expect(columnGroups.map((header) => header.getAttribute("colspan"))).toEqual(["3", "3"]);
    const periodHeaders = within(table).getAllByRole("columnheader").filter(
      (header) => header.getAttribute("scope") === "col",
    ).slice(1);
    expect(periodHeaders.map((header) => header.textContent)).toEqual([
      "I", "II", "III", "I", "II", "III",
    ]);
    // Presentation never mutates or derives meaning from configured IDs/names.
    expect(MATRIX.periods).toEqual([
      { id: "late", name: "Late lesson" },
      { id: "early", name: "Early lesson" },
      { id: "final", name: "Final lesson" },
    ]);
  });

  it("renders every teacher in authoritative order, including a complete zero-load row", () => {
    render(<TeacherTimetableMatrix matrix={MATRIX} />);

    const rowHeaders = screen.getAllByRole("rowheader");
    expect(rowHeaders.map((header) => header.textContent)).toEqual([
      "Teacher Zero Load", "Teacher Scheduled",
    ]);
    const idleRow = rowHeaders[0]!.closest("tr")!;
    expect(within(idleRow).getAllByRole("cell")).toHaveLength(6);
    expect(idleRow.textContent).toBe("Teacher Zero Load");
  });

  it("shows authoritative subgroup and merged class-section labels without ordinary activity text", () => {
    render(<TeacherTimetableMatrix matrix={MATRIX} />);

    expect(screen.getByText("Configured subgroup 1-A")).toBeInTheDocument();
    expect(screen.queryByText("Language")).not.toBeInTheDocument();
    expect(screen.getByText("1-A + XII")).toBeInTheDocument();
    expect(screen.queryByText("Configured upper seminar")).not.toBeInTheDocument();
    expect(screen.queryByText("Seminar")).not.toBeInTheDocument();
    expect(screen.getByText("School Assembly")).toBeInTheDocument();
    expect(MATRIX.teachers[1]?.cells[0]?.entries[0]?.class_sections[0]?.name).toBe("1-A");
    expect(MATRIX.teachers[1]?.cells[1]?.entries[0]?.class_sections[1]?.name).toBe("XII");
  });

  it("renders merged classes from structured class sections with a safe group-name fallback", () => {
    const mergedMatrix: TeacherTimetableMatrixResponse = {
      ...MATRIX,
      teachers: [{
        id: "merged", name: "Teacher Merged", cells: [
          {
            day_id: "third", period_id: "late", entries: [{
              source: "REQUIREMENT", activity_id: "history", activity_name: "History",
              participant_group_id: "merged-history", participant_group_name: "9-A + 9-B Merged History",
              participant_group_role: "MERGED_CLASSES", class_sections: [
                { id: "9-a", name: "9-A" }, { id: "9-b", name: "9-B" },
              ], requirement_id: "merged-history", reserved_block_id: null, resource_id: null,
            }],
          },
          {
            day_id: "third", period_id: "early", entries: [{
              source: "REQUIREMENT", activity_id: "seminar", activity_name: "Seminar",
              participant_group_id: "configured-merge", participant_group_name: "Verbose arbitrary merge",
              participant_group_role: "MERGED_CLASSES", class_sections: [
                { id: "alpha", name: "Orchid Cohort" }, { id: "beta", name: "North Studio" },
              ], requirement_id: "arbitrary-merge", reserved_block_id: null, resource_id: null,
            }],
          },
          {
            day_id: "third", period_id: "final", entries: [{
              source: "REQUIREMENT", activity_id: "fallback", activity_name: "Fallback subject",
              participant_group_id: "empty-merge", participant_group_name: "Configured merged fallback",
              participant_group_role: "MERGED_CLASSES", class_sections: [],
              requirement_id: "empty-merge", reserved_block_id: null, resource_id: null,
            }],
          },
        ],
      }],
    };
    render(<TeacherTimetableMatrix matrix={mergedMatrix} />);

    expect(screen.getByText("9-A + 9-B")).toBeInTheDocument();
    expect(screen.queryByText("9-A + 9-B Merged History")).not.toBeInTheDocument();
    expect(screen.getByText("Orchid Cohort + North Studio")).toBeInTheDocument();
    expect(screen.queryByText("Verbose arbitrary merge")).not.toBeInTheDocument();
    expect(screen.getByText("Configured merged fallback")).toBeInTheDocument();
  });

  it("shows configured class-section names for whole-class entries without changing other group roles", () => {
    const wholeClassMatrix: TeacherTimetableMatrixResponse = {
      ...MATRIX,
      teachers: [{
        id: "whole-class", name: "Teacher Whole Class", cells: [
          {
            day_id: "third", period_id: "late", entries: [{
              source: "REQUIREMENT", activity_id: "math", activity_name: "Mathematics",
              participant_group_id: "all-9-a", participant_group_name: "All of 9-A",
              participant_group_role: "WHOLE_CLASS", class_sections: [{ id: "9-a", name: "9-A" }],
              requirement_id: "whole-9-a", reserved_block_id: null, resource_id: null,
            }],
          },
          {
            day_id: "third", period_id: "early", entries: [{
              source: "REQUIREMENT", activity_id: "science", activity_name: "Science",
              participant_group_id: "all-8-b", participant_group_name: "All of 8-B",
              participant_group_role: "WHOLE_CLASS", class_sections: [{ id: "8-b", name: "8-B" }],
              requirement_id: "whole-8-b", reserved_block_id: null, resource_id: null,
            }],
          },
          {
            day_id: "first", period_id: "late", entries: [{
              source: "REQUIREMENT", activity_id: "combined", activity_name: "Combined",
              participant_group_id: "unexpected-whole", participant_group_name: "Verbose configured group",
              participant_group_role: "WHOLE_CLASS", class_sections: [
                { id: "class-a", name: "Configured A" }, { id: "class-b", name: "Configured B" },
              ], requirement_id: "whole-multiple", reserved_block_id: null, resource_id: null,
            }],
          },
        ],
      }],
    };
    render(<TeacherTimetableMatrix matrix={wholeClassMatrix} />);

    expect(screen.getByText("9-A")).toBeInTheDocument();
    expect(screen.getByText("8-B")).toBeInTheDocument();
    expect(screen.queryByText("All of 9-A")).not.toBeInTheDocument();
    expect(screen.queryByText("All of 8-B")).not.toBeInTheDocument();
    expect(screen.getByText("Configured A, Configured B")).toBeInTheDocument();
    expect(screen.queryByText("Verbose configured group")).not.toBeInTheDocument();
  });

  it("uses configured class names as fallback without hiding a targetless reserved activity", () => {
    const fallbackMatrix: TeacherTimetableMatrixResponse = {
      ...MATRIX,
      teachers: [{
        id: "fallback", name: "Teacher Fallback", cells: [{
          day_id: "third", period_id: "late", entries: [
            {
              source: "REQUIREMENT", activity_id: "subject", activity_name: "Hidden Subject",
              participant_group_id: null, participant_group_name: null, participant_group_role: null,
              class_sections: [{ id: "upper", name: "XII" }], requirement_id: "fallback",
              reserved_block_id: null, resource_id: null,
            },
            {
              source: "RESERVED_BLOCK", activity_id: "assembly", activity_name: "School Assembly",
              participant_group_id: null, participant_group_name: null, participant_group_role: null,
              class_sections: [], requirement_id: null, reserved_block_id: "reserved", resource_id: null,
            },
          ],
        }],
      }],
    };
    render(<TeacherTimetableMatrix matrix={fallbackMatrix} />);

    expect(screen.getByText("XII")).toBeInTheDocument();
    expect(screen.queryByText("Hidden Subject")).not.toBeInTheDocument();
    expect(screen.getByText("School Assembly")).toBeInTheDocument();
  });

  it("keeps free cells visually empty and applies structural day boundaries", () => {
    render(<TeacherTimetableMatrix matrix={MATRIX} />);

    const free = screen.getByRole("cell", {
      name: "Teacher Scheduled, Third configured day, Early lesson: Free",
    });
    expect(free.textContent).toBe("");
    expect(screen.queryByText("Free")).not.toBeInTheDocument();
    expect(free).not.toHaveClass("teacher-matrix-day-start");
    expect(screen.getByRole("cell", {
      name: "Teacher Scheduled, Third configured day, Late lesson",
    })).toHaveClass("teacher-matrix-day-start");
  });

  it("handles empty teachers and empty instructional periods without a broken table", () => {
    const { rerender } = render(<TeacherTimetableMatrix matrix={{ ...MATRIX, teachers: [] }} />);
    expect(screen.getByText("No teachers are available in this schedule configuration.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();

    rerender(<TeacherTimetableMatrix matrix={{ ...MATRIX, periods: [] }} />);
    expect(screen.getByText("No instructional periods are configured for this schedule.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("toRomanNumeral", () => {
  it("formats display positions dynamically beyond the spreadsheet examples", () => {
    expect([1, 2, 3, 7, 8, 9, 10, 14, 40].map(toRomanNumeral)).toEqual([
      "I", "II", "III", "VII", "VIII", "IX", "X", "XIV", "XL",
    ]);
  });

  it("rejects values that cannot be positive display positions", () => {
    expect(() => toRomanNumeral(0)).toThrow(RangeError);
    expect(() => toRomanNumeral(1.5)).toThrow(RangeError);
  });
});
