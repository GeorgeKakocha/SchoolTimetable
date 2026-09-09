/**
 * Typed API module for the Real-School Setup MVP Slice D Subject CRUD
 * backend contract (`docs/DECISIONS.md`, `api/schemas.py`) -- kept
 * separate from `client.ts`, matching `teachingAssignments.ts`'s
 * established "one module per page-domain" convention. Every function is
 * a thin, one-line URL-building wrapper around `client.ts`'s shared
 * `getJson`/`postJson`/`putJson`/`deleteJson`; no backend validation or
 * business rule is duplicated here. `GET /subjects` is already an
 * ORDINARY-only projection (Owner Decision -- Subject =
 * Activity(kind=ORDINARY)) -- this module never filters by `kind`
 * itself, since the response never carries one.
 */
import { deleteJson, getJson, postJson, putJson } from "./client";
import type {
  SubjectDeleteResponse,
  SubjectsProjectionResponse,
  SubjectWriteRequest,
  SubjectWriteResponse,
} from "./types";

function subjectsPath(schoolId: string, academicYearId: string): string {
  return `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/subjects`;
}

export function getSubjects(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<SubjectsProjectionResponse> {
  return getJson<SubjectsProjectionResponse>(subjectsPath(schoolId, academicYearId), signal);
}

export function createSubject(
  schoolId: string,
  academicYearId: string,
  body: SubjectWriteRequest,
  signal?: AbortSignal,
): Promise<SubjectWriteResponse> {
  return postJson<SubjectWriteResponse>(subjectsPath(schoolId, academicYearId), body, signal);
}

export function updateSubject(
  schoolId: string,
  academicYearId: string,
  subjectId: string,
  body: SubjectWriteRequest,
  signal?: AbortSignal,
): Promise<SubjectWriteResponse> {
  const path = `${subjectsPath(schoolId, academicYearId)}/${encodeURIComponent(subjectId)}`;
  return putJson<SubjectWriteResponse>(path, body, signal);
}

export function deleteSubject(
  schoolId: string,
  academicYearId: string,
  subjectId: string,
  signal?: AbortSignal,
): Promise<SubjectDeleteResponse> {
  const path = `${subjectsPath(schoolId, academicYearId)}/${encodeURIComponent(subjectId)}`;
  return deleteJson<SubjectDeleteResponse>(path, signal);
}
