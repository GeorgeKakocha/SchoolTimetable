import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getTeacherAvailability, replaceTeacherAvailability } from "../api/teacherAvailability";
import { ApiError } from "../api/client";
import type {
  AvailabilityCellState,
  TeacherAvailabilityException,
  TeacherAvailabilityExceptionRequestItem,
  TeacherAvailabilityExceptionStatus,
  TeacherAvailabilityProjectionResponse,
} from "../api/types";
import { useActiveSchoolYearContext } from "../context/ActiveSchoolYearContext";
import AvailabilityGrid from "../components/availability/AvailabilityGrid";
import AvailabilityLegend from "../components/availability/AvailabilityLegend";

/**
 * Teacher Availability Slice B (Owner Decision #38): `/configuration/
 * teacher-availability`. One Teacher's complete sparse exception set
 * is edited at a time; the write unit and the wire contract exactly
 * mirror the backend's own locked shape (`api/teacherAvailability.ts`)
 * -- `AVAILABLE` is never sent, never stored explicitly in the local
 * draft, always represented by a cell's absence.
 *
 * State is 100% local `useState`/`useMemo`, matching every existing
 * page's own discipline -- no Redux/Zustand/query library. This page
 * owns everything (GET, selected Teacher, draft, dirty, save/reset,
 * errors); `AvailabilityGrid`/`AvailabilityLegend` are purely
 * presentational.
 */

type PageState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; projection: TeacherAvailabilityProjectionResponse };

type AppConfigResult =
  | { ok: true; schoolId: string; academicYearId: string }
  | { ok: false; message: string };

type RefreshState = { status: "idle" } | { status: "refreshing" } | { status: "stale"; message: string };

interface DraftEntry {
  day_id: string;
  period_id: string;
  status: TeacherAvailabilityExceptionStatus;
}

type Draft = Map<string, DraftEntry>;

const REFRESH_FAILURE_MESSAGE =
  "The change was saved, but the latest availability could not be refreshed. Retry to load the current data.";
const LOCK_RACE_MESSAGE =
  "A schedule was generated since this page loaded. Teacher availability is now read-only.";

const STATE_LABELS: Record<AvailabilityCellState, string> = {
  AVAILABLE: "Available",
  PREFER_NOT: "Prefer not",
  UNAVAILABLE: "Unavailable",
};

const NEXT_STATE: Record<AvailabilityCellState, AvailabilityCellState> = {
  AVAILABLE: "PREFER_NOT",
  PREFER_NOT: "UNAVAILABLE",
  UNAVAILABLE: "AVAILABLE",
};

/** A collision-safe composite key -- a JSON-array encoding can never
 * be ambiguous between e.g. `("a", "bc")` and `("ab", "c")` the way a
 * plain string-concatenation key could. */
function cellKey(dayId: string, periodId: string): string {
  return JSON.stringify([dayId, periodId]);
}

function draftFromExceptions(exceptions: TeacherAvailabilityException[], teacherId: string): Draft {
  const draft: Draft = new Map();
  for (const exception of exceptions) {
    if (exception.teacher_id === teacherId) {
      draft.set(cellKey(exception.day_id, exception.period_id), {
        day_id: exception.day_id,
        period_id: exception.period_id,
        status: exception.status,
      });
    }
  }
  return draft;
}

/** Order-independent comparison -- never sensitive to Map insertion
 * order or array order, matching the sparse contract's own "a set of
 * cells" semantics rather than a sequence. */
function normalizeExceptionList(items: { day_id: string; period_id: string; status: string }[]): string {
  return items
    .map((item) => `${item.day_id}|${item.period_id}|${item.status}`)
    .sort()
    .join(",");
}

function draftMatchesServer(draft: Draft, serverExceptionsForTeacher: TeacherAvailabilityException[]): boolean {
  return normalizeExceptionList(Array.from(draft.values())) === normalizeExceptionList(serverExceptionsForTeacher);
}

function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  return "Something went wrong. Please try again.";
}

function describeMutationError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Something went wrong. Please try again.";
  }
  switch (error.code) {
    case "UNKNOWN_REFERENCE": {
      const kind =
        typeof error.body?.["reference_kind"] === "string" ? (error.body["reference_kind"] as string) : "reference";
      return `Unknown ${kind}. Please reload the page and try again.`;
    }
    case "INVALID_TEACHER_AVAILABILITY": {
      const rawErrors = error.body?.["errors"];
      if (Array.isArray(rawErrors)) {
        const messages = rawErrors
          .map((item) =>
            typeof item === "object" && item !== null && typeof (item as { message?: unknown }).message === "string"
              ? (item as { message: string }).message
              : null,
          )
          .filter((message): message is string => message !== null);
        if (messages.length > 0) {
          return messages.join(" ");
        }
      }
      return error.detail;
    }
    default:
      return error.detail;
  }
}

function TeacherAvailabilityPage() {
  const { activeContext } = useActiveSchoolYearContext();
  const appConfigResult = useMemo<AppConfigResult>(
    () => activeContext === null
      ? { ok: false, message: "No active school and academic year selected." }
      : { ok: true, schoolId: activeContext.schoolId, academicYearId: activeContext.academicYearId },
    [activeContext],
  );

  const [pageState, setPageState] = useState<PageState>({ status: "loading" });
  const [retryToken, setRetryToken] = useState(0);
  const [refreshState, setRefreshState] = useState<RefreshState>({ status: "idle" });
  const [staleLockNotice, setStaleLockNotice] = useState<string | null>(null);

  const [selectedTeacherId, setSelectedTeacherId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(new Map());

  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [liveMessage, setLiveMessage] = useState("");

  useEffect(() => {
    if (!appConfigResult.ok) {
      return;
    }
    const controller = new AbortController();
    setPageState({ status: "loading" });

    getTeacherAvailability(appConfigResult.schoolId, appConfigResult.academicYearId, controller.signal)
      .then((projection) => {
        if (controller.signal.aborted) {
          return;
        }
        setPageState({ status: "ready", projection });
        const firstTeacher = projection.teachers[0];
        if (firstTeacher !== undefined) {
          setSelectedTeacherId(firstTeacher.id);
          setDraft(draftFromExceptions(projection.exceptions, firstTeacher.id));
        } else {
          setSelectedTeacherId(null);
          setDraft(new Map());
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setPageState({ status: "error", message: describeApiError(error) });
      });

    return () => {
      controller.abort();
    };
  }, [appConfigResult, retryToken]);

  const handleRetry = useCallback(() => {
    setRetryToken((token) => token + 1);
  }, []);

  /** The one authoritative re-fetch every successful (or lock-raced)
   * write triggers -- rebuilds the draft for whichever Teacher should
   * now be selected (preferring `preserveTeacherId` when supplied,
   * e.g. immediately after a Save) directly from the fresh response,
   * falling back to the first Teacher if the previously-selected one
   * no longer exists (a stale-client defense). Never optimistic. */
  const refreshProjection = useCallback(
    async (staleFailureMessage: string, preserveTeacherId?: string) => {
      if (!appConfigResult.ok) {
        return;
      }
      setRefreshState({ status: "refreshing" });
      try {
        const projection = await getTeacherAvailability(appConfigResult.schoolId, appConfigResult.academicYearId);
        setPageState({ status: "ready", projection });
        setRefreshState({ status: "idle" });
        const targetId = preserveTeacherId ?? selectedTeacherId;
        const stillExists = targetId !== null && projection.teachers.some((t) => t.id === targetId);
        const nextTeacherId = stillExists ? targetId : projection.teachers[0]?.id ?? null;
        setSelectedTeacherId(nextTeacherId);
        setDraft(nextTeacherId !== null ? draftFromExceptions(projection.exceptions, nextTeacherId) : new Map());
      } catch {
        setRefreshState({ status: "stale", message: staleFailureMessage });
      }
    },
    [appConfigResult, selectedTeacherId],
  );

  // Teacher switch while clean: re-derive the draft from the already-
  // loaded projection -- never a new GET (the full projection already
  // covers every Teacher's exceptions in one response).
  const handleTeacherChange = useCallback(
    (teacherId: string) => {
      if (pageState.status !== "ready" || refreshState.status !== "idle") {
        return;
      }
      setSelectedTeacherId(teacherId);
      setDraft(draftFromExceptions(pageState.projection.exceptions, teacherId));
      setSaveError(null);
    },
    [pageState, refreshState],
  );

  const handleCellActivate = useCallback(
    (dayId: string, periodId: string) => {
      if (pageState.status !== "ready") {
        return;
      }
      const key = cellKey(dayId, periodId);
      const currentState: AvailabilityCellState = draft.get(key)?.status ?? "AVAILABLE";
      const next = NEXT_STATE[currentState];

      setDraft((current) => {
        const updated = new Map(current);
        if (next === "AVAILABLE") {
          updated.delete(key);
        } else {
          updated.set(key, { day_id: dayId, period_id: periodId, status: next });
        }
        return updated;
      });

      const day = pageState.projection.days.find((d) => d.id === dayId);
      const period = pageState.projection.periods.find((p) => p.id === periodId);
      if (day !== undefined && period !== undefined) {
        setLiveMessage(`${day.name}, ${period.name} set to ${STATE_LABELS[next]}.`);
      }
    },
    [pageState, draft],
  );

  const getCellState = useCallback(
    (dayId: string, periodId: string): AvailabilityCellState => draft.get(cellKey(dayId, periodId))?.status ?? "AVAILABLE",
    [draft],
  );

  const isDirty = useMemo(() => {
    if (pageState.status !== "ready" || selectedTeacherId === null) {
      return false;
    }
    const serverExceptions = pageState.projection.exceptions.filter((e) => e.teacher_id === selectedTeacherId);
    return !draftMatchesServer(draft, serverExceptions);
  }, [pageState, selectedTeacherId, draft]);

  const handleReset = useCallback(() => {
    if (pageState.status !== "ready" || selectedTeacherId === null || refreshState.status !== "idle") {
      return;
    }
    setDraft(draftFromExceptions(pageState.projection.exceptions, selectedTeacherId));
    setSaveError(null);
  }, [pageState, selectedTeacherId, refreshState]);

  const handleSave = useCallback(async () => {
    if (!appConfigResult.ok || pageState.status !== "ready" || selectedTeacherId === null) {
      return;
    }
    // Defensive guard, not just the button's `disabled` attribute: the
    // server has already diverged from this client's authority
    // whenever a refresh is in flight or has failed (`refreshState.
    // status !== "idle"`), so no further PUT may be issued until a
    // fresh GET resolves it back to "idle".
    if (!isDirty || isSaving || pageState.projection.configuration_locked || refreshState.status !== "idle") {
      return;
    }
    setIsSaving(true);
    setSaveError(null);

    const exceptions: TeacherAvailabilityExceptionRequestItem[] = Array.from(draft.values()).map((entry) => ({
      day_id: entry.day_id,
      period_id: entry.period_id,
      status: entry.status,
    }));

    try {
      await replaceTeacherAvailability(appConfigResult.schoolId, appConfigResult.academicYearId, selectedTeacherId, {
        exceptions,
      });
      setStaleLockNotice(null);
      await refreshProjection(REFRESH_FAILURE_MESSAGE, selectedTeacherId);
    } catch (error) {
      if (error instanceof ApiError && error.code === "SCHEDULING_CONFIGURATION_LOCKED") {
        setStaleLockNotice(LOCK_RACE_MESSAGE);
        await refreshProjection(REFRESH_FAILURE_MESSAGE, selectedTeacherId);
      } else if (error instanceof ApiError && error.status === 404) {
        // Stale client: the selected Teacher no longer exists server-side.
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      } else {
        setSaveError(describeMutationError(error));
      }
    } finally {
      setIsSaving(false);
    }
  }, [appConfigResult, pageState, selectedTeacherId, isDirty, isSaving, draft, refreshProjection, refreshState]);

  if (!appConfigResult.ok) {
    return <p role="alert">{appConfigResult.message}</p>;
  }

  if (pageState.status === "loading") {
    return (
      <div className="availability-page">
        <h1>Teacher Availability</h1>
        <p className="page-subtitle">Set when each teacher can, should preferably not, or cannot teach.</p>
        <p>Loading teacher availability…</p>
      </div>
    );
  }

  if (pageState.status === "error") {
    return (
      <div className="availability-page">
        <h1>Teacher Availability</h1>
        <p className="page-subtitle">Set when each teacher can, should preferably not, or cannot teach.</p>
        <div role="alert" className="page-error">
          <p>{pageState.message}</p>
          <button type="button" onClick={handleRetry}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  const { projection } = pageState;
  const locked = projection.configuration_locked;
  const instructionalPeriods = projection.periods.filter((p) => p.is_instructional);
  // Mirrors `TeachingAssignmentsPage`'s own `controlsDisabled`: true
  // whenever authoritative server state is unresolved (a refresh is
  // in flight, or the last one failed and the displayed projection is
  // stale) or a write is in flight. Applied uniformly to every
  // mutation entry point below -- cells, Save, Reset, and the Teacher
  // selector -- so none of them can drift out of sync with each
  // other. `locked` is deliberately layered on top per-control rather
  // than folded in here: the Teacher selector must stay usable once
  // an authoritative (non-stale) locked projection has loaded, while
  // Save/Reset/cells must not.
  const controlsDisabled = refreshState.status !== "idle" || isSaving;

  return (
    <div className="availability-page">
      <h1>Teacher Availability</h1>
      <p className="page-subtitle">Set when each teacher can, should preferably not, or cannot teach.</p>

      {locked && (
        <div className="lock-banner" id="teacher-availability-lock-banner-text">
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

      {projection.teachers.length === 0 ? (
        <div className="setup-empty-state">
          <p>No teachers yet.</p>
          <p>Add teachers in School Setup before setting availability.</p>
          <Link to="/configuration/setup" className="action-button">
            Go to School Setup
          </Link>
        </div>
      ) : (
        <>
          <div className="availability-teacher-selector">
            <label>
              Teacher
              <select
                value={selectedTeacherId ?? ""}
                onChange={(event) => handleTeacherChange(event.target.value)}
                disabled={isDirty || controlsDisabled}
              >
                {projection.teachers.map((teacher) => (
                  <option key={teacher.id} value={teacher.id}>
                    {teacher.name}
                  </option>
                ))}
              </select>
            </label>
            {isDirty && (
              <p className="availability-dirty-hint" role="status">
                Save or reset changes before switching teachers.
              </p>
            )}
          </div>

          <AvailabilityLegend />

          {refreshState.status === "refreshing" && (
            <p className="refreshing-note" role="status">
              Refreshing…
            </p>
          )}

          <AvailabilityGrid
            days={projection.days}
            periods={instructionalPeriods}
            getCellState={getCellState}
            disabled={locked || controlsDisabled}
            onCellActivate={handleCellActivate}
          />

          <div className="availability-action-bar">
            {saveError !== null && (
              <p role="alert" className="availability-save-error">
                {saveError}
              </p>
            )}
            <button type="button" onClick={handleReset} disabled={!isDirty || locked || controlsDisabled}>
              Reset changes
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={handleSave}
              disabled={!isDirty || locked || controlsDisabled}
            >
              {isSaving ? "Saving…" : "Save changes"}
            </button>
          </div>

          <div aria-live="polite" role="status" className="sr-only">
            {liveMessage}
          </div>
        </>
      )}
    </div>
  );
}

export default TeacherAvailabilityPage;
