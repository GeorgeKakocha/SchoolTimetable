import { afterEach, describe, expect, it, vi } from "vitest";
import { getTeachingAssignments } from "./teachingAssignments";
import type { TeachingAssignmentsProjectionResponse } from "./types";

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
