import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import {
  isConfigurationChangedDuringGenerationError,
  isIncompatibleLocksRequireConfirmationError,
  isInvalidConfigurationError,
  isNoConfigurationDraftError,
  isScheduleInfeasibleError,
  isStaleScheduleVersionError,
  regenerateActiveSchedule,
} from "./scheduleRegeneration";
import type { ActiveScheduleResponse, RegenerateScheduleRequest } from "./types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const ACTIVE: ActiveScheduleResponse = {
  version_number: 2,
  solver_status: "OPTIMAL",
  total_soft_penalty: 0,
  created_at: "2026-01-02T00:00:00Z",
  is_active: true,
  entries: [],
  locked_occurrences: [],
};

describe("schedule regeneration API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts the exact empty-confirmation request and returns ActiveScheduleResponse", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, ACTIVE));
    vi.stubGlobal("fetch", fetchMock);
    const request: RegenerateScheduleRequest = {
      base_version_number: 1,
      confirmed_incompatible_lock_keys: [],
    };

    await expect(regenerateActiveSchedule("school 1", "year/1", request)).resolves.toEqual(ACTIVE);
    expect(fetchMock).toHaveBeenCalledWith(
      "/schools/school%201/years/year%2F1/schedule/active/regenerate",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      },
    );
  });

  it("serializes the exact natural-ID confirmation key set without a boolean shortcut", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, ACTIVE));
    vi.stubGlobal("fetch", fetchMock);
    const request: RegenerateScheduleRequest = {
      base_version_number: 1,
      confirmed_incompatible_lock_keys: [
        { requirement_id: "req/1", day_id: "day 2", anchor_period_id: "p3" },
        { requirement_id: "req/2", day_id: "day 1", anchor_period_id: "p4" },
      ],
    };

    await regenerateActiveSchedule("s1", "y1", request);

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const serialized = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(serialized).toEqual(request);
    expect(serialized).not.toHaveProperty("force");
    expect(serialized).not.toHaveProperty("confirm");
  });

  it("preserves incompatible-lock data and distinguishes all supported regeneration errors", () => {
    const incompatible = new ApiError(409, "Locks need review", "INCOMPATIBLE_LOCKS_REQUIRE_CONFIRMATION", {
      code: "INCOMPATIBLE_LOCKS_REQUIRE_CONFIRMATION",
      detail: "Locks need review",
      incompatible_locks: [
        {
          requirement_id: "req1",
          day_id: "mon",
          anchor_period_id: "p1",
          reason_code: "FIXED_PLACEMENT_CONFLICT",
          message: "The locked lesson conflicts with a fixed placement.",
        },
      ],
    });
    expect(isIncompatibleLocksRequireConfirmationError(incompatible)).toBe(true);
    if (isIncompatibleLocksRequireConfirmationError(incompatible)) {
      expect(incompatible.body.incompatible_locks[0]).toEqual({
        requirement_id: "req1",
        day_id: "mon",
        anchor_period_id: "p1",
        reason_code: "FIXED_PLACEMENT_CONFLICT",
        message: "The locked lesson conflicts with a fixed placement.",
      });
    }

    expect(isStaleScheduleVersionError(new ApiError(409, "stale", "STALE_SCHEDULE_VERSION"))).toBe(true);
    expect(isNoConfigurationDraftError(new ApiError(409, "draft", "NO_CONFIGURATION_DRAFT"))).toBe(true);
    expect(isConfigurationChangedDuringGenerationError(
      new ApiError(409, "changed", "CONFIGURATION_CHANGED_DURING_GENERATION"),
    )).toBe(true);
    expect(isScheduleInfeasibleError(new ApiError(409, "infeasible", "SCHEDULE_INFEASIBLE"))).toBe(true);
    expect(isInvalidConfigurationError(new ApiError(422, "invalid", "INVALID_CONFIGURATION", { errors: [] }))).toBe(true);
  });
});
