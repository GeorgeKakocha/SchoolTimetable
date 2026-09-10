/**
 * Typed API module for the Reserved Activities Slice A1 Special
 * Activity CRUD backend contract (`docs/DECISIONS.md`,
 * `api/schemas.py`, `api/special_activity_routes.py`) -- kept
 * separate from `client.ts`, matching `subjects.ts`'s established
 * "one module per page-domain" convention. Every function is a thin,
 * one-line URL-building wrapper around `client.ts`'s shared
 * `getJson`/`postJson`/`putJson`/`deleteJson`; no backend validation
 * or business rule is duplicated here. "Special Activity" is not a
 * new domain entity -- it is the user-facing name for
 * `Activity(kind=CLUB)` -- this module never exposes `kind` or any
 * other internal vocabulary, since the backend response never carries
 * one on this surface.
 */
import { deleteJson, getJson, postJson, putJson } from "./client";

export interface SpecialActivityItem {
  id: string;
  name: string;
}

export interface SpecialActivitiesProjectionResponse {
  configuration_locked: boolean;
  special_activities: SpecialActivityItem[];
}

export interface SpecialActivityWriteRequest {
  name: string;
}

export interface SpecialActivityWriteResponse {
  id: string;
  name: string;
}

export interface SpecialActivityDeleteResponse {
  deleted_id: string;
}

function specialActivitiesPath(schoolId: string, academicYearId: string): string {
  return `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/special-activities`;
}

export function getSpecialActivities(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<SpecialActivitiesProjectionResponse> {
  return getJson<SpecialActivitiesProjectionResponse>(specialActivitiesPath(schoolId, academicYearId), signal);
}

export function createSpecialActivity(
  schoolId: string,
  academicYearId: string,
  body: SpecialActivityWriteRequest,
  signal?: AbortSignal,
): Promise<SpecialActivityWriteResponse> {
  return postJson<SpecialActivityWriteResponse>(specialActivitiesPath(schoolId, academicYearId), body, signal);
}

export function updateSpecialActivity(
  schoolId: string,
  academicYearId: string,
  specialActivityId: string,
  body: SpecialActivityWriteRequest,
  signal?: AbortSignal,
): Promise<SpecialActivityWriteResponse> {
  const path = `${specialActivitiesPath(schoolId, academicYearId)}/${encodeURIComponent(specialActivityId)}`;
  return putJson<SpecialActivityWriteResponse>(path, body, signal);
}

export function deleteSpecialActivity(
  schoolId: string,
  academicYearId: string,
  specialActivityId: string,
  signal?: AbortSignal,
): Promise<SpecialActivityDeleteResponse> {
  const path = `${specialActivitiesPath(schoolId, academicYearId)}/${encodeURIComponent(specialActivityId)}`;
  return deleteJson<SpecialActivityDeleteResponse>(path, signal);
}
