/**
 * Typed API module for Owner Decision #38's Teacher Availability
 * backend contract (`docs/DECISIONS.md`, `api/schemas.py`) -- kept
 * separate from `client.ts`, matching `teachers.ts`/`classes.ts`/
 * `subjects.ts`/`teachingAssignments.ts`'s established "one module per
 * page-domain" convention. Every function is a thin, one-line
 * URL-building wrapper around `client.ts`'s shared
 * `getJson`/`putJson`; no backend validation or business rule is
 * duplicated here.
 */
import { getJson, putJson } from "./client";
import type {
  TeacherAvailabilityProjectionResponse,
  TeacherAvailabilityReplaceRequest,
  TeacherAvailabilityWriteResponse,
} from "./types";

function teacherAvailabilityPath(schoolId: string, academicYearId: string): string {
  return `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/teacher-availability`;
}

export function getTeacherAvailability(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<TeacherAvailabilityProjectionResponse> {
  return getJson<TeacherAvailabilityProjectionResponse>(teacherAvailabilityPath(schoolId, academicYearId), signal);
}

export function replaceTeacherAvailability(
  schoolId: string,
  academicYearId: string,
  teacherId: string,
  body: TeacherAvailabilityReplaceRequest,
  signal?: AbortSignal,
): Promise<TeacherAvailabilityWriteResponse> {
  const path = `${teacherAvailabilityPath(schoolId, academicYearId)}/${encodeURIComponent(teacherId)}`;
  return putJson<TeacherAvailabilityWriteResponse>(path, body, signal);
}
