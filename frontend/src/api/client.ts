/**
 * Native-`fetch`-based API client for the backend's read-only schedule
 * endpoints. Every URL is relative -- the Vite dev proxy (`vite.config.ts`)
 * is the only place a backend host/port is ever written down; this
 * module never hard-codes `http://localhost:8000` (Decision #32, Owner
 * Decision 9).
 *
 * This module resolves no scheduling semantics of its own: it only
 * fetches, checks the HTTP status, and narrows the JSON body to the
 * typed shape this frontend slice actually consumes.
 */
import type {
  ClassSectionSummary,
  ClassTimetableResponse,
  SchedulingConfigIndexResponse,
} from "./types";

/** A real, non-2xx backend response. `detail` preserves the backend's
 * own safe, user-facing message (e.g. "Active schedule not found") when
 * one is present; otherwise a generic fallback -- never a raw
 * `Response`/internal browser object, never response body text that
 * might not be safe to display. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`Request failed with status ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** The response body was not shaped the way this client expects --
 * distinct from `ApiError`, which represents a real HTTP error status
 * the backend itself reported. */
export class MalformedResponseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MalformedResponseError";
  }
}

const GENERIC_ERROR_DETAIL = "Request failed.";

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof (body as { detail: unknown }).detail === "string"
    ) {
      return (body as { detail: string }).detail;
    }
  } catch {
    // Response body was not JSON (or was empty) -- fall through.
  }
  return GENERIC_ERROR_DETAIL;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new ApiError(response.status, await readErrorDetail(response));
  }
  return (await response.json()) as T;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string";
}

function isClassSectionSummary(value: unknown): value is ClassSectionSummary {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return isNonEmptyString(candidate["id"]) && isNonEmptyString(candidate["name"]);
}

/** Narrows the real `/config` response (a superset of what this slice
 * needs) down to `SchedulingConfigIndexResponse` -- a small manual shape
 * guard, not a schema-validation dependency, so an obviously malformed
 * payload never silently becomes a usable class index. */
function toSchedulingConfigIndex(raw: unknown): SchedulingConfigIndexResponse {
  if (typeof raw !== "object" || raw === null) {
    throw new MalformedResponseError("Scheduling configuration response was not an object.");
  }
  const candidate = raw as Record<string, unknown>;

  const school = candidate["school"];
  const academicYear = candidate["academic_year"];
  const classSections = candidate["class_sections"];

  if (
    typeof school !== "object" ||
    school === null ||
    !isNonEmptyString((school as Record<string, unknown>)["id"]) ||
    !isNonEmptyString((school as Record<string, unknown>)["name"])
  ) {
    throw new MalformedResponseError("Scheduling configuration response had a malformed 'school'.");
  }
  if (
    typeof academicYear !== "object" ||
    academicYear === null ||
    !isNonEmptyString((academicYear as Record<string, unknown>)["id"]) ||
    !isNonEmptyString((academicYear as Record<string, unknown>)["label"])
  ) {
    throw new MalformedResponseError(
      "Scheduling configuration response had a malformed 'academic_year'.",
    );
  }
  if (!Array.isArray(classSections) || !classSections.every(isClassSectionSummary)) {
    throw new MalformedResponseError(
      "Scheduling configuration response had a malformed 'class_sections'.",
    );
  }

  return {
    school: school as SchedulingConfigIndexResponse["school"],
    academic_year: academicYear as SchedulingConfigIndexResponse["academic_year"],
    class_sections: classSections,
  };
}

export async function getSchedulingConfigIndex(
  schoolId: string,
  academicYearId: string,
): Promise<SchedulingConfigIndexResponse> {
  const path = `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/config`;
  const raw = await getJson<unknown>(path);
  return toSchedulingConfigIndex(raw);
}

export function getClassTimetable(
  schoolId: string,
  academicYearId: string,
  classSectionId: string,
): Promise<ClassTimetableResponse> {
  const path =
    `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}` +
    `/schedule/active/classes/${encodeURIComponent(classSectionId)}`;
  return getJson<ClassTimetableResponse>(path);
}
