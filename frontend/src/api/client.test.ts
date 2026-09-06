import { afterEach, describe, expect, it, vi } from "vitest";
import { getClassTimetable, getSchedulingConfigIndex } from "./client";
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
});
