import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createSpecialActivity,
  deleteSpecialActivity,
  getSpecialActivities,
  updateSpecialActivity,
} from "./specialActivities";
import type { SpecialActivitiesProjectionResponse, SpecialActivityWriteRequest } from "./specialActivities";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const VALID_PROJECTION: SpecialActivitiesProjectionResponse = {
  configuration_locked: false,
  special_activities: [{ id: "club_1", name: "Debate Club" }],
};

const WRITE_REQUEST: SpecialActivityWriteRequest = { name: "Debate Club" };

describe("specialActivities api", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs the exact special-activities endpoint path with safely encoded segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getSpecialActivities("school 1", "year/1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/school%201/years/year%2F1/special-activities");
    expect(result).toEqual(VALID_PROJECTION);
  });

  it("POSTs the exact endpoint path with the exact request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: "club_1", name: "Debate Club" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await createSpecialActivity("s1", "y1", WRITE_REQUEST);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/special-activities", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(WRITE_REQUEST),
    });
    expect(result).toEqual({ id: "club_1", name: "Debate Club" });
  });

  it("PUTs the exact endpoint path (including the special activity ID) with the exact request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { id: "club_1", name: "Art Club" }));
    vi.stubGlobal("fetch", fetchMock);

    await updateSpecialActivity("s1", "y1", "club_1", { name: "Art Club" });

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/special-activities/club_1", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Art Club" }),
    });
  });

  it("DELETEs the exact endpoint path with no request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { deleted_id: "club_1" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await deleteSpecialActivity("s1", "y1", "club_1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/special-activities/club_1", { method: "DELETE" });
    expect(result).toEqual({ deleted_id: "club_1" });
  });

  it("propagates a structured 409 DUPLICATE_SPECIAL_ACTIVITY error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, { code: "DUPLICATE_SPECIAL_ACTIVITY", detail: "A special activity named Debate Club already exists" }),
      ),
    );

    await expect(createSpecialActivity("s1", "y1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 409,
      code: "DUPLICATE_SPECIAL_ACTIVITY",
    });
  });

  it("propagates a structured 409 SPECIAL_ACTIVITY_IN_USE error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          code: "SPECIAL_ACTIVITY_IN_USE",
          detail: "Special activity is referenced elsewhere",
          referenced_by: ["RESERVED_BLOCK"],
        }),
      ),
    );

    await expect(deleteSpecialActivity("s1", "y1", "club_1")).rejects.toMatchObject({
      status: 409,
      code: "SPECIAL_ACTIVITY_IN_USE",
      body: { referenced_by: ["RESERVED_BLOCK"] },
    });
  });

  it("propagates a 404 'Special activity not found'", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(404, { detail: "Special activity not found" })));

    await expect(updateSpecialActivity("s1", "y1", "club_1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 404,
      detail: "Special activity not found",
    });
  });

  it("forwards an AbortSignal when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getSpecialActivities("s1", "y1", controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/special-activities", { signal: controller.signal });
  });
});
