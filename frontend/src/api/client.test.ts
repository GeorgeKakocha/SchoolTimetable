import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, deleteJson, getClassTimetable, getSchedulingConfigIndex, postJson, putJson } from "./client";
import type { ClassTimetableResponse } from "./types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function textResponse(status: number, body: string): Response {
  return new Response(body, { status });
}

const VALID_CLASS_TIMETABLE: ClassTimetableResponse = {
  school_id: "s1",
  school_name: "Pilot School",
  academic_year_id: "y1",
  academic_year_label: "2025/2026",
  class_section_id: "8a",
  class_section_name: "8-A",
  version_number: 1,
  solver_status: "OPTIMAL",
  total_soft_penalty: 0,
  created_at: "2026-01-01T00:00:00Z",
  is_active: true,
  days: [{ id: "mon", name: "Monday" }],
  rows: [
    {
      period_id: "p1",
      period_name: "Period 1",
      cells: [{ day_id: "mon", entries: [] }],
    },
  ],
};

const VALID_CONFIG_SUPERSET = {
  school: { id: "s1", name: "Pilot School" },
  academic_year: { id: "y1", label: "2025/2026" },
  class_sections: [{ id: "8a", name: "8-A" }],
  // Fields the real /config response also carries, deliberately unused
  // by this frontend slice -- proves the client reduces the superset
  // rather than requiring/echoing them.
  days: [{ id: "mon", name: "Monday", index: 0 }],
  teachers: [{ id: "t1", name: "Teacher One" }],
  activities: [{ id: "math", name: "Mathematics", kind: "ORDINARY" }],
};

describe("api client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests the exact config endpoint path with safely encoded segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_CONFIG_SUPERSET));
    vi.stubGlobal("fetch", fetchMock);

    await getSchedulingConfigIndex("school 1", "year/1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/school%201/years/year%2F1/config");
  });

  it("requests the exact class timetable endpoint path with safely encoded segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_CLASS_TIMETABLE));
    vi.stubGlobal("fetch", fetchMock);

    await getClassTimetable("school 1", "year/1", "class#1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/schools/school%201/years/year%2F1/schedule/active/classes/class%231",
    );
  });

  it("returns a successful class timetable response unchanged", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, VALID_CLASS_TIMETABLE)));

    const result = await getClassTimetable("s1", "y1", "8a");

    expect(result).toEqual(VALID_CLASS_TIMETABLE);
  });

  it("reduces the /config superset response to the minimal config-index shape", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, VALID_CONFIG_SUPERSET)));

    const index = await getSchedulingConfigIndex("s1", "y1");

    expect(index).toEqual({
      school: { id: "s1", name: "Pilot School" },
      academic_year: { id: "y1", label: "2025/2026" },
      class_sections: [{ id: "8a", name: "8-A" }],
    });
  });

  it("preserves a backend 404 detail in ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(404, { detail: "Active schedule not found" })),
    );

    await expect(getClassTimetable("s1", "y1", "8a")).rejects.toMatchObject({
      status: 404,
      detail: "Active schedule not found",
    });
  });

  it("falls back to a safe generic detail for a malformed/non-JSON error response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(textResponse(500, "<html>Internal Server Error</html>")),
    );

    await expect(getClassTimetable("s1", "y1", "8a")).rejects.toMatchObject({
      status: 500,
      detail: "Request failed.",
    });
  });

  it("never embeds an absolute localhost/127.0.0.1 backend URL in the request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_CLASS_TIMETABLE));
    vi.stubGlobal("fetch", fetchMock);

    await getClassTimetable("s1", "y1", "8a");

    const calledUrl = fetchMock.mock.calls[0]?.[0] as string;
    expect(calledUrl.startsWith("/")).toBe(true);
    expect(calledUrl).not.toContain("localhost");
    expect(calledUrl).not.toContain("127.0.0.1");
  });

  it("forwards an AbortSignal to fetch for getClassTimetable when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_CLASS_TIMETABLE));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getClassTimetable("s1", "y1", "8a", controller.signal);

    expect(fetchMock).toHaveBeenCalledWith(
      "/schools/s1/years/y1/schedule/active/classes/8a",
      { signal: controller.signal },
    );
  });

  it("forwards an AbortSignal to fetch for getSchedulingConfigIndex when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_CONFIG_SUPERSET));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getSchedulingConfigIndex("s1", "y1", controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/config", { signal: controller.signal });
  });

  // -- Phase 3C.3b write helpers (postJson/putJson/deleteJson) ----------

  describe("postJson", () => {
    it("sends a JSON POST with Content-Type and the exact stringified body", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: "r1", warnings: [] }));
      vi.stubGlobal("fetch", fetchMock);
      const requestBody = { teacher_id: "t1", participant_group_id: "g1", activity_id: "math", weekly_periods: 4 };

      const result = await postJson("/schools/s1/years/y1/teaching-assignments", requestBody);

      expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/teaching-assignments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });
      expect(result).toEqual({ id: "r1", warnings: [] });
    });

    it("forwards an AbortSignal when supplied", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: "r1", warnings: [] }));
      vi.stubGlobal("fetch", fetchMock);
      const controller = new AbortController();

      await postJson("/schools/s1/years/y1/teaching-assignments", { a: 1 }, controller.signal);

      expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/teaching-assignments", {
        method: "POST",
        signal: controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ a: 1 }),
      });
    });
  });

  describe("putJson", () => {
    it("sends a JSON PUT with Content-Type and the exact stringified body", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { id: "r1", warnings: [] }));
      vi.stubGlobal("fetch", fetchMock);
      const requestBody = { teacher_id: "t1", participant_group_id: "g1", activity_id: "math", weekly_periods: 5 };

      await putJson("/schools/s1/years/y1/teaching-assignments/r1", requestBody);

      expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/teaching-assignments/r1", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });
    });
  });

  describe("deleteJson", () => {
    it("sends a DELETE with no body and no Content-Type header", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { deleted_id: "r1", warnings: [] }));
      vi.stubGlobal("fetch", fetchMock);

      const result = await deleteJson("/schools/s1/years/y1/teaching-assignments/r1");

      expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/teaching-assignments/r1", { method: "DELETE" });
      expect(result).toEqual({ deleted_id: "r1", warnings: [] });
    });

    it("forwards an AbortSignal when supplied, still with no body", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { deleted_id: "r1", warnings: [] }));
      vi.stubGlobal("fetch", fetchMock);
      const controller = new AbortController();

      await deleteJson("/schools/s1/years/y1/teaching-assignments/r1", controller.signal);

      expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/teaching-assignments/r1", {
        method: "DELETE",
        signal: controller.signal,
      });
    });
  });

  describe("structured ApiError (code/body) for mutation error responses", () => {
    it("captures code and the full parsed body alongside detail for a structured 409", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          jsonResponse(409, {
            code: "DUPLICATE_TEACHING_ASSIGNMENT",
            detail: "a teaching assignment already exists",
            teacher_id: "t1",
            participant_group_id: "g1",
            activity_id: "math",
          }),
        ),
      );

      await expect(postJson("/x", {})).rejects.toMatchObject({
        status: 409,
        detail: "a teaching assignment already exists",
        code: "DUPLICATE_TEACHING_ASSIGNMENT",
        body: {
          code: "DUPLICATE_TEACHING_ASSIGNMENT",
          detail: "a teaching assignment already exists",
          teacher_id: "t1",
          participant_group_id: "g1",
          activity_id: "math",
        },
      });
    });

    it("keeps ApiError.detail a safe generic string, never an array, for FastAPI's generic Pydantic 422 body", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          jsonResponse(422, {
            detail: [{ type: "greater_than", loc: ["body", "weekly_periods"], msg: "Input should be greater than 0" }],
          }),
        ),
      );

      let caught: unknown;
      try {
        await postJson("/x", {});
      } catch (error) {
        caught = error;
      }

      expect(caught).toBeInstanceOf(ApiError);
      const apiError = caught as ApiError;
      expect(typeof apiError.detail).toBe("string");
      expect(apiError.detail).toBe("Request failed.");
      expect(apiError.code).toBeUndefined();
      // The raw body (with its array `detail`) is still available for a
      // caller that specifically wants it -- just never promoted to
      // `.detail` itself.
      expect(Array.isArray(apiError.body?.["detail"])).toBe(true);
    });

    it("still falls back to a safe generic detail with no code/body for a malformed/non-JSON error response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(textResponse(500, "<html>Internal Server Error</html>")));

      await expect(postJson("/x", {})).rejects.toMatchObject({
        status: 500,
        detail: "Request failed.",
        code: undefined,
        body: undefined,
      });
    });

    it("propagates a network failure from postJson unchanged", async () => {
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

      await expect(postJson("/x", {})).rejects.toThrow("Failed to fetch");
    });
  });
});
