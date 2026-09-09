/**
 * Typed API module for the Real-School Setup MVP Slice C Class CRUD
 * backend contract (`docs/DECISIONS.md`, `api/schemas.py`) -- kept
 * separate from `client.ts`, matching `teachingAssignments.ts`'s
 * established "one module per page-domain" convention. Every function is
 * a thin, one-line URL-building wrapper around `client.ts`'s shared
 * `getJson`/`postJson`/`putJson`/`deleteJson`; no backend validation or
 * business rule is duplicated here.
 */
import { deleteJson, getJson, postJson, putJson } from "./client";
import type {
  ClassesProjectionResponse,
  ClassSectionDeleteResponse,
  ClassSectionWriteRequest,
  ClassSectionWriteResponse,
} from "./types";

function classesPath(schoolId: string, academicYearId: string): string {
  return `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/classes`;
}

export function getClasses(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<ClassesProjectionResponse> {
  return getJson<ClassesProjectionResponse>(classesPath(schoolId, academicYearId), signal);
}

export function createClass(
  schoolId: string,
  academicYearId: string,
  body: ClassSectionWriteRequest,
  signal?: AbortSignal,
): Promise<ClassSectionWriteResponse> {
  return postJson<ClassSectionWriteResponse>(classesPath(schoolId, academicYearId), body, signal);
}

export function updateClass(
  schoolId: string,
  academicYearId: string,
  classId: string,
  body: ClassSectionWriteRequest,
  signal?: AbortSignal,
): Promise<ClassSectionWriteResponse> {
  const path = `${classesPath(schoolId, academicYearId)}/${encodeURIComponent(classId)}`;
  return putJson<ClassSectionWriteResponse>(path, body, signal);
}

export function deleteClass(
  schoolId: string,
  academicYearId: string,
  classId: string,
  signal?: AbortSignal,
): Promise<ClassSectionDeleteResponse> {
  const path = `${classesPath(schoolId, academicYearId)}/${encodeURIComponent(classId)}`;
  return deleteJson<ClassSectionDeleteResponse>(path, signal);
}
