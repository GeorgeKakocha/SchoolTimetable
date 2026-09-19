import { getJson, postJson } from "./client";
import type {
  SynchronizedSplitConfigResponse,
  SynchronizedSplitCreateRequest,
  SynchronizedSplitCreateResponse,
} from "./types";

function configurationPath(schoolId: string, academicYearId: string): string {
  return `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/config`;
}

function synchronizedSplitsPath(schoolId: string, academicYearId: string): string {
  return `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}`
    + "/configuration/synchronized-splits";
}

export function getSynchronizedSplitConfig(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<SynchronizedSplitConfigResponse> {
  return getJson<SynchronizedSplitConfigResponse>(configurationPath(schoolId, academicYearId), signal);
}

export function createSynchronizedSplit(
  schoolId: string,
  academicYearId: string,
  body: SynchronizedSplitCreateRequest,
): Promise<SynchronizedSplitCreateResponse> {
  return postJson<SynchronizedSplitCreateResponse>(synchronizedSplitsPath(schoolId, academicYearId), body);
}
