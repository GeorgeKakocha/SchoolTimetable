/**
 * Typed API module for the Real-School Setup MVP Slice B Teacher CRUD
 * backend contract (`docs/DECISIONS.md`, `api/schemas.py`) -- kept
 * separate from `client.ts`, matching `teachingAssignments.ts`'s
 * established "one module per page-domain" convention. Every function is
 * a thin, one-line URL-building wrapper around `client.ts`'s shared
 * `getJson`/`postJson`/`putJson`/`deleteJson`; no backend validation or
 * business rule is duplicated here.
 */
import { deleteJson, getJson, postJson, putJson } from "./client";
import type {
  TeacherDeleteResponse,
  TeachersProjectionResponse,
  TeacherWriteRequest,
  TeacherWriteResponse,
} from "./types";

function teachersPath(schoolId: string, academicYearId: string): string {
  return `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/teachers`;
}

export function getTeachers(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<TeachersProjectionResponse> {
  return getJson<TeachersProjectionResponse>(teachersPath(schoolId, academicYearId), signal);
}

export function createTeacher(
  schoolId: string,
  academicYearId: string,
  body: TeacherWriteRequest,
  signal?: AbortSignal,
): Promise<TeacherWriteResponse> {
  return postJson<TeacherWriteResponse>(teachersPath(schoolId, academicYearId), body, signal);
}

export function updateTeacher(
  schoolId: string,
  academicYearId: string,
  teacherId: string,
  body: TeacherWriteRequest,
  signal?: AbortSignal,
): Promise<TeacherWriteResponse> {
  const path = `${teachersPath(schoolId, academicYearId)}/${encodeURIComponent(teacherId)}`;
  return putJson<TeacherWriteResponse>(path, body, signal);
}

export function deleteTeacher(
  schoolId: string,
  academicYearId: string,
  teacherId: string,
  signal?: AbortSignal,
): Promise<TeacherDeleteResponse> {
  const path = `${teachersPath(schoolId, academicYearId)}/${encodeURIComponent(teacherId)}`;
  return deleteJson<TeacherDeleteResponse>(path, signal);
}
