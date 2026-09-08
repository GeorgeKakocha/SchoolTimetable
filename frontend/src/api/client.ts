/**
 * Native-`fetch`-based API client. Every URL is relative -- the Vite dev
 * proxy (`vite.config.ts`) is the only place a backend host/port is
 * ever written down; this module never hard-codes
 * `http://localhost:8000` (Decision #32, Owner Decision 9).
 *
 * This module resolves no scheduling semantics of its own: it only
 * fetches, checks the HTTP status, and narrows the JSON body to the
 * typed shape this frontend slice actually consumes. `getJson` remains
 * the GET-only entry point every read call already used; `postJson`/
 * `putJson`/`deleteJson` (Phase 3C.3b, all funneling through the
 * shared internal `sendJson`) are the write counterparts, added for
 * the Teaching Assignments mutation UI -- neither introduces any new
 * fetch/error-handling discipline of its own.
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
 * might not be safe to display.
 *
 * `code`/`body` are optional, additive fields (Phase 3C.3b): the
 * Teaching Assignment write endpoints return a structured
 * `{"code": "...", "detail": "...", ...extra fields}` error body
 * (`docs/DECISIONS.md` #34-#36); `code` and the full parsed `body` are
 * captured here, unparsed, so callers needing e.g.
 * `body.advanced_reasons`/`body.teacher_id` can read them directly
 * without `client.ts` hand-typing every backend error shape. `detail`
 * stays a plain string always -- FastAPI's own generic Pydantic 422
 * body (`{"detail": [...]}`) has a non-string `detail`; that case falls
 * back to the generic message below rather than ever becoming an
 * array, but the raw body (with its array `detail`) is still available
 * via `.body` for a caller that wants it. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly code?: string | undefined;
  readonly body?: Record<string, unknown> | undefined;

  constructor(status: number, detail: string, code?: string, body?: Record<string, unknown>) {
    super(`Request failed with status ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.code = code;
    this.body = body;
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

/** Parses a non-2xx response body exactly once, into the three pieces
 * `ApiError` needs. A malformed/non-JSON/empty body, or a JSON body
 * that isn't a plain object (e.g. FastAPI's generic Pydantic 422 array
 * `detail`, or any other non-object shape), safely falls back to the
 * generic detail with no `code`/`body` -- this never throws, and never
 * surfaces raw response text. */
async function readErrorInfo(
  response: Response,
): Promise<{ detail: string; code?: string | undefined; body?: Record<string, unknown> | undefined }> {
  let parsed: unknown;
  try {
    parsed = await response.json();
  } catch {
    return { detail: GENERIC_ERROR_DETAIL };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { detail: GENERIC_ERROR_DETAIL };
  }
  const body = parsed as Record<string, unknown>;
  const detail = typeof body["detail"] === "string" ? (body["detail"] as string) : GENERIC_ERROR_DETAIL;
  const code = typeof body["code"] === "string" ? (body["code"] as string) : undefined;
  return { detail, code, body };
}

/** Exported so other page-scoped API modules (e.g. `teachingAssignments.ts`)
 * reuse this exact fetch/status/JSON-parsing discipline instead of
 * duplicating it -- the `ApiError`/`MalformedResponseError` contract
 * stays defined in exactly one place. */
export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = signal === undefined ? await fetch(path) : await fetch(path, { signal });
  if (!response.ok) {
    const info = await readErrorInfo(response);
    throw new ApiError(response.status, info.detail, info.code, info.body);
  }
  return (await response.json()) as T;
}

/** The shared write-request helper (Phase 3C.3b): every mutation
 * (`POST`/`PUT`/`DELETE`) funnels through here so the
 * fetch/`Content-Type`/error-parsing discipline is defined exactly
 * once, matching `getJson`'s existing discipline. `body` is
 * JSON-stringified with an explicit `Content-Type: application/json`
 * header only when supplied -- `DELETE` (no `body` argument) sends
 * neither, matching the locked backend contract exactly. */
async function sendJson<T>(
  method: "POST" | "PUT" | "DELETE",
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const init: RequestInit = { method };
  if (signal !== undefined) {
    init.signal = signal;
  }
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  if (!response.ok) {
    const info = await readErrorInfo(response);
    throw new ApiError(response.status, info.detail, info.code, info.body);
  }
  return (await response.json()) as T;
}

export function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return sendJson<T>("POST", path, body, signal);
}

export function putJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return sendJson<T>("PUT", path, body, signal);
}

export function deleteJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return sendJson<T>("DELETE", path, undefined, signal);
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
  signal?: AbortSignal,
): Promise<SchedulingConfigIndexResponse> {
  const path = `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/config`;
  const raw = await getJson<unknown>(path, signal);
  return toSchedulingConfigIndex(raw);
}

export function getClassTimetable(
  schoolId: string,
  academicYearId: string,
  classSectionId: string,
  signal?: AbortSignal,
): Promise<ClassTimetableResponse> {
  const path =
    `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}` +
    `/schedule/active/classes/${encodeURIComponent(classSectionId)}`;
  return getJson<ClassTimetableResponse>(path, signal);
}
