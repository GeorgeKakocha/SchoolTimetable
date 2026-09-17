import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import {
  buildTeacherMatrixCellLookup,
  getActiveTeacherTimetableMatrix,
  getTeacherTimetableMatrixForVersion,
} from "./teacherTimetableMatrix";
import type { TeacherTimetableMatrixResponse } from "./types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const MATRIX: TeacherTimetableMatrixResponse = {
  school_id: "school",
  school_name: "Dynamic School",
  academic_year_id: "year",
  academic_year_label: "2026/2027",
  version_number: 4,
  solver_status: "FEASIBLE",
  total_soft_penalty: 3,
  created_at: "2026-09-17T09:00:00Z",
  is_active: true,
  days: [
    { id: "day-1", name: "First configured day" },
    { id: "day-2", name: "Second configured day" },
    { id: "day-3", name: "Third configured day" },
  ],
  periods: [
    { id: "period-1", name: "Opening lesson" },
    { id: "period-2", name: "Second lesson" },
    { id: "period-3", name: "Third lesson" },
    { id: "period-4", name: "Final lesson" },
  ],
  teachers: [
    { id: "teacher-idle", name: "Teacher Idle", cells: [] },
    {
      id: "teacher-one",
      name: "Teacher One",
      cells: [
        {
          day_id: "day-1",
          period_id: "period-1",
          entries: [
            {
              source: "REQUIREMENT",
              activity_id: "language",
              activity_name: "Language",
              participant_group_id: "subgroup",
              participant_group_name: "Configured language branch",
              participant_group_role: "SUBGROUP",
              class_sections: [{ id: "lower", name: "1-A" }],
              requirement_id: "requirement-language",
              reserved_block_id: null,
              resource_id: null,
            },
          ],
        },
        {
          day_id: "day-2",
          period_id: "period-4",
          entries: [
            {
              source: "REQUIREMENT",
              activity_id: "seminar",
              activity_name: "Seminar",
              participant_group_id: "merged-group",
              participant_group_name: "Configured cross-grade seminar",
              participant_group_role: "MERGED_CLASSES",
              class_sections: [
                { id: "lower", name: "1-A" },
                { id: "upper", name: "XII" },
              ],
              requirement_id: "requirement-seminar",
              reserved_block_id: null,
              resource_id: "room-natural-id",
            },
            {
              source: "RESERVED_BLOCK",
              activity_id: "club",
              activity_name: "Configured club",
              participant_group_id: null,
              participant_group_name: null,
              participant_group_role: null,
              class_sections: [{ id: "upper", name: "XII" }],
              requirement_id: null,
              reserved_block_id: "reserved-club",
              resource_id: null,
            },
          ],
        },
      ],
    },
  ],
};

describe("Teacher Matrix API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests the exact active path and URL-encodes both natural IDs", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, MATRIX));
    vi.stubGlobal("fetch", fetchMock);

    await getActiveTeacherTimetableMatrix("school name/#1", "year / 1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/schools/school%20name%2F%231/years/year%20%2F%201/schedule/active/teacher-matrix",
    );
  });

  it("requests the exact historical path with its explicit version", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ...MATRIX, is_active: false }));
    vi.stubGlobal("fetch", fetchMock);

    await getTeacherTimetableMatrixForVersion("school", "year", 17);

    expect(fetchMock).toHaveBeenCalledWith(
      "/schools/school/years/year/schedule/versions/17/teacher-matrix",
    );
  });

  it("returns the realistic dynamic sparse response unchanged", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, MATRIX)));

    const result = await getActiveTeacherTimetableMatrix("school", "year");

    expect(result).toEqual(MATRIX);
    expect(result.days).toHaveLength(3);
    expect(result.periods).toHaveLength(4);
    expect(result.teachers[0]).toEqual({ id: "teacher-idle", name: "Teacher Idle", cells: [] });
    expect(result.teachers[1]?.cells[0]?.entries[0]?.participant_group_role).toBe("SUBGROUP");
    expect(result.teachers[1]?.cells[0]?.entries[0]?.participant_group_name).toBe(
      "Configured language branch",
    );
    const combined = result.teachers[1]?.cells[1]?.entries;
    expect(combined?.[0]?.participant_group_name).toBe("Configured cross-grade seminar");
    expect(combined?.[0]?.class_sections.map((section) => section.name)).toEqual(["1-A", "XII"]);
    expect(combined?.[1]?.source).toBe("RESERVED_BLOCK");
    expect(combined?.[1]?.reserved_block_id).toBe("reserved-club");
    expect(combined).toHaveLength(2);
  });

  it("keeps active and historical calls distinct and forwards AbortSignal", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(jsonResponse(200, MATRIX)),
    );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getActiveTeacherTimetableMatrix("school", "year", controller.signal);
    await getTeacherTimetableMatrixForVersion("school", "year", 3, controller.signal);

    expect(fetchMock.mock.calls[0]).toEqual([
      "/schools/school/years/year/schedule/active/teacher-matrix",
      { signal: controller.signal },
    ]);
    expect(fetchMock.mock.calls[1]).toEqual([
      "/schools/school/years/year/schedule/versions/3/teacher-matrix",
      { signal: controller.signal },
    ]);
  });

  it("preserves a structured historical failure through the shared ApiError contract", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(404, {
        code: "SCHEDULE_VERSION_NOT_FOUND",
        detail: "Schedule version 999 was not found",
        version_number: 999,
      })),
    );

    await expect(getTeacherTimetableMatrixForVersion("school", "year", 999)).rejects.toMatchObject({
      status: 404,
      detail: "Schedule version 999 was not found",
      code: "SCHEDULE_VERSION_NOT_FOUND",
      body: { version_number: 999 },
    } satisfies Partial<ApiError>);
  });
});

describe("buildTeacherMatrixCellLookup", () => {
  it("indexes occupied cells without materializing free coordinates", () => {
    const lookup = buildTeacherMatrixCellLookup(MATRIX);

    expect(lookup.get("teacher-one")?.get("day-1")?.get("period-1")).toBe(
      MATRIX.teachers[1]?.cells[0],
    );
    expect(lookup.get("teacher-one")?.get("day-1")?.get("period-2")).toBeUndefined();
    expect(lookup.get("teacher-one")?.get("day-3")).toBeUndefined();
  });

  it("retains zero-load teachers and every entry in a defensive multi-entry cell", () => {
    const lookup = buildTeacherMatrixCellLookup(MATRIX);

    expect(lookup.has("teacher-idle")).toBe(true);
    expect(lookup.get("teacher-idle")?.size).toBe(0);
    expect(lookup.get("teacher-one")?.get("day-2")?.get("period-4")?.entries).toEqual(
      MATRIX.teachers[1]?.cells[1]?.entries,
    );
  });

  it("does not depend on the number or labels of configured dimensions", () => {
    const changedDimensions: TeacherTimetableMatrixResponse = {
      ...MATRIX,
      days: [...MATRIX.days, { id: "weekend", name: "Configured weekend day" }],
      periods: [{ id: "only-period", name: "Only configured period" }],
    };

    const lookup = buildTeacherMatrixCellLookup(changedDimensions);

    expect(lookup.get("teacher-one")?.get("day-1")?.get("period-1")).toBeDefined();
    expect(lookup.get("teacher-one")?.get("weekend")?.get("only-period")).toBeUndefined();
  });
});
