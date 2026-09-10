import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createReservedActivity,
  deleteReservedActivity,
  getReservedActivities,
  updateReservedActivity,
} from "./reservedActivities";
import type { ReservedActivitiesProjectionResponse, ReservedActivityWriteRequest } from "./reservedActivities";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const VALID_PROJECTION: ReservedActivitiesProjectionResponse = {
  configuration_locked: false,
  special_activities: [{ id: "club_debate", name: "Debate Club" }],
  teachers: [{ id: "teacher_a", name: "Maia Beridze" }],
  class_sections: [{ id: "class_8a", name: "8A" }],
  days: [{ id: "mon", name: "Monday", index: 0 }],
  periods: [{ id: "p1", name: "Period 1", index: 0, is_instructional: true }],
  reserved_activities: [
    {
      id: "reserved_block_1",
      special_activity_id: "club_debate",
      class_section_ids: ["class_8a"],
      teacher_id: null,
      slots: [{ day_id: "mon", period_id: "p1" }],
    },
  ],
};

const WRITE_REQUEST: ReservedActivityWriteRequest = {
  special_activity_id: "club_debate",
  class_section_ids: ["class_8a"],
  teacher_id: null,
  slots: [{ day_id: "mon", period_id: "p1" }],
};

describe("reservedActivities api", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs the exact reserved-activities endpoint path with safely encoded segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getReservedActivities("school 1", "year/1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/school%201/years/year%2F1/reserved-activities");
    expect(result).toEqual(VALID_PROJECTION);
  });

  it("POSTs the exact endpoint path with the exact request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(201, {
        id: "reserved_block_1",
        special_activity_id: "club_debate",
        class_section_ids: ["class_8a"],
        teacher_id: null,
        slots: [{ day_id: "mon", period_id: "p1" }],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createReservedActivity("s1", "y1", WRITE_REQUEST);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/reserved-activities", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(WRITE_REQUEST),
    });
    expect(result.id).toBe("reserved_block_1");
  });

  it("PUTs the exact endpoint path (including the reserved activity ID) with the exact request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        id: "reserved_block_1",
        special_activity_id: "club_debate",
        class_section_ids: ["class_8a", "class_8b"],
        teacher_id: "teacher_a",
        slots: [{ day_id: "mon", period_id: "p1" }],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const updatedRequest: ReservedActivityWriteRequest = {
      special_activity_id: "club_debate",
      class_section_ids: ["class_8a", "class_8b"],
      teacher_id: "teacher_a",
      slots: [{ day_id: "mon", period_id: "p1" }],
    };

    await updateReservedActivity("s1", "y1", "reserved_block_1", updatedRequest);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/reserved-activities/reserved_block_1", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updatedRequest),
    });
  });

  it("DELETEs the exact endpoint path with no request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { deleted_id: "reserved_block_1" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await deleteReservedActivity("s1", "y1", "reserved_block_1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/reserved-activities/reserved_block_1", {
      method: "DELETE",
    });
    expect(result).toEqual({ deleted_id: "reserved_block_1" });
  });

  it("propagates a structured 422 INVALID_RESERVED_ACTIVITY error with its diagnostics", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, {
          code: "INVALID_RESERVED_ACTIVITY",
          detail: "invalid reserved activity",
          errors: [{ code: "RESERVED_BLOCK_TEACHER_UNAVAILABLE", message: "teacher unavailable", context: {} }],
        }),
      ),
    );

    await expect(createReservedActivity("s1", "y1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 422,
      code: "INVALID_RESERVED_ACTIVITY",
      body: { errors: [{ code: "RESERVED_BLOCK_TEACHER_UNAVAILABLE" }] },
    });
  });

  it("propagates a structured 422 NON_SPECIAL_ACTIVITY_TARGET error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, {
          code: "NON_SPECIAL_ACTIVITY_TARGET",
          detail: "Selected activity is not a special activity",
          activity_id: "activity_math",
        }),
      ),
    );

    await expect(createReservedActivity("s1", "y1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 422,
      code: "NON_SPECIAL_ACTIVITY_TARGET",
    });
  });

  it("propagates a structured 422 UNKNOWN_REFERENCE error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, {
          code: "UNKNOWN_REFERENCE",
          detail: "Unknown class",
          reference_kind: "class_section",
          reference_id: "class_missing",
        }),
      ),
    );

    await expect(createReservedActivity("s1", "y1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 422,
      code: "UNKNOWN_REFERENCE",
      body: { reference_kind: "class_section" },
    });
  });

  it("propagates a structured 409 SCHEDULING_CONFIGURATION_LOCKED error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          code: "SCHEDULING_CONFIGURATION_LOCKED",
          detail: "Scheduling configuration is locked because a schedule already exists",
        }),
      ),
    );

    await expect(createReservedActivity("s1", "y1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 409,
      code: "SCHEDULING_CONFIGURATION_LOCKED",
    });
  });

  it("propagates a 404 'Reserved activity not found'", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(404, { detail: "Reserved activity not found" })));

    await expect(updateReservedActivity("s1", "y1", "reserved_block_1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 404,
      detail: "Reserved activity not found",
    });
  });

  it("forwards an AbortSignal when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getReservedActivities("s1", "y1", controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/reserved-activities", { signal: controller.signal });
  });
});
