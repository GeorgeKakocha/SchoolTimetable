/**
 * Typed API module for the Calendar A backend contract (`docs/DECISIONS.md`,
 * `api/schemas.py`, `api/calendar_routes.py`) -- kept separate from
 * `client.ts`, matching `resources.ts`'s established "one module per
 * page-domain" convention. Every function is a thin, one-line
 * URL-building wrapper around `client.ts`'s shared
 * `getJson`/`postJson`/`putJson`/`deleteJson`; no backend validation or
 * business rule is duplicated here.
 *
 * `Day`/`Period` are the existing `domain.calendar.Day`/`Period` --
 * this module never exposes a raw `block_id` (only the public
 * `starts_new_block` boundary marker) and never exposes an ordinal
 * beyond the server-assigned `index` used purely for display/ordering.
 * Times are HH:MM strings (or `null`) on the wire -- `parse_hhmm`/
 * `format_hhmm` on the backend, never re-implemented here.
 */
import { deleteJson, getJson, postJson, putJson } from "./client";

export interface CalendarDayItem {
  id: string;
  name: string;
  index: number;
}

export interface CalendarPeriodItem {
  id: string;
  name: string;
  index: number;
  start_time: string | null;
  end_time: string | null;
  starts_new_block: boolean;
  is_instructional: boolean;
}

export interface CalendarProjectionResponse {
  configuration_locked: boolean;
  days: CalendarDayItem[];
  periods: CalendarPeriodItem[];
}

export type MoveDirection = "up" | "down";

export interface DayWriteRequest {
  name: string;
}

export interface DayWriteResponse {
  id: string;
  name: string;
  index: number;
}

export interface DayDeleteResponse {
  deleted_id: string;
}

export interface DayMoveRequest {
  direction: MoveDirection;
}

export interface PeriodWriteRequest {
  name: string;
  start_time: string | null;
  end_time: string | null;
  starts_new_block: boolean;
}

export interface PeriodWriteResponse {
  id: string;
  name: string;
  index: number;
  start_time: string | null;
  end_time: string | null;
  starts_new_block: boolean;
  is_instructional: boolean;
}

export interface PeriodDeleteResponse {
  deleted_id: string;
}

export interface PeriodMoveRequest {
  direction: MoveDirection;
}

function calendarPath(schoolId: string, academicYearId: string): string {
  return `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/calendar`;
}

function daysPath(schoolId: string, academicYearId: string): string {
  return `${calendarPath(schoolId, academicYearId)}/days`;
}

function periodsPath(schoolId: string, academicYearId: string): string {
  return `${calendarPath(schoolId, academicYearId)}/periods`;
}

export function getCalendar(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<CalendarProjectionResponse> {
  return getJson<CalendarProjectionResponse>(calendarPath(schoolId, academicYearId), signal);
}

// -- Day ---------------------------------------------------------------

export function createDay(
  schoolId: string,
  academicYearId: string,
  body: DayWriteRequest,
  signal?: AbortSignal,
): Promise<DayWriteResponse> {
  return postJson<DayWriteResponse>(daysPath(schoolId, academicYearId), body, signal);
}

export function updateDay(
  schoolId: string,
  academicYearId: string,
  dayId: string,
  body: DayWriteRequest,
  signal?: AbortSignal,
): Promise<DayWriteResponse> {
  const path = `${daysPath(schoolId, academicYearId)}/${encodeURIComponent(dayId)}`;
  return putJson<DayWriteResponse>(path, body, signal);
}

export function deleteDay(
  schoolId: string,
  academicYearId: string,
  dayId: string,
  signal?: AbortSignal,
): Promise<DayDeleteResponse> {
  const path = `${daysPath(schoolId, academicYearId)}/${encodeURIComponent(dayId)}`;
  return deleteJson<DayDeleteResponse>(path, signal);
}

export function moveDay(
  schoolId: string,
  academicYearId: string,
  dayId: string,
  direction: MoveDirection,
  signal?: AbortSignal,
): Promise<CalendarProjectionResponse> {
  const path = `${daysPath(schoolId, academicYearId)}/${encodeURIComponent(dayId)}/move`;
  const body: DayMoveRequest = { direction };
  return postJson<CalendarProjectionResponse>(path, body, signal);
}

// -- Period --------------------------------------------------------------

export function createPeriod(
  schoolId: string,
  academicYearId: string,
  body: PeriodWriteRequest,
  signal?: AbortSignal,
): Promise<PeriodWriteResponse> {
  return postJson<PeriodWriteResponse>(periodsPath(schoolId, academicYearId), body, signal);
}

export function updatePeriod(
  schoolId: string,
  academicYearId: string,
  periodId: string,
  body: PeriodWriteRequest,
  signal?: AbortSignal,
): Promise<PeriodWriteResponse> {
  const path = `${periodsPath(schoolId, academicYearId)}/${encodeURIComponent(periodId)}`;
  return putJson<PeriodWriteResponse>(path, body, signal);
}

export function deletePeriod(
  schoolId: string,
  academicYearId: string,
  periodId: string,
  signal?: AbortSignal,
): Promise<PeriodDeleteResponse> {
  const path = `${periodsPath(schoolId, academicYearId)}/${encodeURIComponent(periodId)}`;
  return deleteJson<PeriodDeleteResponse>(path, signal);
}

export function movePeriod(
  schoolId: string,
  academicYearId: string,
  periodId: string,
  direction: MoveDirection,
  signal?: AbortSignal,
): Promise<CalendarProjectionResponse> {
  const path = `${periodsPath(schoolId, academicYearId)}/${encodeURIComponent(periodId)}/move`;
  const body: PeriodMoveRequest = { direction };
  return postJson<CalendarProjectionResponse>(path, body, signal);
}
