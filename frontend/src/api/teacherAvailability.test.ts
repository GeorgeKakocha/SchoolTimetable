import { afterEach, describe, expect, it, vi } from "vitest";
import { getTeacherAvailability, replaceTeacherAvailability } from "./teacherAvailability";
import type { TeacherAvailabilityProjectionResponse, TeacherAvailabilityReplaceRequest } from "./types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const VALID_PROJECTION: TeacherAvailabilityProjectionResponse = {
  configuration_locked: false,
  teachers: [{ id: "teacher_1", name: "Ada Lovelace" }],
  days: [{ id: "mon", name: "Monday", index: 0 }],
  periods: [{ id: "p1", name: "Period 1", index: 0, block_id: "morning", is_instructional: true }],
  exceptions: [{ teacher_id: "teacher_1", day_id: "mon", period_id: "p1", status: "UNAVAILABLE" }],
};

describe("teacherAvailability api", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs the exact teacher-availability endpoint path with safely encoded segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getTeacherAvailability("school 1", "year/1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/school%201/years/year%2F1/teacher-availability");
    expect(result).toEqual(VALID_PROJECTION);
  });

  it("preserves a backend 404 detail in ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(404, { detail: "Scheduling configuration not found" })),
    );

    await expect(getTeacherAvailability("s1", "y1")).rejects.toMatchObject({
      status: 404,
      detail: "Scheduling configuration not found",
    });
  });

  it("forwards an AbortSignal when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getTeacherAvailability("s1", "y1", controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/teacher-availability", {
      signal: controller.signal,
    });
  });
});

const WRITE_REQUEST: TeacherAvailabilityReplaceRequest = {
  exceptions: [
    { day_id: "mon", period_id: "p1", status: "UNAVAILABLE" },
    { day_id: "tue", period_id: "p3", status: "PREFER_NOT" },
  ],
};

describe("replaceTeacherAvailability", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("PUTs the exact endpoint path (including the teacher ID) with the exact request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, { teacher_id: "teacher_1", exceptions: WRITE_REQUEST.exceptions }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await replaceTeacherAvailability("school 1", "year/1", "teacher_1", WRITE_REQUEST);

    expect(fetchMock).toHaveBeenCalledWith(
      "/schools/school%201/years/year%2F1/teacher-availability/teacher_1",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(WRITE_REQUEST),
      },
    );
    expect(result).toEqual({ teacher_id: "teacher_1", exceptions: WRITE_REQUEST.exceptions });
  });

  it("sends an empty exceptions array verbatim", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { teacher_id: "teacher_1", exceptions: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await replaceTeacherAvailability("s1", "y1", "teacher_1", { exceptions: [] });

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/teacher-availability/teacher_1", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ exceptions: [] }),
    });
  });

  it("propagates a structured 404 when the teacher does not exist", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(404, { detail: "Teacher not found" })));

    await expect(replaceTeacherAvailability("s1", "y1", "t_nobody", WRITE_REQUEST)).rejects.toMatchObject({
      status: 404,
      detail: "Teacher not found",
    });
  });

  it("propagates a structured 422 UNKNOWN_REFERENCE error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, {
          code: "UNKNOWN_REFERENCE",
          detail: "unknown day 'someday'",
          reference_kind: "day",
          reference_id: "someday",
        }),
      ),
    );

    await expect(replaceTeacherAvailability("s1", "y1", "teacher_1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 422,
      code: "UNKNOWN_REFERENCE",
      body: { reference_kind: "day", reference_id: "someday" },
    });
  });

  it("propagates a structured 409 when configuration is locked", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          code: "SCHEDULING_CONFIGURATION_LOCKED",
          detail: "Scheduling configuration is locked because a schedule already exists",
        }),
      ),
    );

    await expect(replaceTeacherAvailability("s1", "y1", "teacher_1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 409,
      code: "SCHEDULING_CONFIGURATION_LOCKED",
    });
  });

  it("forwards an AbortSignal when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { teacher_id: "teacher_1", exceptions: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await replaceTeacherAvailability("s1", "y1", "teacher_1", { exceptions: [] }, controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/teacher-availability/teacher_1", {
      method: "PUT",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ exceptions: [] }),
    });
  });
});
