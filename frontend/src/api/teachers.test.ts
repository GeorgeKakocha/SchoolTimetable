import { afterEach, describe, expect, it, vi } from "vitest";
import { createTeacher, deleteTeacher, getTeachers, updateTeacher } from "./teachers";
import type { TeachersProjectionResponse, TeacherWriteRequest } from "./types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const VALID_PROJECTION: TeachersProjectionResponse = {
  configuration_locked: false,
  teachers: [{ id: "teacher_1", first_name: "Ada", last_name: "Lovelace", name: "Ada Lovelace" }],
};

const WRITE_REQUEST: TeacherWriteRequest = { first_name: "Ada", last_name: "Lovelace" };

describe("teachers api", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs the exact teachers endpoint path with safely encoded segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getTeachers("school 1", "year/1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/school%201/years/year%2F1/teachers");
    expect(result).toEqual(VALID_PROJECTION);
  });

  it("POSTs the exact endpoint path with the exact request body", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(201, { id: "teacher_1", first_name: "Ada", last_name: "Lovelace", name: "Ada Lovelace" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await createTeacher("s1", "y1", WRITE_REQUEST);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/teachers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(WRITE_REQUEST),
    });
    expect(result).toEqual({ id: "teacher_1", first_name: "Ada", last_name: "Lovelace", name: "Ada Lovelace" });
  });

  it("PUTs the exact endpoint path (including the teacher ID) with the exact request body", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { id: "teacher_1", first_name: "Ada", last_name: "L.", name: "Ada L." }));
    vi.stubGlobal("fetch", fetchMock);

    await updateTeacher("s1", "y1", "teacher_1", { first_name: "Ada", last_name: "L." });

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/teachers/teacher_1", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ first_name: "Ada", last_name: "L." }),
    });
  });

  it("DELETEs the exact endpoint path with no request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { deleted_id: "teacher_1" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await deleteTeacher("s1", "y1", "teacher_1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/teachers/teacher_1", { method: "DELETE" });
    expect(result).toEqual({ deleted_id: "teacher_1" });
  });

  it("propagates a structured 409 TEACHER_IN_USE error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          code: "TEACHER_IN_USE",
          detail: "Teacher is referenced elsewhere",
          referenced_by: ["TEACHING_REQUIREMENT"],
        }),
      ),
    );

    await expect(deleteTeacher("s1", "y1", "teacher_1")).rejects.toMatchObject({
      status: 409,
      code: "TEACHER_IN_USE",
      body: { referenced_by: ["TEACHING_REQUIREMENT"] },
    });
  });

  it("forwards an AbortSignal when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getTeachers("s1", "y1", controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/teachers", { signal: controller.signal });
  });
});
