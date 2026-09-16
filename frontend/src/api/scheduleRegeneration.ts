import { postJson, ApiError } from "./client";
import type {
  ActiveScheduleResponse,
  ConfigurationChangedDuringGenerationErrorBody,
  IncompatibleLocksRequireConfirmationErrorBody,
  InvalidConfigurationErrorBody,
  NoConfigurationDraftErrorBody,
  RegenerateScheduleRequest,
  ScheduleInfeasibleErrorBody,
  StaleScheduleVersionErrorBody,
} from "./types";

export type {
  ConfigurationChangedDuringGenerationErrorBody,
  IncompatibleLocksRequireConfirmationErrorBody,
  InvalidConfigurationErrorBody,
  NoConfigurationDraftErrorBody,
  RegenerateScheduleRequest,
  ScheduleInfeasibleErrorBody,
  StaleScheduleVersionErrorBody,
};

export function regenerateActiveSchedule(
  schoolId: string,
  academicYearId: string,
  request: RegenerateScheduleRequest,
  signal?: AbortSignal,
): Promise<ActiveScheduleResponse> {
  const path =
    `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}` +
    `/schedule/active/regenerate`;
  return postJson<ActiveScheduleResponse>(path, request, signal);
}

function hasCode(error: unknown, code: string): error is ApiError {
  return error instanceof ApiError && error.code === code;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isIncompatibleLock(value: unknown): boolean {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value["requirement_id"] === "string" &&
    typeof value["day_id"] === "string" &&
    typeof value["anchor_period_id"] === "string" &&
    typeof value["reason_code"] === "string" &&
    typeof value["message"] === "string"
  );
}

export function isIncompatibleLocksRequireConfirmationError(
  error: unknown,
): error is ApiError & { body: IncompatibleLocksRequireConfirmationErrorBody } {
  return hasCode(error, "INCOMPATIBLE_LOCKS_REQUIRE_CONFIRMATION") &&
    Array.isArray(error.body?.["incompatible_locks"]) &&
    error.body["incompatible_locks"].every(isIncompatibleLock);
}

export function isStaleScheduleVersionError(error: unknown): error is ApiError {
  return hasCode(error, "STALE_SCHEDULE_VERSION");
}

export function isNoConfigurationDraftError(error: unknown): error is ApiError {
  return hasCode(error, "NO_CONFIGURATION_DRAFT");
}

export function isConfigurationChangedDuringGenerationError(error: unknown): error is ApiError {
  return hasCode(error, "CONFIGURATION_CHANGED_DURING_GENERATION");
}

export function isScheduleInfeasibleError(error: unknown): error is ApiError {
  return hasCode(error, "SCHEDULE_INFEASIBLE");
}

export function isInvalidConfigurationError(error: unknown): error is ApiError {
  return hasCode(error, "INVALID_CONFIGURATION") &&
    Array.isArray(error.body?.["errors"]) &&
    error.body["errors"].every((diagnostic) => {
      if (!isRecord(diagnostic)) {
        return false;
      }
      return typeof diagnostic["code"] === "string" && typeof diagnostic["message"] === "string";
    });
}
