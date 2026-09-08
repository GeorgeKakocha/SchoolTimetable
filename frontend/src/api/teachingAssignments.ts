/**
 * Typed API module for the Phase 3C.2/3C.3b Teaching Assignments
 * backend contract (`docs/DECISIONS.md` #34-#36) -- kept separate from
 * `client.ts` (which stays scoped to the endpoints/fetch discipline it
 * already owns), matching the design gate's "one module per
 * page-domain" recommendation rather than one large generic SDK.
 *
 * Every URL is relative, reusing the exact same Vite dev-proxy
 * discipline `client.ts` already established -- this module never
 * hard-codes a backend host/port either. No backend validation/write
 * logic is duplicated here: every function is a thin, one-line
 * URL-building wrapper around `client.ts`'s shared
 * `getJson`/`postJson`/`putJson`/`deleteJson`.
 */
import { deleteJson, getJson, postJson, putJson } from "./client";
import type {
  TeachingAssignmentDeleteResponse,
  TeachingAssignmentsProjectionResponse,
  TeachingAssignmentWriteRequest,
  TeachingAssignmentWriteResponse,
} from "./types";

function teachingAssignmentsPath(schoolId: string, academicYearId: string): string {
  return `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/teaching-assignments`;
}

export function getTeachingAssignments(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<TeachingAssignmentsProjectionResponse> {
  return getJson<TeachingAssignmentsProjectionResponse>(teachingAssignmentsPath(schoolId, academicYearId), signal);
}

export function createTeachingAssignment(
  schoolId: string,
  academicYearId: string,
  body: TeachingAssignmentWriteRequest,
  signal?: AbortSignal,
): Promise<TeachingAssignmentWriteResponse> {
  return postJson<TeachingAssignmentWriteResponse>(teachingAssignmentsPath(schoolId, academicYearId), body, signal);
}

export function updateTeachingAssignment(
  schoolId: string,
  academicYearId: string,
  requirementId: string,
  body: TeachingAssignmentWriteRequest,
  signal?: AbortSignal,
): Promise<TeachingAssignmentWriteResponse> {
  const path = `${teachingAssignmentsPath(schoolId, academicYearId)}/${encodeURIComponent(requirementId)}`;
  return putJson<TeachingAssignmentWriteResponse>(path, body, signal);
}

export function deleteTeachingAssignment(
  schoolId: string,
  academicYearId: string,
  requirementId: string,
  signal?: AbortSignal,
): Promise<TeachingAssignmentDeleteResponse> {
  const path = `${teachingAssignmentsPath(schoolId, academicYearId)}/${encodeURIComponent(requirementId)}`;
  return deleteJson<TeachingAssignmentDeleteResponse>(path, signal);
}
