/**
 * Typed API module for the schedule version history + restore slice:
 * `GET .../schedule/versions` (history list), `GET .../schedule/
 * versions/{version_number}/classes/{class_section_id}` and
 * `.../teachers/{teacher_id}` (read-only historical projections), and
 * `POST .../schedule/versions/{version_number}/restore` -- matching
 * `scheduleEditing.ts`'s established one-domain-per-module convention.
 *
 * Restoring never reactivates or mutates the historical version itself
 * -- it creates a brand new active `ScheduleVersion` copied from it, so
 * the response is the exact same `ActiveScheduleResponse` shape every
 * other mutating editing command already returns.
 */
import { getJson, postJson } from "./client";
import type {
  ActiveScheduleResponse,
  ClassTimetableResponse,
  ScheduleVersionHistoryResponse,
  TeacherTimetableResponse,
} from "./types";

export interface RestoreVersionRequest {
  base_version_number: number;
}

function scheduleVersionsPath(schoolId: string, academicYearId: string): string {
  return `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/schedule/versions`;
}

export function getScheduleVersionHistory(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<ScheduleVersionHistoryResponse> {
  return getJson<ScheduleVersionHistoryResponse>(scheduleVersionsPath(schoolId, academicYearId), signal);
}

export function getClassTimetableForVersion(
  schoolId: string,
  academicYearId: string,
  versionNumber: number,
  classSectionId: string,
  signal?: AbortSignal,
): Promise<ClassTimetableResponse> {
  const path =
    `${scheduleVersionsPath(schoolId, academicYearId)}/${versionNumber}` +
    `/classes/${encodeURIComponent(classSectionId)}`;
  return getJson<ClassTimetableResponse>(path, signal);
}

export function getTeacherTimetableForVersion(
  schoolId: string,
  academicYearId: string,
  versionNumber: number,
  teacherId: string,
  signal?: AbortSignal,
): Promise<TeacherTimetableResponse> {
  const path =
    `${scheduleVersionsPath(schoolId, academicYearId)}/${versionNumber}` +
    `/teachers/${encodeURIComponent(teacherId)}`;
  return getJson<TeacherTimetableResponse>(path, signal);
}

export function restoreScheduleVersion(
  schoolId: string,
  academicYearId: string,
  versionNumber: number,
  body: RestoreVersionRequest,
  signal?: AbortSignal,
): Promise<ActiveScheduleResponse> {
  return postJson<ActiveScheduleResponse>(
    `${scheduleVersionsPath(schoolId, academicYearId)}/${versionNumber}/restore`,
    body,
    signal,
  );
}
