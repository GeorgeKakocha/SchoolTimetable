/** Typed reads and sparse-cell indexing for the whole-school Teacher Matrix. */
import { getJson } from "./client";
import type {
  TeacherMatrixCell,
  TeacherTimetableMatrixResponse,
} from "./types";

export type TeacherMatrixCellLookup = Map<
  string,
  Map<string, Map<string, TeacherMatrixCell>>
>;

function schedulePath(schoolId: string, academicYearId: string): string {
  return (
    `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}` +
    "/schedule"
  );
}

export function getActiveTeacherTimetableMatrix(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<TeacherTimetableMatrixResponse> {
  return getJson<TeacherTimetableMatrixResponse>(
    `${schedulePath(schoolId, academicYearId)}/active/teacher-matrix`,
    signal,
  );
}

export function getTeacherTimetableMatrixForVersion(
  schoolId: string,
  academicYearId: string,
  versionNumber: number,
  signal?: AbortSignal,
): Promise<TeacherTimetableMatrixResponse> {
  return getJson<TeacherTimetableMatrixResponse>(
    `${schedulePath(schoolId, academicYearId)}/versions/${versionNumber}/teacher-matrix`,
    signal,
  );
}

/** Index only the occupied cells supplied by the authoritative API.
 * Every configured teacher receives a top-level map, including zero-load
 * teachers, but no day/period coordinate is materialized for a free slot. */
export function buildTeacherMatrixCellLookup(
  matrix: TeacherTimetableMatrixResponse,
): TeacherMatrixCellLookup {
  const lookup: TeacherMatrixCellLookup = new Map();
  for (const teacher of matrix.teachers) {
    const byDay = new Map<string, Map<string, TeacherMatrixCell>>();
    lookup.set(teacher.id, byDay);
    for (const cell of teacher.cells) {
      let byPeriod = byDay.get(cell.day_id);
      if (byPeriod === undefined) {
        byPeriod = new Map();
        byDay.set(cell.day_id, byPeriod);
      }
      byPeriod.set(cell.period_id, cell);
    }
  }
  return lookup;
}
