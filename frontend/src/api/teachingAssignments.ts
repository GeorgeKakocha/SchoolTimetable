/**
 * Typed API module for the Phase 3C.2b Teaching Assignments backend
 * contract (`docs/DECISIONS.md` #34-#36) -- kept separate from
 * `client.ts` (which stays scoped to the read-only schedule/config
 * endpoints it already owns), matching the design gate's "one module
 * per page-domain" recommendation rather than one large generic SDK.
 *
 * Phase 3C.3a is read-only: only `getTeachingAssignments` exists here.
 * `createTeachingAssignment`/`updateTeachingAssignment`/
 * `deleteTeachingAssignment` belong to Phase 3C.3b, not this slice.
 *
 * Every URL is relative, reusing the exact same Vite dev-proxy
 * discipline `client.ts` already established -- this module never
 * hard-codes a backend host/port either.
 */
import { getJson } from "./client";
import type { TeachingAssignmentsProjectionResponse } from "./types";

export function getTeachingAssignments(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<TeachingAssignmentsProjectionResponse> {
  const path = `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/teaching-assignments`;
  return getJson<TeachingAssignmentsProjectionResponse>(path, signal);
}
