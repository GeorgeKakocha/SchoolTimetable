import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createTeachingAssignment,
  deleteTeachingAssignment,
  getTeachingAssignments,
  updateTeachingAssignment,
} from "./teachingAssignments";
import type { TeachingAssignmentsProjectionResponse, TeachingAssignmentWriteRequest } from "./types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const VALID_PROJECTION: TeachingAssignmentsProjectionResponse = {
  configuration_locked: false,
  assignments: [],
  teachers: [{ id: "t1", name: "Teacher One" }],
  whole_class_targets: [
    { class_section_id: "8a", class_section_name: "8-A", participant_group_id: "g1", participant_group_name: "8-A" },
  ],
  activities: [{ id: "math", name: "Mathematics" }],
  teacher_workloads: [{ teacher_id: "t1", teacher_name: "Teacher One", total_weekly_periods: 0 }],
};

describe("teachingAssignments api", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests the exact teaching-assignments endpoint path with safely encoded segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);

    await getTeachingAssignments("school 1", "year/1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/school%201/years/year%2F1/teaching-assignments");
  });

  it("returns a successful projection response unchanged", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION)));

    const result = await getTeachingAssignments("s1", "y1");

    expect(result).toEqual(VALID_PROJECTION);
  });

  it("preserves a backend 404 detail in ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(404, { detail: "Scheduling configuration not found" })),
    );

    await expect(getTeachingAssignments("s1", "y1")).rejects.toMatchObject({
      status: 404,
      detail: "Scheduling configuration not found",
    });
  });

  it("never embeds an absolute localhost/127.0.0.1 backend URL in the request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);

    await getTeachingAssignments("s1", "y1");

    const calledUrl = fetchMock.mock.calls[0]?.[0] as string;
    expect(calledUrl.startsWith("/")).toBe(true);
    expect(calledUrl).not.toContain("localhost");
    expect(calledUrl).not.toContain("127.0.0.1");
  });

  it("forwards an AbortSignal to fetch when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getTeachingAssignments("s1", "y1", controller.signal);

    expect(fetchMock).toHaveBeenCalledWith(
      "/schools/s1/years/y1/teaching-assignments",
      { signal: controller.signal },
    );
  });
});

const WRITE_REQUEST: TeachingAssignmentWriteRequest = {
  teacher_id: "t1",
  participant_group_id: "g1",
  activity_id: "math",
  weekly_periods: 4,
};

describe("createTeachingAssignment", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs the exact endpoint path with the exact request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: "r1", warnings: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await createTeachingAssignment("school 1", "year/1", WRITE_REQUEST);

    expect(fetchMock).toHaveBeenCalledWith("/schools/school%201/years/year%2F1/teaching-assignments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(WRITE_REQUEST),
    });
    expect(result).toEqual({ id: "r1", warnings: [] });
  });

  it("preserves warnings on a successful create", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(201, {
          id: "r1",
          warnings: [{ code: "TEACHER_OVERLOADED", message: "Teacher has a high weekly load", context: {} }],
        }),
      ),
    );

    const result = await createTeachingAssignment("s1", "y1", WRITE_REQUEST);

    expect(result.warnings).toEqual([
      { code: "TEACHER_OVERLOADED", message: "Teacher has a high weekly load", context: {} },
    ]);
  });

  it("propagates a structured 409 duplicate error", async () => {
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

    await expect(createTeachingAssignment("s1", "y1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 409,
      code: "DUPLICATE_TEACHING_ASSIGNMENT",
    });
  });

  it("forwards an AbortSignal when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: "r1", warnings: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await createTeachingAssignment("s1", "y1", WRITE_REQUEST, controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/teaching-assignments", {
      method: "POST",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(WRITE_REQUEST),
    });
  });
});

describe("updateTeachingAssignment", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("PUTs the exact endpoint path (including the requirement ID) with the exact request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { id: "math_8a", warnings: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await updateTeachingAssignment("school 1", "year/1", "math_8a", WRITE_REQUEST);

    expect(fetchMock).toHaveBeenCalledWith(
      "/schools/school%201/years/year%2F1/teaching-assignments/math_8a",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(WRITE_REQUEST),
      },
    );
    expect(result).toEqual({ id: "math_8a", warnings: [] });
  });

  it("safely encodes a requirement ID with special characters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { id: "r/1", warnings: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await updateTeachingAssignment("s1", "y1", "r/1", WRITE_REQUEST);

    expect(fetchMock).toHaveBeenCalledWith(
      "/schools/s1/years/y1/teaching-assignments/r%2F1",
      expect.objectContaining({ method: "PUT" }),
    );
  });

  it("propagates a 404 when the assignment no longer exists", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(404, { detail: "Teaching assignment not found" })),
    );

    await expect(updateTeachingAssignment("s1", "y1", "gone", WRITE_REQUEST)).rejects.toMatchObject({
      status: 404,
      detail: "Teaching assignment not found",
    });
  });
});

describe("deleteTeachingAssignment", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("DELETEs the exact endpoint path with no request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { deleted_id: "math_8a", warnings: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await deleteTeachingAssignment("school 1", "year/1", "math_8a");

    expect(fetchMock).toHaveBeenCalledWith(
      "/schools/school%201/years/year%2F1/teaching-assignments/math_8a",
      { method: "DELETE" },
    );
    expect(result).toEqual({ deleted_id: "math_8a", warnings: [] });
  });

  it("propagates a structured 409 when the target is not editable (stale-client defense)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          code: "ADVANCED_REQUIREMENT_NOT_EDITABLE",
          detail: "not plain/editable",
          advanced_reasons: ["block_policy"],
        }),
      ),
    );

    await expect(deleteTeachingAssignment("s1", "y1", "math_8a")).rejects.toMatchObject({
      status: 409,
      code: "ADVANCED_REQUIREMENT_NOT_EDITABLE",
      body: { advanced_reasons: ["block_policy"] },
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

    await expect(deleteTeachingAssignment("s1", "y1", "science_8a")).rejects.toMatchObject({
      status: 409,
      code: "SCHEDULING_CONFIGURATION_LOCKED",
    });
  });

  it("forwards an AbortSignal when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { deleted_id: "r1", warnings: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await deleteTeachingAssignment("s1", "y1", "r1", controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/teaching-assignments/r1", {
      method: "DELETE",
      signal: controller.signal,
    });
  });
});
