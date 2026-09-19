import { afterEach, describe, expect, it, vi } from "vitest";
import { createSynchronizedSplit, getSynchronizedSplitConfig } from "./synchronizedSplits";
import type { SynchronizedSplitCreateRequest } from "./types";

afterEach(() => vi.unstubAllGlobals());

describe("synchronized splits API", () => {
  it("reads the authoritative configuration with encoded runtime IDs", async () => {
    const response = { class_sections: [], teachers: [], activities: [], participant_groups: [], teaching_requirements: [] };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(response), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getSynchronizedSplitConfig("school one", "year/one")).resolves.toEqual(response);
    expect(fetchMock).toHaveBeenCalledWith("/schools/school%20one/years/year%2Fone/config");
  });

  it("posts only the user-entered aggregate payload", async () => {
    const result = {
      split_group_id: "server-split", class_section_id: "9a", weekly_periods: 2,
      branches: [], warnings: [],
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(result), {
      status: 201, headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const body: SynchronizedSplitCreateRequest = {
      class_section_id: "9a", weekly_periods: 2,
      branches: [
        { participant_group_name: "A", teacher_id: "t1", activity_id: "a1" },
        { participant_group_name: "B", teacher_id: "t2", activity_id: "a2" },
      ],
    };
    await createSynchronizedSplit("runtime-school", "runtime-year", body);
    expect(fetchMock).toHaveBeenCalledWith(
      "/schools/runtime-school/years/runtime-year/configuration/synchronized-splits",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
  });
});
