/**
 * Typed API module for the manual timetable editing backend slice
 * (`docs/DECISIONS.md`, `api/schemas.py`, `api/schedule_routes.py`):
 * `GET .../schedule/active` (reused here for `locked_occurrences`, not
 * duplicated) plus the four mutating commands, matching `resources.ts`/
 * `calendar.ts`'s established one-domain-per-module convention.
 *
 * Every mutating request carries `base_version_number` -- the caller's
 * own responsibility to track and pass, never invented here. No
 * persistence surrogate ID is ever part of any request/response shape;
 * `requirement_id`/`day_id`/`period_id` are the same natural IDs the
 * class/teacher timetable projections already expose.
 */
import { getJson, postJson } from "./client";
import type { ActiveScheduleResponse, MovePreviewResponse } from "./types";

export interface MoveRequest {
  base_version_number: number;
  requirement_id: string;
  source_day_id: string;
  source_period_id: string;
  target_day_id: string;
  target_period_id: string;
}

/** `POST .../schedule/active/move/preview`'s request body -- the exact
 * same source-identification fields `MoveRequest` uses, minus the
 * target: this endpoint reports every OTHER instructional slot's
 * `validate_move` outcome for this one source. */
export interface MovePreviewRequest {
  base_version_number: number;
  requirement_id: string;
  source_day_id: string;
  source_period_id: string;
}

export interface LockRequest {
  base_version_number: number;
  requirement_id: string;
  day_id: string;
  period_id: string;
}

export type UnlockRequest = LockRequest;

export interface ReoptimizeRequest {
  base_version_number: number;
}

function scheduleActivePath(schoolId: string, academicYearId: string): string {
  return `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/schedule/active`;
}

export function getActiveSchedule(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<ActiveScheduleResponse> {
  return getJson<ActiveScheduleResponse>(scheduleActivePath(schoolId, academicYearId), signal);
}

export function moveScheduleEntry(
  schoolId: string,
  academicYearId: string,
  body: MoveRequest,
  signal?: AbortSignal,
): Promise<ActiveScheduleResponse> {
  return postJson<ActiveScheduleResponse>(`${scheduleActivePath(schoolId, academicYearId)}/move`, body, signal);
}

/** Read-only -- never persists anything, so callers may issue it freely
 * (including re-issuing it after a stale-version 409) without any of
 * the write-throttling concerns the mutating commands below carry. */
export function previewMove(
  schoolId: string,
  academicYearId: string,
  body: MovePreviewRequest,
  signal?: AbortSignal,
): Promise<MovePreviewResponse> {
  return postJson<MovePreviewResponse>(`${scheduleActivePath(schoolId, academicYearId)}/move/preview`, body, signal);
}

export function lockOccurrence(
  schoolId: string,
  academicYearId: string,
  body: LockRequest,
  signal?: AbortSignal,
): Promise<ActiveScheduleResponse> {
  return postJson<ActiveScheduleResponse>(`${scheduleActivePath(schoolId, academicYearId)}/lock`, body, signal);
}

export function unlockOccurrence(
  schoolId: string,
  academicYearId: string,
  body: UnlockRequest,
  signal?: AbortSignal,
): Promise<ActiveScheduleResponse> {
  return postJson<ActiveScheduleResponse>(`${scheduleActivePath(schoolId, academicYearId)}/unlock`, body, signal);
}

export function reoptimizeSchedule(
  schoolId: string,
  academicYearId: string,
  body: ReoptimizeRequest,
  signal?: AbortSignal,
): Promise<ActiveScheduleResponse> {
  return postJson<ActiveScheduleResponse>(`${scheduleActivePath(schoolId, academicYearId)}/reoptimize`, body, signal);
}
