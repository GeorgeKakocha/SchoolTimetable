import { useCallback, useEffect, useState } from "react";
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
} from "../../api/calendar";
import type { CalendarDayItem, CalendarPeriodItem, CalendarProjectionResponse, MoveDirection } from "../../api/calendar";
import { ApiError } from "../../api/client";
import { AppConfigError, loadAppConfig } from "../../config/appConfig";

/**
 * Calendar B: the "Calendar & Bell Schedule" tab of `SchoolSetupPage`.
 * Self-contained, matching `RoomsResourcesPanel`'s architecture --
 * fetches its own `CalendarProjectionResponse` on mount and after every
 * mutation, and never renders a raw `id`, `index`, or `block_id`.
 *
 * Two visually distinct sections share one projection: "Working Days"
 * and "Bell Schedule". Reorder is Up/Down only (no drag-and-drop, no
 * numeric index editing); every successful write (create/update/
 * delete/move) is followed by a fresh authoritative GET rather than an
 * optimistic local reorder, even though `move` itself already returns
 * the freshly-recomputed projection -- that response is deliberately
 * not trusted here, matching every other write in this panel.
 *
 * `starts_new_block` is the only block-boundary field ever read or
 * written -- raw `block_id` is never part of `CalendarPeriodItem` at
 * all. `is_instructional` is read-only here: `PeriodWriteRequest` has
 * no such field, so a legacy `is_instructional=false` Period's flag is
 * preserved by the backend regardless of what this panel sends.
 */

type PanelState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; projection: CalendarProjectionResponse };

type AppConfigResult =
  | { ok: true; schoolId: string; academicYearId: string }
  | { ok: false; message: string };

type RefreshState = { status: "idle" } | { status: "refreshing" } | { status: "stale"; message: string };

type WriteOutcome = { ok: true } | { ok: false; lockRace: boolean; notFound: boolean };

const REFRESH_FAILURE_MESSAGE =
  "The change was saved, but the latest calendar could not be refreshed. Retry to load the current data.";
const LOCK_RACE_MESSAGE = "A schedule was generated since this page loaded. Calendar & Bell Schedule is now read-only.";

function isBlank(value: string): boolean {
  return value.trim() === "";
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  return "Something went wrong. Please try again.";
}

const DAY_REFERENCED_BY_LABELS: Record<string, string> = {
  TEACHER_AVAILABILITY: "Teacher Availability",
  RESERVED_BLOCK: "Reserved Activities",
  FIXED_PLACEMENT: "a fixed placement",
};

const PERIOD_REFERENCED_BY_LABELS: Record<string, string> = {
  TEACHER_AVAILABILITY: "Teacher Availability",
  RESERVED_BLOCK: "Reserved Activities",
  FIXED_PLACEMENT: "a fixed placement",
  TIME_PREFERENCE: "existing time preferences in Teaching Assignments",
};

function friendlyReferencedByList(raw: unknown, labels: Record<string, string>): string | null {
  if (!Array.isArray(raw) || raw.length === 0) {
    return null;
  }
  return raw
    .filter(isString)
    .map((code) => labels[code] ?? code)
    .join(", ");
}

const DAY_VALIDATION_MESSAGES: Record<string, string> = {
  BLANK_DAY_NAME: "Day name cannot be blank.",
  NO_CALENDAR_DAYS: "At least one working day is required.",
  DAY_ALREADY_AT_TOP: "This day is already first.",
  DAY_ALREADY_AT_BOTTOM: "This day is already last.",
};

const PERIOD_VALIDATION_MESSAGES: Record<string, string> = {
  BLANK_PERIOD_NAME: "Period name cannot be blank.",
  PERIOD_TIME_PAIR_INCOMPLETE: "Enter both a start and end time, or leave both empty.",
  PERIOD_TIME_ORDER_INVALID: "Start time must be before end time.",
  PERIOD_CLOCK_TIME_OVERLAP: "This period's time overlaps with a neighboring period. Adjust the times so they don't overlap.",
  NO_INSTRUCTIONAL_PERIODS: "At least one lesson period is required.",
  PERIOD_ALREADY_AT_TOP: "This period is already first.",
  PERIOD_ALREADY_AT_BOTTOM: "This period is already last.",
};

function firstValidationMessage(rawErrors: unknown, messages: Record<string, string>, fallback: string): string {
  if (!Array.isArray(rawErrors) || rawErrors.length === 0) {
    return fallback;
  }
  for (const item of rawErrors) {
    if (typeof item === "object" && item !== null && isString((item as { code?: unknown }).code)) {
      const code = (item as { code: string }).code;
      const known = messages[code];
      if (known !== undefined) {
        return known;
      }
    }
  }
  return fallback;
}

function describeDayMutationError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Something went wrong. Please try again.";
  }
  switch (error.code) {
    case "DUPLICATE_DAY":
      return "A day with this name already exists.";
    case "DAY_IN_USE": {
      const labels = friendlyReferencedByList(error.body?.["referenced_by"], DAY_REFERENCED_BY_LABELS);
      return labels !== null ? `This day can't be deleted because it is used by: ${labels}.` : error.detail;
    }
    case "INVALID_DAY":
      return firstValidationMessage(error.body?.["errors"], DAY_VALIDATION_MESSAGES, error.detail);
    default:
      return error.detail;
  }
}

function describePeriodMutationError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Something went wrong. Please try again.";
  }
  switch (error.code) {
    case "DUPLICATE_PERIOD":
      return "A period with this name already exists.";
    case "PERIOD_IN_USE": {
      const labels = friendlyReferencedByList(error.body?.["referenced_by"], PERIOD_REFERENCED_BY_LABELS);
      return labels !== null ? `This period can't be changed because it is used by: ${labels}.` : error.detail;
    }
    case "PERIOD_REORDER_BLOCKED_BY_TIME_PREFERENCES":
      return "Periods cannot be reordered while teaching assignments contain time preferences.";
    case "INVALID_PERIOD":
      return firstValidationMessage(error.body?.["errors"], PERIOD_VALIDATION_MESSAGES, error.detail);
    default:
      return error.detail;
  }
}

function isValidTimePair(start: string, end: string): boolean {
  if (start === "" && end === "") {
    return true;
  }
  if (start === "" || end === "") {
    return false;
  }
  return start < end;
}

type DayEditState = { dayId: string; name: string };

type PeriodEditState = {
  periodId: string;
  name: string;
  startTime: string;
  endTime: string;
  startsNewBlock: boolean;
};

function CalendarBellSchedulePanel() {
  const [appConfigResult] = useState<AppConfigResult>(() => {
    try {
      const config = loadAppConfig();
      return { ok: true, schoolId: config.schoolId, academicYearId: config.academicYearId };
    } catch (error) {
      return {
        ok: false,
        message: error instanceof AppConfigError ? error.message : "Frontend configuration is invalid.",
      };
    }
  });

  const [panelState, setPanelState] = useState<PanelState>({ status: "loading" });
  const [retryToken, setRetryToken] = useState(0);
  const [refreshState, setRefreshState] = useState<RefreshState>({ status: "idle" });
  const [staleLockNotice, setStaleLockNotice] = useState<string | null>(null);

  // Day create/edit/delete/move state
  const [createDayOpen, setCreateDayOpen] = useState(false);
  const [createDayName, setCreateDayName] = useState("");
  const [createDaySubmitting, setCreateDaySubmitting] = useState(false);
  const [createDayError, setCreateDayError] = useState<string | null>(null);

  const [editDayState, setEditDayState] = useState<DayEditState | null>(null);
  const [editDaySubmitting, setEditDaySubmitting] = useState(false);
  const [editDayError, setEditDayError] = useState<string | null>(null);

  const [deleteDayConfirmId, setDeleteDayConfirmId] = useState<string | null>(null);
  const [deleteDaySubmitting, setDeleteDaySubmitting] = useState(false);
  const [deleteDayError, setDeleteDayError] = useState<string | null>(null);

  const [movingDayId, setMovingDayId] = useState<string | null>(null);
  const [dayMoveError, setDayMoveError] = useState<string | null>(null);

  // Period create/edit/delete/move state
  const [createPeriodOpen, setCreatePeriodOpen] = useState(false);
  const [createPeriodName, setCreatePeriodName] = useState("");
  const [createPeriodStart, setCreatePeriodStart] = useState("");
  const [createPeriodEnd, setCreatePeriodEnd] = useState("");
  const [createPeriodStartsNewBlock, setCreatePeriodStartsNewBlock] = useState(false);
  const [createPeriodSubmitting, setCreatePeriodSubmitting] = useState(false);
  const [createPeriodError, setCreatePeriodError] = useState<string | null>(null);

  const [editPeriodState, setEditPeriodState] = useState<PeriodEditState | null>(null);
  const [editPeriodSubmitting, setEditPeriodSubmitting] = useState(false);
  const [editPeriodError, setEditPeriodError] = useState<string | null>(null);

  const [deletePeriodConfirmId, setDeletePeriodConfirmId] = useState<string | null>(null);
  const [deletePeriodSubmitting, setDeletePeriodSubmitting] = useState(false);
  const [deletePeriodError, setDeletePeriodError] = useState<string | null>(null);

  const [movingPeriodId, setMovingPeriodId] = useState<string | null>(null);
  const [periodMoveError, setPeriodMoveError] = useState<string | null>(null);

  useEffect(() => {
    if (!appConfigResult.ok) {
      return;
    }
    const controller = new AbortController();
    setPanelState({ status: "loading" });

    getCalendar(appConfigResult.schoolId, appConfigResult.academicYearId, controller.signal)
      .then((projection) => {
        if (controller.signal.aborted) {
          return;
        }
        setPanelState({ status: "ready", projection });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setPanelState({ status: "error", message: describeApiError(error) });
      });

    return () => {
      controller.abort();
    };
  }, [appConfigResult, retryToken]);

  const handleRetry = useCallback(() => {
    setRetryToken((token) => token + 1);
  }, []);

  const refreshProjection = useCallback(
    async (staleFailureMessage: string) => {
      if (!appConfigResult.ok) {
        return;
      }
      setRefreshState({ status: "refreshing" });
      try {
        const projection = await getCalendar(appConfigResult.schoolId, appConfigResult.academicYearId);
        setPanelState({ status: "ready", projection });
        setRefreshState({ status: "idle" });
      } catch {
        setRefreshState({ status: "stale", message: staleFailureMessage });
      }
    },
    [appConfigResult],
  );

  const performWrite = useCallback(
    async (action: () => Promise<unknown>, onInlineError: (error: unknown) => void): Promise<WriteOutcome> => {
      try {
        await action();
        setStaleLockNotice(null);
        return { ok: true };
      } catch (error) {
        if (error instanceof ApiError && error.code === "SCHEDULING_CONFIGURATION_LOCKED") {
          setStaleLockNotice(LOCK_RACE_MESSAGE);
          return { ok: false, lockRace: true, notFound: false };
        }
        if (error instanceof ApiError && error.status === 404) {
          setPanelState({ status: "error", message: error.detail });
          return { ok: false, lockRace: false, notFound: true };
        }
        onInlineError(error);
        return { ok: false, lockRace: false, notFound: false };
      }
    },
    [],
  );

  const anyMutationInFlight =
    createDaySubmitting ||
    editDaySubmitting ||
    deleteDaySubmitting ||
    movingDayId !== null ||
    createPeriodSubmitting ||
    editPeriodSubmitting ||
    deletePeriodSubmitting ||
    movingPeriodId !== null;

  const anyFormOpen =
    createDayOpen ||
    editDayState !== null ||
    deleteDayConfirmId !== null ||
    createPeriodOpen ||
    editPeriodState !== null ||
    deletePeriodConfirmId !== null;

  const controlsDisabled = refreshState.status !== "idle" || anyMutationInFlight;
  const rowActionsDisabled = controlsDisabled || anyFormOpen;

  // -- Day handlers --------------------------------------------------------

  const handleOpenCreateDay = useCallback(() => {
    setCreateDayOpen(true);
    setCreateDayName("");
    setCreateDayError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelCreateDay = useCallback(() => {
    setCreateDayOpen(false);
    setCreateDayName("");
    setCreateDayError(null);
  }, []);

  const createDayValid = !isBlank(createDayName);

  const handleSubmitCreateDay = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!appConfigResult.ok || createDaySubmitting || !createDayValid) {
        return;
      }
      setCreateDaySubmitting(true);
      setCreateDayError(null);
      const outcome = await performWrite(
        () => createDay(appConfigResult.schoolId, appConfigResult.academicYearId, { name: createDayName.trim() }),
        (error) => setCreateDayError(describeDayMutationError(error)),
      );
      setCreateDaySubmitting(false);

      if (outcome.ok || outcome.lockRace) {
        setCreateDayOpen(false);
        setCreateDayName("");
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [appConfigResult, createDaySubmitting, createDayValid, createDayName, performWrite, refreshProjection],
  );

  const handleStartEditDay = useCallback((day: CalendarDayItem) => {
    setEditDayState({ dayId: day.id, name: day.name });
    setEditDayError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelEditDay = useCallback(() => {
    setEditDayState(null);
    setEditDayError(null);
  }, []);

  const editDayValid = editDayState !== null && !isBlank(editDayState.name);

  const handleSubmitEditDay = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!appConfigResult.ok || editDayState === null || editDaySubmitting || !editDayValid) {
        return;
      }
      setEditDaySubmitting(true);
      setEditDayError(null);
      const outcome = await performWrite(
        () =>
          updateDay(appConfigResult.schoolId, appConfigResult.academicYearId, editDayState.dayId, {
            name: editDayState.name.trim(),
          }),
        (error) => setEditDayError(describeDayMutationError(error)),
      );
      setEditDaySubmitting(false);

      if (outcome.ok || outcome.lockRace || outcome.notFound) {
        setEditDayState(null);
      }
      if (outcome.ok || outcome.lockRace) {
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [appConfigResult, editDayState, editDaySubmitting, editDayValid, performWrite, refreshProjection],
  );

  const handleDeleteDayClick = useCallback((dayId: string) => {
    setDeleteDayConfirmId(dayId);
    setDeleteDayError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelDeleteDay = useCallback(() => {
    setDeleteDayConfirmId(null);
    setDeleteDayError(null);
  }, []);

  const handleConfirmDeleteDay = useCallback(
    async (dayId: string) => {
      if (!appConfigResult.ok) {
        return;
      }
      setDeleteDaySubmitting(true);
      setDeleteDayError(null);
      const outcome = await performWrite(
        () => deleteDay(appConfigResult.schoolId, appConfigResult.academicYearId, dayId),
        (error) => setDeleteDayError(describeDayMutationError(error)),
      );
      setDeleteDaySubmitting(false);

      if (outcome.ok || outcome.lockRace || outcome.notFound) {
        setDeleteDayConfirmId(null);
      }
      if (outcome.ok || outcome.lockRace) {
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [appConfigResult, performWrite, refreshProjection],
  );

  const handleMoveDay = useCallback(
    async (dayId: string, direction: MoveDirection) => {
      if (!appConfigResult.ok) {
        return;
      }
      setMovingDayId(dayId);
      setDayMoveError(null);
      const outcome = await performWrite(
        () => moveDay(appConfigResult.schoolId, appConfigResult.academicYearId, dayId, direction),
        (error) => setDayMoveError(describeDayMutationError(error)),
      );
      setMovingDayId(null);
      if (outcome.ok || outcome.lockRace) {
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [appConfigResult, performWrite, refreshProjection],
  );

  // -- Period handlers ------------------------------------------------------

  const handleOpenCreatePeriod = useCallback(() => {
    setCreatePeriodOpen(true);
    setCreatePeriodName("");
    setCreatePeriodStart("");
    setCreatePeriodEnd("");
    setCreatePeriodStartsNewBlock(false);
    setCreatePeriodError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelCreatePeriod = useCallback(() => {
    setCreatePeriodOpen(false);
    setCreatePeriodName("");
    setCreatePeriodStart("");
    setCreatePeriodEnd("");
    setCreatePeriodStartsNewBlock(false);
    setCreatePeriodError(null);
  }, []);

  const createPeriodValid = !isBlank(createPeriodName) && isValidTimePair(createPeriodStart, createPeriodEnd);

  const handleSubmitCreatePeriod = useCallback(
    async (event: React.FormEvent<HTMLFormElement>, willBeFirstPeriod: boolean) => {
      event.preventDefault();
      if (!appConfigResult.ok || createPeriodSubmitting || !createPeriodValid) {
        return;
      }
      setCreatePeriodSubmitting(true);
      setCreatePeriodError(null);
      const outcome = await performWrite(
        () =>
          createPeriod(appConfigResult.schoolId, appConfigResult.academicYearId, {
            name: createPeriodName.trim(),
            start_time: createPeriodStart === "" ? null : createPeriodStart,
            end_time: createPeriodEnd === "" ? null : createPeriodEnd,
            starts_new_block: willBeFirstPeriod ? true : createPeriodStartsNewBlock,
          }),
        (error) => setCreatePeriodError(describePeriodMutationError(error)),
      );
      setCreatePeriodSubmitting(false);

      if (outcome.ok || outcome.lockRace) {
        setCreatePeriodOpen(false);
        setCreatePeriodName("");
        setCreatePeriodStart("");
        setCreatePeriodEnd("");
        setCreatePeriodStartsNewBlock(false);
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [
      appConfigResult,
      createPeriodSubmitting,
      createPeriodValid,
      createPeriodName,
      createPeriodStart,
      createPeriodEnd,
      createPeriodStartsNewBlock,
      performWrite,
      refreshProjection,
    ],
  );

  const handleStartEditPeriod = useCallback((period: CalendarPeriodItem) => {
    setEditPeriodState({
      periodId: period.id,
      name: period.name,
      startTime: period.start_time ?? "",
      endTime: period.end_time ?? "",
      startsNewBlock: period.starts_new_block,
    });
    setEditPeriodError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelEditPeriod = useCallback(() => {
    setEditPeriodState(null);
    setEditPeriodError(null);
  }, []);

  const editPeriodValid =
    editPeriodState !== null && !isBlank(editPeriodState.name) && isValidTimePair(editPeriodState.startTime, editPeriodState.endTime);

  const handleSubmitEditPeriod = useCallback(
    async (event: React.FormEvent<HTMLFormElement>, isFirstPeriod: boolean) => {
      event.preventDefault();
      if (!appConfigResult.ok || editPeriodState === null || editPeriodSubmitting || !editPeriodValid) {
        return;
      }
      setEditPeriodSubmitting(true);
      setEditPeriodError(null);
      const outcome = await performWrite(
        () =>
          updatePeriod(appConfigResult.schoolId, appConfigResult.academicYearId, editPeriodState.periodId, {
            name: editPeriodState.name.trim(),
            start_time: editPeriodState.startTime === "" ? null : editPeriodState.startTime,
            end_time: editPeriodState.endTime === "" ? null : editPeriodState.endTime,
            starts_new_block: isFirstPeriod ? true : editPeriodState.startsNewBlock,
          }),
        (error) => setEditPeriodError(describePeriodMutationError(error)),
      );
      setEditPeriodSubmitting(false);

      if (outcome.ok || outcome.lockRace || outcome.notFound) {
        setEditPeriodState(null);
      }
      if (outcome.ok || outcome.lockRace) {
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [appConfigResult, editPeriodState, editPeriodSubmitting, editPeriodValid, performWrite, refreshProjection],
  );

  const handleDeletePeriodClick = useCallback((periodId: string) => {
    setDeletePeriodConfirmId(periodId);
    setDeletePeriodError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelDeletePeriod = useCallback(() => {
    setDeletePeriodConfirmId(null);
    setDeletePeriodError(null);
  }, []);

  const handleConfirmDeletePeriod = useCallback(
    async (periodId: string) => {
      if (!appConfigResult.ok) {
        return;
      }
      setDeletePeriodSubmitting(true);
      setDeletePeriodError(null);
      const outcome = await performWrite(
        () => deletePeriod(appConfigResult.schoolId, appConfigResult.academicYearId, periodId),
        (error) => setDeletePeriodError(describePeriodMutationError(error)),
      );
      setDeletePeriodSubmitting(false);

      if (outcome.ok || outcome.lockRace || outcome.notFound) {
        setDeletePeriodConfirmId(null);
      }
      if (outcome.ok || outcome.lockRace) {
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [appConfigResult, performWrite, refreshProjection],
  );

  const handleMovePeriod = useCallback(
    async (periodId: string, direction: MoveDirection) => {
      if (!appConfigResult.ok) {
        return;
      }
      setMovingPeriodId(periodId);
      setPeriodMoveError(null);
      const outcome = await performWrite(
        () => movePeriod(appConfigResult.schoolId, appConfigResult.academicYearId, periodId, direction),
        (error) => setPeriodMoveError(describePeriodMutationError(error)),
      );
      setMovingPeriodId(null);
      if (outcome.ok || outcome.lockRace) {
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [appConfigResult, performWrite, refreshProjection],
  );

  if (!appConfigResult.ok) {
    return <p role="alert">{appConfigResult.message}</p>;
  }

  if (panelState.status === "loading") {
    return <p>Loading calendar…</p>;
  }

  if (panelState.status === "error") {
    return (
      <div role="alert" className="panel-error">
        <p>{panelState.message}</p>
        <button type="button" onClick={handleRetry}>
          Retry
        </button>
      </div>
    );
  }

  const { projection } = panelState;
  const locked = projection.configuration_locked;
  const days = projection.days;
  const periods = projection.periods;

  return (
    <div className="setup-panel calendar-panel">
      <p className="page-subtitle">
        Manage the working days in the school week and the bell schedule of periods used to build the timetable.
      </p>

      {locked && (
        <div className="lock-banner" id="calendar-lock-banner-text">
          Scheduling configuration is locked because a schedule already exists.
        </div>
      )}

      {staleLockNotice !== null && (
        <div className="lock-race-banner" role="alert">
          {staleLockNotice}
        </div>
      )}

      {refreshState.status === "stale" && (
        <div className="stale-banner" role="alert">
          <p>{refreshState.message}</p>
          <button type="button" onClick={() => refreshProjection(refreshState.message)}>
            Retry
          </button>
        </div>
      )}

      {refreshState.status === "refreshing" && (
        <p className="refreshing-note" role="status">
          Refreshing…
        </p>
      )}

      {/* ==================== Working Days ==================== */}
      <section className="calendar-section" aria-labelledby="calendar-working-days-heading">
        <h2 id="calendar-working-days-heading" className="calendar-section-heading">
          Working Days
        </h2>
        <p className="calendar-section-intro">The days of the week this school holds lessons on, in order.</p>

        <div className="setup-toolbar">
          <button
            type="button"
            className="btn-primary"
            onClick={handleOpenCreateDay}
            disabled={locked || rowActionsDisabled}
          >
            + Add day
          </button>
        </div>

        {dayMoveError !== null && (
          <p role="alert" className="row-edit-error">
            {dayMoveError}
          </p>
        )}

        {createDayOpen && (
          <form className="create-panel" onSubmit={handleSubmitCreateDay}>
            <h3 className="create-panel-heading">Add day</h3>
            <label>
              Name
              <input
                type="text"
                value={createDayName}
                onChange={(event) => setCreateDayName(event.target.value)}
                disabled={createDaySubmitting}
                autoFocus
                required
              />
            </label>
            {createDayError !== null && (
              <p role="alert" className="create-panel-error">
                {createDayError}
              </p>
            )}
            <div className="create-panel-actions">
              <button type="button" onClick={handleCancelCreateDay} disabled={createDaySubmitting}>
                Cancel
              </button>
              <button type="submit" className="btn-primary" disabled={createDaySubmitting || !createDayValid}>
                {createDaySubmitting ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        )}

        {days.length === 0 ? (
          <div className="setup-empty-state">
            <p>No working days yet.</p>
            <p>Add the days of the week this school holds lessons on.</p>
          </div>
        ) : (
          <div className="table-scroll">
            <table className="setup-table">
              <colgroup>
                <col className="col-setup-name" />
                <col className="col-setup-actions" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">Day</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {days.map((day, position) => {
                  const isEditing = editDayState !== null && editDayState.dayId === day.id;
                  const isConfirmingDelete = deleteDayConfirmId === day.id;
                  const isFirst = position === 0;
                  const isLast = position === days.length - 1;
                  const isMoving = movingDayId === day.id;

                  return (
                    <tr key={day.id}>
                      {isEditing && editDayState !== null ? (
                        <td colSpan={2}>
                          <form className="row-edit-form" onSubmit={handleSubmitEditDay}>
                            <label>
                              Name
                              <input
                                type="text"
                                value={editDayState.name}
                                onChange={(event) =>
                                  setEditDayState((current) =>
                                    current === null ? current : { ...current, name: event.target.value },
                                  )
                                }
                                disabled={editDaySubmitting}
                                autoFocus
                                required
                              />
                            </label>
                            {editDayError !== null && (
                              <p role="alert" className="row-edit-error">
                                {editDayError}
                              </p>
                            )}
                            <span className="row-edit-actions">
                              <button type="submit" className="btn-primary" disabled={editDaySubmitting || !editDayValid}>
                                {editDaySubmitting ? "Saving…" : "Save"}
                              </button>
                              <button type="button" onClick={handleCancelEditDay} disabled={editDaySubmitting}>
                                Cancel
                              </button>
                            </span>
                          </form>
                        </td>
                      ) : (
                        <>
                          <th scope="row" data-label="Day">
                            {day.name}
                          </th>
                          <td data-label="Actions">
                            {isConfirmingDelete ? (
                              <span className="delete-confirm">
                                <span className="delete-confirm-text">Delete {day.name}?</span>
                                {deleteDayError !== null && (
                                  <p role="alert" className="delete-confirm-error">
                                    {deleteDayError}
                                  </p>
                                )}
                                <span className="delete-confirm-actions">
                                  <button
                                    type="button"
                                    onClick={() => handleConfirmDeleteDay(day.id)}
                                    disabled={deleteDaySubmitting}
                                  >
                                    {deleteDaySubmitting ? "Deleting…" : "Confirm delete"}
                                  </button>
                                  <button type="button" onClick={handleCancelDeleteDay} disabled={deleteDaySubmitting}>
                                    Cancel
                                  </button>
                                </span>
                              </span>
                            ) : (
                              <span className="actions-group">
                                <button
                                  type="button"
                                  className="action-button move-button"
                                  aria-label={`Move ${day.name} up`}
                                  onClick={() => handleMoveDay(day.id, "up")}
                                  disabled={locked || rowActionsDisabled || isFirst}
                                >
                                  {isMoving ? "…" : "↑"}
                                </button>
                                <button
                                  type="button"
                                  className="action-button move-button"
                                  aria-label={`Move ${day.name} down`}
                                  onClick={() => handleMoveDay(day.id, "down")}
                                  disabled={locked || rowActionsDisabled || isLast}
                                >
                                  {isMoving ? "…" : "↓"}
                                </button>
                                <button
                                  type="button"
                                  className="action-button"
                                  onClick={() => handleStartEditDay(day)}
                                  disabled={locked || rowActionsDisabled}
                                  aria-describedby={locked ? "calendar-lock-banner-text" : undefined}
                                >
                                  Edit
                                </button>
                                <button
                                  type="button"
                                  className="action-button action-button-delete"
                                  onClick={() => handleDeleteDayClick(day.id)}
                                  disabled={locked || rowActionsDisabled}
                                  aria-describedby={locked ? "calendar-lock-banner-text" : undefined}
                                >
                                  Delete
                                </button>
                              </span>
                            )}
                          </td>
                        </>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ==================== Bell Schedule ==================== */}
      <section className="calendar-section" aria-labelledby="calendar-bell-schedule-heading">
        <h2 id="calendar-bell-schedule-heading" className="calendar-section-heading">
          Bell Schedule
        </h2>
        <p className="calendar-section-intro">
          The periods lessons can be scheduled into, in order, with optional bell times shown for reference.
        </p>

        <div className="setup-toolbar">
          <button
            type="button"
            className="btn-primary"
            onClick={handleOpenCreatePeriod}
            disabled={locked || rowActionsDisabled}
          >
            + Add period
          </button>
        </div>

        {periodMoveError !== null && (
          <p role="alert" className="row-edit-error">
            {periodMoveError}
          </p>
        )}

        {createPeriodOpen && (
          <form
            className="create-panel"
            onSubmit={(event) => handleSubmitCreatePeriod(event, periods.length === 0)}
          >
            <h3 className="create-panel-heading">Add period</h3>
            <label>
              Name
              <input
                type="text"
                value={createPeriodName}
                onChange={(event) => setCreatePeriodName(event.target.value)}
                disabled={createPeriodSubmitting}
                autoFocus
                required
              />
            </label>
            <span className="time-inputs">
              <label>
                Start time
                <input
                  type="time"
                  value={createPeriodStart}
                  onChange={(event) => setCreatePeriodStart(event.target.value)}
                  disabled={createPeriodSubmitting}
                />
              </label>
              <label>
                End time
                <input
                  type="time"
                  value={createPeriodEnd}
                  onChange={(event) => setCreatePeriodEnd(event.target.value)}
                  disabled={createPeriodSubmitting}
                />
              </label>
            </span>
            {!isValidTimePair(createPeriodStart, createPeriodEnd) && (
              <p role="alert" className="row-edit-error">
                Enter both a start and end time, with start before end, or leave both empty.
              </p>
            )}
            {periods.length === 0 ? (
              <p className="hint-callout">This will be the first period of the day, so it always starts a new block.</p>
            ) : (
              <>
                <label className="calendar-checkbox-label">
                  <input
                    type="checkbox"
                    checked={createPeriodStartsNewBlock}
                    onChange={(event) => setCreatePeriodStartsNewBlock(event.target.checked)}
                    disabled={createPeriodSubmitting}
                  />
                  Starts a new block after a break
                </label>
                <p className="hint-callout">
                  Use this when a longer break or lunch separates this period from the previous one. Consecutive
                  multi-period lessons will not cross this boundary.
                </p>
              </>
            )}
            {createPeriodError !== null && (
              <p role="alert" className="create-panel-error">
                {createPeriodError}
              </p>
            )}
            <div className="create-panel-actions">
              <button type="button" onClick={handleCancelCreatePeriod} disabled={createPeriodSubmitting}>
                Cancel
              </button>
              <button
                type="submit"
                className="btn-primary"
                disabled={createPeriodSubmitting || !createPeriodValid}
              >
                {createPeriodSubmitting ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        )}

        {periods.length === 0 ? (
          <div className="setup-empty-state">
            <p>No periods yet.</p>
            <p>Add the periods lessons can be scheduled into.</p>
          </div>
        ) : (
          <div className="table-scroll">
            <table className="setup-table">
              <colgroup>
                <col className="col-setup-name" />
                <col className="col-setup-time" />
                <col className="col-setup-time" />
                <col className="col-setup-block" />
                <col className="col-setup-actions" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Start</th>
                  <th scope="col">End</th>
                  <th scope="col">Block</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {periods.map((period, position) => {
                  const isEditing = editPeriodState !== null && editPeriodState.periodId === period.id;
                  const isConfirmingDelete = deletePeriodConfirmId === period.id;
                  const isFirst = position === 0;
                  const isLast = position === periods.length - 1;
                  const isMoving = movingPeriodId === period.id;

                  return (
                    <tr key={period.id}>
                      {isEditing && editPeriodState !== null ? (
                        <td colSpan={5}>
                          <form className="row-edit-form" onSubmit={(event) => handleSubmitEditPeriod(event, isFirst)}>
                            <label>
                              Name
                              <input
                                type="text"
                                value={editPeriodState.name}
                                onChange={(event) =>
                                  setEditPeriodState((current) =>
                                    current === null ? current : { ...current, name: event.target.value },
                                  )
                                }
                                disabled={editPeriodSubmitting}
                                autoFocus
                                required
                              />
                            </label>
                            <span className="time-inputs">
                              <label>
                                Start time
                                <input
                                  type="time"
                                  value={editPeriodState.startTime}
                                  onChange={(event) =>
                                    setEditPeriodState((current) =>
                                      current === null ? current : { ...current, startTime: event.target.value },
                                    )
                                  }
                                  disabled={editPeriodSubmitting}
                                />
                              </label>
                              <label>
                                End time
                                <input
                                  type="time"
                                  value={editPeriodState.endTime}
                                  onChange={(event) =>
                                    setEditPeriodState((current) =>
                                      current === null ? current : { ...current, endTime: event.target.value },
                                    )
                                  }
                                  disabled={editPeriodSubmitting}
                                />
                              </label>
                            </span>
                            {!isValidTimePair(editPeriodState.startTime, editPeriodState.endTime) && (
                              <p role="alert" className="row-edit-error">
                                Enter both a start and end time, with start before end, or leave both empty.
                              </p>
                            )}
                            {isFirst ? (
                              <p className="hint-callout">
                                This is the first period of the day, so it always starts a new block.
                              </p>
                            ) : (
                              <>
                                <label className="calendar-checkbox-label">
                                  <input
                                    type="checkbox"
                                    checked={editPeriodState.startsNewBlock}
                                    onChange={(event) =>
                                      setEditPeriodState((current) =>
                                        current === null ? current : { ...current, startsNewBlock: event.target.checked },
                                      )
                                    }
                                    disabled={editPeriodSubmitting}
                                  />
                                  Starts a new block after a break
                                </label>
                                <p className="hint-callout">
                                  Use this when a longer break or lunch separates this period from the previous one.
                                  Consecutive multi-period lessons will not cross this boundary.
                                </p>
                              </>
                            )}
                            {editPeriodError !== null && (
                              <p role="alert" className="row-edit-error">
                                {editPeriodError}
                              </p>
                            )}
                            <span className="row-edit-actions">
                              <button
                                type="submit"
                                className="btn-primary"
                                disabled={editPeriodSubmitting || !editPeriodValid}
                              >
                                {editPeriodSubmitting ? "Saving…" : "Save"}
                              </button>
                              <button type="button" onClick={handleCancelEditPeriod} disabled={editPeriodSubmitting}>
                                Cancel
                              </button>
                            </span>
                          </form>
                        </td>
                      ) : (
                        <>
                          <th scope="row" data-label="Name">
                            {period.name}
                            {!period.is_instructional && (
                              <>
                                {" "}
                                <span className="legacy-badge">Non-instructional</span>
                                <p className="legacy-hint">This legacy period is not available for lesson scheduling.</p>
                              </>
                            )}
                          </th>
                          <td data-label="Start">{period.start_time ?? "—"}</td>
                          <td data-label="End">{period.end_time ?? "—"}</td>
                          <td data-label="Block">
                            {isFirst ? (
                              <span className="block-badge block-badge-first">First period of the day</span>
                            ) : period.starts_new_block ? (
                              <span className="block-badge block-badge-break">Starts a new block after a break</span>
                            ) : (
                              <span className="block-badge block-badge-continues">Continues previous block</span>
                            )}
                          </td>
                          <td data-label="Actions">
                            {isConfirmingDelete ? (
                              <span className="delete-confirm">
                                <span className="delete-confirm-text">Delete {period.name}?</span>
                                {deletePeriodError !== null && (
                                  <p role="alert" className="delete-confirm-error">
                                    {deletePeriodError}
                                  </p>
                                )}
                                <span className="delete-confirm-actions">
                                  <button
                                    type="button"
                                    onClick={() => handleConfirmDeletePeriod(period.id)}
                                    disabled={deletePeriodSubmitting}
                                  >
                                    {deletePeriodSubmitting ? "Deleting…" : "Confirm delete"}
                                  </button>
                                  <button
                                    type="button"
                                    onClick={handleCancelDeletePeriod}
                                    disabled={deletePeriodSubmitting}
                                  >
                                    Cancel
                                  </button>
                                </span>
                              </span>
                            ) : (
                              <span className="actions-group">
                                <button
                                  type="button"
                                  className="action-button move-button"
                                  aria-label={`Move ${period.name} up`}
                                  onClick={() => handleMovePeriod(period.id, "up")}
                                  disabled={locked || rowActionsDisabled || isFirst}
                                >
                                  {isMoving ? "…" : "↑"}
                                </button>
                                <button
                                  type="button"
                                  className="action-button move-button"
                                  aria-label={`Move ${period.name} down`}
                                  onClick={() => handleMovePeriod(period.id, "down")}
                                  disabled={locked || rowActionsDisabled || isLast}
                                >
                                  {isMoving ? "…" : "↓"}
                                </button>
                                <button
                                  type="button"
                                  className="action-button"
                                  onClick={() => handleStartEditPeriod(period)}
                                  disabled={locked || rowActionsDisabled}
                                  aria-describedby={locked ? "calendar-lock-banner-text" : undefined}
                                >
                                  Edit
                                </button>
                                <button
                                  type="button"
                                  className="action-button action-button-delete"
                                  onClick={() => handleDeletePeriodClick(period.id)}
                                  disabled={locked || rowActionsDisabled}
                                  aria-describedby={locked ? "calendar-lock-banner-text" : undefined}
                                >
                                  Delete
                                </button>
                              </span>
                            )}
                          </td>
                        </>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export default CalendarBellSchedulePanel;
