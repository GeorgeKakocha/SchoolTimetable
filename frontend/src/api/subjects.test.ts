import { afterEach, describe, expect, it, vi } from "vitest";
import { createSubject, deleteSubject, getSubjects, updateSubject } from "./subjects";
import type { SubjectsProjectionResponse, SubjectWriteRequest } from "./types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const VALID_PROJECTION: SubjectsProjectionResponse = {
  configuration_locked: false,
  subjects: [{ id: "activity_1", name: "Mathematics" }],
};

const WRITE_REQUEST: SubjectWriteRequest = { name: "Mathematics" };

describe("subjects api", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs the exact subjects endpoint path with safely encoded segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getSubjects("school 1", "year/1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/school%201/years/year%2F1/subjects");
    expect(result).toEqual(VALID_PROJECTION);
  });

  it("POSTs the exact endpoint path with the exact request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: "activity_1", name: "Mathematics" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await createSubject("s1", "y1", WRITE_REQUEST);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/subjects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(WRITE_REQUEST),
    });
    expect(result).toEqual({ id: "activity_1", name: "Mathematics" });
  });

  it("PUTs the exact endpoint path (including the subject ID) with the exact request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { id: "activity_1", name: "Math" }));
    vi.stubGlobal("fetch", fetchMock);

    await updateSubject("s1", "y1", "activity_1", { name: "Math" });

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/subjects/activity_1", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Math" }),
    });
  });

  it("DELETEs the exact endpoint path with no request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { deleted_id: "activity_1" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await deleteSubject("s1", "y1", "activity_1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/subjects/activity_1", { method: "DELETE" });
    expect(result).toEqual({ deleted_id: "activity_1" });
  });

  it("propagates a structured 409 DUPLICATE_SUBJECT error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(409, { code: "DUPLICATE_SUBJECT", detail: "A subject named Mathematics already exists" })),
    );

    await expect(createSubject("s1", "y1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 409,
      code: "DUPLICATE_SUBJECT",
    });
  });

  it("propagates a structured 409 SUBJECT_IN_USE error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          code: "SUBJECT_IN_USE",
          detail: "Subject is referenced elsewhere",
          referenced_by: ["TEACHING_REQUIREMENT"],
        }),
      ),
    );

    await expect(deleteSubject("s1", "y1", "activity_1")).rejects.toMatchObject({
      status: 409,
      code: "SUBJECT_IN_USE",
      body: { referenced_by: ["TEACHING_REQUIREMENT"] },
    });
  });

  it("propagates a 404 'Subject not found' (missing or CLUB target, indistinguishable)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(404, { detail: "Subject not found" })));

    await expect(updateSubject("s1", "y1", "club_1", WRITE_REQUEST)).rejects.toMatchObject({
      status: 404,
      detail: "Subject not found",
    });
  });

  it("forwards an AbortSignal when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getSubjects("s1", "y1", controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/subjects", { signal: controller.signal });
  });
});
