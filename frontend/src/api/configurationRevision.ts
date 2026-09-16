import { deleteJson, getJson, postJson } from "./client";
import type { ConfigurationRevisionStateResponse } from "./types";

function configurationPath(schoolId: string, academicYearId: string): string {
  return `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/configuration`;
}

function statePath(schoolId: string, academicYearId: string): string {
  return `${configurationPath(schoolId, academicYearId)}/state`;
}

function draftPath(schoolId: string, academicYearId: string): string {
  return `${configurationPath(schoolId, academicYearId)}/draft`;
}

export function getConfigurationState(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<ConfigurationRevisionStateResponse> {
  return getJson<ConfigurationRevisionStateResponse>(statePath(schoolId, academicYearId), signal);
}

export function beginConfigurationDraft(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<ConfigurationRevisionStateResponse> {
  return postJson<ConfigurationRevisionStateResponse>(draftPath(schoolId, academicYearId), undefined, signal);
}

export function discardConfigurationDraft(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<ConfigurationRevisionStateResponse> {
  return deleteJson<ConfigurationRevisionStateResponse>(draftPath(schoolId, academicYearId), signal);
}
