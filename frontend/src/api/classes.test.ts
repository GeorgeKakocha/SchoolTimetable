import { afterEach, describe, expect, it, vi } from "vitest";
import { createClass, deleteClass, getClasses, updateClass } from "./classes";
import type { ClassesProjectionResponse, ClassSectionWriteRequest } from "./types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const VALID_PROJECTION: ClassesProjectionResponse = {
  configuration_locked: false,
  classes: [{ id: "class_1", name: "8-A" }],
};

const WRITE_REQUEST: ClassSectionWriteRequest = { name: "8-A" };

describe("classes api", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs the exact classes endpoint path with safely encoded segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getClasses("school 1", "year/1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/school%201/years/year%2F1/classes");
    expect(result).toEqual(VALID_PROJECTION);
  });

  it("POSTs the exact endpoint path with the exact request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: "class_1", name: "8-A" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await createClass("s1", "y1", WRITE_REQUEST);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/classes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(WRITE_REQUEST),
    });
    expect(result).toEqual({ id: "class_1", name: "8-A" });
  });

  it("PUTs the exact endpoint path (including the class ID) with the exact request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { id: "class_1", name: "8-B" }));
    vi.stubGlobal("fetch", fetchMock);

    await updateClass("s1", "y1", "class_1", { name: "8-B" });

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/classes/class_1", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "8-B" }),
    });
  });

  it("DELETEs the exact endpoint path with no request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { deleted_id: "class_1" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await deleteClass("s1", "y1", "class_1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/classes/class_1", { method: "DELETE" });
    expect(result).toEqual({ deleted_id: "class_1" });
  });

  it("propagates a structured 409 DUPLICATE_CLASS error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(409, { code: "DUPLICATE_CLASS", detail: "A class named 8-A already exists" })),
    );

    await expect(createClass("s1", "y1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 409,
      code: "DUPLICATE_CLASS",
    });
  });

  it("propagates a structured 409 CLASS_IN_USE error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          code: "CLASS_IN_USE",
          detail: "Class is referenced elsewhere",
          referenced_by: ["TEACHING_REQUIREMENT", "SUBGROUP"],
        }),
      ),
    );

    await expect(deleteClass("s1", "y1", "class_1")).rejects.toMatchObject({
      status: 409,
      code: "CLASS_IN_USE",
      body: { referenced_by: ["TEACHING_REQUIREMENT", "SUBGROUP"] },
    });
  });

  it("forwards an AbortSignal when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getClasses("s1", "y1", controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/classes", { signal: controller.signal });
  });
});
