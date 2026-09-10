/**
 * Typed API module for the Reserved Activities Slice A2 Reserved
 * Activity CRUD backend contract (`docs/DECISIONS.md`,
 * `api/schemas.py`, `api/reserved_activity_routes.py`) -- kept
 * separate from `client.ts`, matching `teacherAvailability.ts`'s
 * established "one module per page-domain" convention: every wire
 * type this feature needs is defined directly alongside its API
 * functions here, rather than centralized in `api/types.ts` (Reserved
 * B's corrected frontend contract, item 17 -- `types.ts` is read-only
 * reused for `ValidationDiagnostic`, never modified for this feature).
 * "Reserved Activity" is not a new domain entity -- it is the
 * user-facing name for `ReservedBlock`; this module never exposes
 * `ReservedBlock.name`, natural IDs as primary text, or raw
 * `CLUB`/`ORDINARY` vocabulary -- the backend response never carries
 * any of that on this surface. Every function is a thin, one-line
 * URL-building wrapper around `client.ts`'s shared
 * `getJson`/`postJson`/`putJson`/`deleteJson`; no backend validation
 * or business rule is duplicated here.
 */
import { deleteJson, getJson, postJson, putJson } from "./client";
import type { ValidationDiagnostic } from "./types";

export interface ReservedActivitySpecialActivityOption {
  id: string;
  name: string;
}

export interface ReservedActivityTeacherOption {
  id: string;
  name: string;
}

export interface ReservedActivityClassSectionOption {
  id: string;
  name: string;
}

export interface ReservedActivityDay {
  id: string;
  name: string;
  index: number;
}

export interface ReservedActivityPeriod {
  id: string;
  name: string;
  index: number;
  is_instructional: boolean;
}

export interface ReservedActivitySlot {
  day_id: string;
  period_id: string;
}

// Resources B2's "fixed Resource" option list -- `Resource` is the
// existing `domain.resources.Resource(id, name, capacity)` (Resources
// Slice A); `capacity` means "maximum simultaneous resource
// occupations," never seat/headcount capacity.
export interface ReservedActivityResourceOption {
  id: string;
  name: string;
  capacity: number;
}

export interface ReservedActivityItem {
  id: string;
  special_activity_id: string;
  class_section_ids: string[];
  teacher_id: string | null;
  slots: ReservedActivitySlot[];
  resource_id: string | null;
}

export interface ReservedActivitiesProjectionResponse {
  configuration_locked: boolean;
  special_activities: ReservedActivitySpecialActivityOption[];
  teachers: ReservedActivityTeacherOption[];
  class_sections: ReservedActivityClassSectionOption[];
  days: ReservedActivityDay[];
  periods: ReservedActivityPeriod[];
  reserved_activities: ReservedActivityItem[];
  resources: ReservedActivityResourceOption[];
}

export interface ReservedActivityWriteRequest {
  special_activity_id: string;
  class_section_ids: string[];
  teacher_id: string | null;
  slots: ReservedActivitySlot[];
  // Full-replacement, never PATCH (Resources B2): `null` (explicit,
  // never omitted by this frontend) means "no fixed Resource" -- on a
  // PUT this always clears any Resource currently assigned. A
  // non-null value assigns/replaces that exact Resource.
  resource_id: string | null;
}

export interface ReservedActivityWriteResponse {
  id: string;
  special_activity_id: string;
  class_section_ids: string[];
  teacher_id: string | null;
  slots: ReservedActivitySlot[];
  resource_id: string | null;
}

export interface ReservedActivityDeleteResponse {
  deleted_id: string;
}

/** Re-exported so callers (`ReservedActivitiesPage`) can type a
 * `INVALID_RESERVED_ACTIVITY` error body's `errors` array without
 * redefining the already-shared `{code, message, context}` shape --
 * the one genuinely shared type this feature reuses, per item 17. */
export type { ValidationDiagnostic };

function reservedActivitiesPath(schoolId: string, academicYearId: string): string {
  return `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/reserved-activities`;
}

export function getReservedActivities(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<ReservedActivitiesProjectionResponse> {
  return getJson<ReservedActivitiesProjectionResponse>(reservedActivitiesPath(schoolId, academicYearId), signal);
}

export function createReservedActivity(
  schoolId: string,
  academicYearId: string,
  body: ReservedActivityWriteRequest,
  signal?: AbortSignal,
): Promise<ReservedActivityWriteResponse> {
  return postJson<ReservedActivityWriteResponse>(reservedActivitiesPath(schoolId, academicYearId), body, signal);
}

export function updateReservedActivity(
  schoolId: string,
  academicYearId: string,
  reservedActivityId: string,
  body: ReservedActivityWriteRequest,
  signal?: AbortSignal,
): Promise<ReservedActivityWriteResponse> {
  const path = `${reservedActivitiesPath(schoolId, academicYearId)}/${encodeURIComponent(reservedActivityId)}`;
  return putJson<ReservedActivityWriteResponse>(path, body, signal);
}

export function deleteReservedActivity(
  schoolId: string,
  academicYearId: string,
  reservedActivityId: string,
  signal?: AbortSignal,
): Promise<ReservedActivityDeleteResponse> {
  const path = `${reservedActivitiesPath(schoolId, academicYearId)}/${encodeURIComponent(reservedActivityId)}`;
  return deleteJson<ReservedActivityDeleteResponse>(path, signal);
}
