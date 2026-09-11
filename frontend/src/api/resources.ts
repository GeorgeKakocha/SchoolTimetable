/**
 * Typed API module for the Resources catalog CRUD backend contract
 * (`docs/DECISIONS.md`, `api/schemas.py`, `api/resource_routes.py`) --
 * kept separate from `client.ts`, matching `specialActivities.ts`'s
 * established "one module per page-domain" convention. Every function
 * is a thin, one-line URL-building wrapper around `client.ts`'s shared
 * `getJson`/`postJson`/`putJson`/`deleteJson`; no backend validation or
 * business rule is duplicated here.
 *
 * `Resource` is the existing `domain.resources.Resource(id, name,
 * capacity)` -- user-facing label "Rooms & Resources" -- this module
 * never exposes a natural ID as anything other than an opaque `id`
 * string, and never exposes an ordinal or database surrogate ID.
 */
import { deleteJson, getJson, postJson, putJson } from "./client";

export interface ResourceItem {
  id: string;
  name: string;
  capacity: number;
}

export interface ResourcesProjectionResponse {
  configuration_locked: boolean;
  resources: ResourceItem[];
}

export interface ResourceWriteRequest {
  name: string;
  capacity: number;
}

export interface ResourceWriteResponse {
  id: string;
  name: string;
  capacity: number;
}

export interface ResourceDeleteResponse {
  deleted_id: string;
}

function resourcesPath(schoolId: string, academicYearId: string): string {
  return `/schools/${encodeURIComponent(schoolId)}/years/${encodeURIComponent(academicYearId)}/resources`;
}

export function getResources(
  schoolId: string,
  academicYearId: string,
  signal?: AbortSignal,
): Promise<ResourcesProjectionResponse> {
  return getJson<ResourcesProjectionResponse>(resourcesPath(schoolId, academicYearId), signal);
}

export function createResource(
  schoolId: string,
  academicYearId: string,
  body: ResourceWriteRequest,
  signal?: AbortSignal,
): Promise<ResourceWriteResponse> {
  return postJson<ResourceWriteResponse>(resourcesPath(schoolId, academicYearId), body, signal);
}

export function updateResource(
  schoolId: string,
  academicYearId: string,
  resourceId: string,
  body: ResourceWriteRequest,
  signal?: AbortSignal,
): Promise<ResourceWriteResponse> {
  const path = `${resourcesPath(schoolId, academicYearId)}/${encodeURIComponent(resourceId)}`;
  return putJson<ResourceWriteResponse>(path, body, signal);
}

export function deleteResource(
  schoolId: string,
  academicYearId: string,
  resourceId: string,
  signal?: AbortSignal,
): Promise<ResourceDeleteResponse> {
  const path = `${resourcesPath(schoolId, academicYearId)}/${encodeURIComponent(resourceId)}`;
  return deleteJson<ResourceDeleteResponse>(path, signal);
}
