import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { beginConfigurationDraft, discardConfigurationDraft, getConfigurationState } from "./configurationRevision";
import type { ConfigurationRevisionStateResponse } from "./types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const STATE: ConfigurationRevisionStateResponse = {
  published_revision_number: 1,
  draft_revision_number: 2,
  configuration_locked: false,
  timetable_out_of_date: true,
};

describe("configuration revision API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("gets configuration state with encoded path segments and preserves the typed response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, STATE));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getConfigurationState("school 1", "year/1")).resolves.toEqual(STATE);
    expect(fetchMock).toHaveBeenCalledWith("/schools/school%201/years/year%2F1/configuration/state");
  });

  it("opens a draft with an exact bodyless POST", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, STATE));
    vi.stubGlobal("fetch", fetchMock);

    await beginConfigurationDraft("s1", "y1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/configuration/draft", { method: "POST" });
  });

  it("discards a draft with an exact bodyless DELETE", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ...STATE, draft_revision_number: null }));
    vi.stubGlobal("fetch", fetchMock);

    await discardConfigurationDraft("s1", "y1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/configuration/draft", { method: "DELETE" });
  });

  it("keeps structured backend errors in the shared ApiError mechanism", async () => {
    const body = { code: "NO_CONFIGURATION_DRAFT", detail: "No configuration draft is open" };
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(409, body))));

    await expect(discardConfigurationDraft("s1", "y1")).rejects.toBeInstanceOf(ApiError);
    await expect(discardConfigurationDraft("s1", "y1")).rejects.toMatchObject({
      status: 409,
      code: "NO_CONFIGURATION_DRAFT",
      body,
    });
  });
});
