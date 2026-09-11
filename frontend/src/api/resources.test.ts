import { afterEach, describe, expect, it, vi } from "vitest";
import { createResource, deleteResource, getResources, updateResource } from "./resources";
import type { ResourcesProjectionResponse, ResourceWriteRequest } from "./resources";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const VALID_PROJECTION: ResourcesProjectionResponse = {
  configuration_locked: false,
  resources: [{ id: "resource_1", name: "Gym", capacity: 1 }],
};

const WRITE_REQUEST: ResourceWriteRequest = { name: "Gym", capacity: 1 };

describe("resources api", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs the exact resources endpoint path with safely encoded segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getResources("school 1", "year/1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/school%201/years/year%2F1/resources");
    expect(result).toEqual(VALID_PROJECTION);
  });

  it("POSTs the exact endpoint path with the exact request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: "resource_1", name: "Gym", capacity: 1 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await createResource("s1", "y1", WRITE_REQUEST);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/resources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(WRITE_REQUEST),
    });
    expect(result).toEqual({ id: "resource_1", name: "Gym", capacity: 1 });
  });

  it("PUTs the exact endpoint path (including the resource ID) with the exact request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, { id: "resource_1", name: "Gym A", capacity: 2 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await updateResource("s1", "y1", "resource_1", { name: "Gym A", capacity: 2 });

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/resources/resource_1", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Gym A", capacity: 2 }),
    });
  });

  it("DELETEs the exact endpoint path with no request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { deleted_id: "resource_1" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await deleteResource("s1", "y1", "resource_1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/resources/resource_1", { method: "DELETE" });
    expect(result).toEqual({ deleted_id: "resource_1" });
  });

  it("propagates a structured 409 DUPLICATE_RESOURCE error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(409, { code: "DUPLICATE_RESOURCE", detail: "a resource named 'Gym' already exists" })),
    );

    await expect(createResource("s1", "y1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 409,
      code: "DUPLICATE_RESOURCE",
    });
  });

  it("propagates a structured 409 RESOURCE_IN_USE error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          code: "RESOURCE_IN_USE",
          detail: "Resource is referenced elsewhere",
          referenced_by: ["TEACHING_REQUIREMENT", "RESERVED_BLOCK"],
        }),
      ),
    );

    await expect(deleteResource("s1", "y1", "resource_1")).rejects.toMatchObject({
      status: 409,
      code: "RESOURCE_IN_USE",
      body: { referenced_by: ["TEACHING_REQUIREMENT", "RESERVED_BLOCK"] },
    });
  });

  it("propagates a structured 422 INVALID_RESOURCE error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, {
          code: "INVALID_RESOURCE",
          detail: "invalid resource",
          errors: [{ code: "INVALID_RESOURCE_CAPACITY", message: "capacity must be at least 1" }],
        }),
      ),
    );

    await expect(createResource("s1", "y1", { name: "Gym", capacity: 0 })).rejects.toMatchObject({
      status: 422,
      code: "INVALID_RESOURCE",
    });
  });

  it("propagates a 404 'Resource not found'", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(404, { detail: "Resource not found" })));

    await expect(updateResource("s1", "y1", "resource_1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 404,
      detail: "Resource not found",
    });
  });

  it("forwards an AbortSignal when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getResources("s1", "y1", controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/resources", { signal: controller.signal });
  });
});
