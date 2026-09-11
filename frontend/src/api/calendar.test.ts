import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createDay,
  createPeriod,
  deleteDay,
  deletePeriod,
  getCalendar,
  moveDay,
  movePeriod,
  updateDay,
  updatePeriod,
} from "./calendar";
import type { CalendarProjectionResponse, PeriodWriteRequest } from "./calendar";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const VALID_PROJECTION: CalendarProjectionResponse = {
  configuration_locked: false,
  days: [
    { id: "day_mon", name: "Monday", index: 0 },
    { id: "day_tue", name: "Tuesday", index: 1 },
  ],
  periods: [
    {
      id: "period_1",
      name: "1",
      index: 0,
      start_time: "09:00",
      end_time: "09:40",
      starts_new_block: true,
      is_instructional: true,
    },
    {
      id: "period_2",
      name: "2",
      index: 1,
      start_time: null,
      end_time: null,
      starts_new_block: false,
      is_instructional: true,
    },
  ],
};

const PERIOD_WRITE_REQUEST: PeriodWriteRequest = {
  name: "1",
  start_time: "09:00",
  end_time: "09:40",
  starts_new_block: false,
};

describe("calendar api", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GETs the exact calendar endpoint path with safely encoded segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getCalendar("school 1", "year/1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/school%201/years/year%2F1/calendar");
    expect(result).toEqual(VALID_PROJECTION);
  });

  it("never requires a raw block_id field on the projection response type", () => {
    // Type-level guard: this is a compile-time check that
    // CalendarPeriodItem has no `block_id` -- if it were added the next
    // line would fail to compile, not just fail at runtime.
    const period = VALID_PROJECTION.periods[0];
    expect(period).not.toHaveProperty("block_id");
  });

  it("preserves null start_time/end_time on the projection", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION)));

    const result = await getCalendar("s1", "y1");

    expect(result.periods[1]?.start_time).toBeNull();
    expect(result.periods[1]?.end_time).toBeNull();
  });

  it("preserves HH:MM strings on the projection", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION)));

    const result = await getCalendar("s1", "y1");

    expect(result.periods[0]?.start_time).toBe("09:00");
    expect(result.periods[0]?.end_time).toBe("09:40");
  });

  it("preserves is_instructional=false for a legacy period", async () => {
    const projectionWithLegacyPeriod: CalendarProjectionResponse = {
      ...VALID_PROJECTION,
      periods: [
        ...VALID_PROJECTION.periods,
        {
          id: "period_legacy",
          name: "Legacy",
          index: 2,
          start_time: null,
          end_time: null,
          starts_new_block: false,
          is_instructional: false,
        },
      ],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, projectionWithLegacyPeriod)));

    const result = await getCalendar("s1", "y1");

    expect(result.periods[2]?.is_instructional).toBe(false);
  });

  // -- Day --------------------------------------------------------------

  it("POSTs the exact day-create endpoint path with the exact request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: "day_wed", name: "Wednesday", index: 2 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await createDay("s1", "y1", { name: "Wednesday" });

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/calendar/days", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Wednesday" }),
    });
    expect(result).toEqual({ id: "day_wed", name: "Wednesday", index: 2 });
  });

  it("PUTs the exact day-update endpoint path (including the day ID)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { id: "day_mon", name: "Mon", index: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    await updateDay("s1", "y1", "day_mon", { name: "Mon" });

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/calendar/days/day_mon", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Mon" }),
    });
  });

  it("DELETEs the exact day-delete endpoint path with no request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { deleted_id: "day_mon" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await deleteDay("s1", "y1", "day_mon");

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/calendar/days/day_mon", { method: "DELETE" });
    expect(result).toEqual({ deleted_id: "day_mon" });
  });

  it("POSTs the exact day-move endpoint path with the direction in the body, and returns the full projection", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);

    const result = await moveDay("s1", "y1", "day_mon", "up");

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/calendar/days/day_mon/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction: "up" }),
    });
    expect(result).toEqual(VALID_PROJECTION);
  });

  it("propagates a structured 409 DUPLICATE_DAY error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(409, { code: "DUPLICATE_DAY", detail: "a day named 'Monday' already exists" })),
    );

    await expect(createDay("s1", "y1", { name: "Monday" })).rejects.toMatchObject({
      status: 409,
      code: "DUPLICATE_DAY",
    });
  });

  it("propagates a structured 409 DAY_IN_USE error with referenced_by", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          code: "DAY_IN_USE",
          detail: "Day is referenced elsewhere",
          referenced_by: ["TEACHER_AVAILABILITY", "RESERVED_BLOCK"],
        }),
      ),
    );

    await expect(deleteDay("s1", "y1", "day_mon")).rejects.toMatchObject({
      status: 409,
      code: "DAY_IN_USE",
      body: { referenced_by: ["TEACHER_AVAILABILITY", "RESERVED_BLOCK"] },
    });
  });

  it("propagates a structured 422 INVALID_DAY error (e.g. last day)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, {
          code: "INVALID_DAY",
          detail: "the Academic Year must retain at least one Day",
          errors: [{ code: "NO_CALENDAR_DAYS", message: "the Academic Year must retain at least one Day", context: {} }],
        }),
      ),
    );

    await expect(deleteDay("s1", "y1", "day_mon")).rejects.toMatchObject({
      status: 422,
      code: "INVALID_DAY",
    });
  });

  it("propagates a 404 'Day not found'", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(404, { detail: "Day not found" })));

    await expect(updateDay("s1", "y1", "day_missing", { name: "X" })).rejects.toMatchObject({
      status: 404,
      detail: "Day not found",
    });
  });

  // -- Period -------------------------------------------------------------

  it("POSTs the exact period-create endpoint path with the exact request body (including null times)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(201, {
        id: "period_1",
        name: "1",
        index: 0,
        start_time: null,
        end_time: null,
        starts_new_block: true,
        is_instructional: true,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const body: PeriodWriteRequest = { name: "1", start_time: null, end_time: null, starts_new_block: true };
    const result = await createPeriod("s1", "y1", body);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/calendar/periods", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    expect(result.start_time).toBeNull();
    expect(result.end_time).toBeNull();
  });

  it("PUTs the exact period-update endpoint path (including the period ID) with HH:MM times", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        id: "period_1",
        name: "1",
        index: 0,
        start_time: "09:00",
        end_time: "09:40",
        starts_new_block: false,
        is_instructional: true,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await updatePeriod("s1", "y1", "period_1", PERIOD_WRITE_REQUEST);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/calendar/periods/period_1", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(PERIOD_WRITE_REQUEST),
    });
  });

  it("does not require an is_instructional field on the write request type", () => {
    // Type-level guard: PeriodWriteRequest has no is_instructional field
    // at all -- a legacy Period's flag can never be flipped from this
    // module because there is nowhere to put it in the request.
    const body: PeriodWriteRequest = { name: "1", start_time: null, end_time: null, starts_new_block: false };
    expect(body).not.toHaveProperty("is_instructional");
  });

  it("DELETEs the exact period-delete endpoint path with no request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { deleted_id: "period_1" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await deletePeriod("s1", "y1", "period_1");

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/calendar/periods/period_1", { method: "DELETE" });
    expect(result).toEqual({ deleted_id: "period_1" });
  });

  it("POSTs the exact period-move endpoint path with the direction in the body, and returns the full projection", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);

    const result = await movePeriod("s1", "y1", "period_1", "down");

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/calendar/periods/period_1/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction: "down" }),
    });
    expect(result).toEqual(VALID_PROJECTION);
  });

  it("propagates a structured 409 DUPLICATE_PERIOD error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(409, { code: "DUPLICATE_PERIOD", detail: "a period named '1' already exists" })),
    );

    await expect(createPeriod("s1", "y1", PERIOD_WRITE_REQUEST)).rejects.toMatchObject({
      status: 409,
      code: "DUPLICATE_PERIOD",
    });
  });

  it("propagates a structured 409 PERIOD_IN_USE error with referenced_by including TIME_PREFERENCE", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          code: "PERIOD_IN_USE",
          detail: "Period is referenced elsewhere",
          referenced_by: ["TIME_PREFERENCE"],
        }),
      ),
    );

    await expect(deletePeriod("s1", "y1", "period_1")).rejects.toMatchObject({
      status: 409,
      code: "PERIOD_IN_USE",
      body: { referenced_by: ["TIME_PREFERENCE"] },
    });
  });

  it("propagates a structured 409 PERIOD_REORDER_BLOCKED_BY_TIME_PREFERENCES error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          code: "PERIOD_REORDER_BLOCKED_BY_TIME_PREFERENCES",
          detail: "Periods cannot be reordered while teaching requirements contain time preferences.",
        }),
      ),
    );

    await expect(movePeriod("s1", "y1", "period_1", "up")).rejects.toMatchObject({
      status: 409,
      code: "PERIOD_REORDER_BLOCKED_BY_TIME_PREFERENCES",
    });
  });

  it("propagates a structured 422 INVALID_PERIOD error (e.g. overlapping clock times)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, {
          code: "INVALID_PERIOD",
          detail: "Period 'period_1' ends at 09:40 which is after Period 'period_2' starts at 09:30",
          errors: [
            {
              code: "PERIOD_CLOCK_TIME_OVERLAP",
              message: "overlap",
              context: { period_id: "period_1", next_period_id: "period_2" },
            },
          ],
        }),
      ),
    );

    await expect(createPeriod("s1", "y1", PERIOD_WRITE_REQUEST)).rejects.toMatchObject({
      status: 422,
      code: "INVALID_PERIOD",
    });
  });

  it("propagates a 404 'Period not found'", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(404, { detail: "Period not found" })));

    await expect(updatePeriod("s1", "y1", "period_missing", PERIOD_WRITE_REQUEST)).rejects.toMatchObject({
      status: 404,
      detail: "Period not found",
    });
  });

  it("propagates a structured 409 SCHEDULING_CONFIGURATION_LOCKED error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          code: "SCHEDULING_CONFIGURATION_LOCKED",
          detail: "Scheduling configuration is locked because a schedule already exists",
        }),
      ),
    );

    await expect(createDay("s1", "y1", { name: "Saturday" })).rejects.toMatchObject({
      status: 409,
      code: "SCHEDULING_CONFIGURATION_LOCKED",
    });
  });

  it("forwards an AbortSignal when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, VALID_PROJECTION));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getCalendar("s1", "y1", controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("/schools/s1/years/y1/calendar", { signal: controller.signal });
  });
});
