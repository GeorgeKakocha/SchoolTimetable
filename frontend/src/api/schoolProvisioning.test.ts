import { afterEach, expect, it, vi } from "vitest";
import { provisionSchool, type ProvisionSchoolResponse } from "./schoolProvisioning";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("POSTs the exact accepted provisioning payload to /schools", async () => {
  const response: ProvisionSchoolResponse = {
    school: { id: "trial-school", name: "Trial School" },
    academic_year: { id: "ay-2026-2027", label: "2026/2027" },
    configuration_state: {
      published_revision_number: null,
      draft_revision_number: 1,
      configuration_locked: false,
      timetable_out_of_date: false,
    },
  };
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(response), {
    status: 201,
    headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("fetch", fetchMock);

  const request = {
    school_id: "trial-school",
    school_name: "Trial School",
    initial_academic_year: { academic_year_id: "ay-2026-2027", label: "2026/2027" },
  };
  await expect(provisionSchool(request)).resolves.toEqual(response);
  expect(fetchMock).toHaveBeenCalledWith("/schools", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
});
