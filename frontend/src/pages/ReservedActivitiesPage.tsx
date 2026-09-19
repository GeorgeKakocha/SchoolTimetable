import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  createReservedActivity,
  deleteReservedActivity,
  getReservedActivities,
  updateReservedActivity,
} from "../api/reservedActivities";
import type { ReservedActivitiesProjectionResponse, ReservedActivityItem } from "../api/reservedActivities";
import { ApiError } from "../api/client";
import { useActiveSchoolYearContext } from "../context/ActiveSchoolYearContext";
import ReservedActivityEditor from "../components/ReservedActivityEditor";
import type { ReservedActivityDraftValues } from "../components/ReservedActivityEditor";
import ReservedActivityCard from "../components/ReservedActivityCard";
import type { SchoolSetupNavigationState } from "./SchoolSetupPage";

/**
 * Reserved Activities Slice B: `/configuration/reserved-activities`.
 * Owns everything -- the single authoritative GET projection, loading/
 * error/refresh/lock-race/transient-message state, which editor (if
 * any) is open and with what draft, delete confirmation, and all write
 * orchestration/error classification. `ReservedActivityEditor`/
 * `ReservedActivityCard` are purely presentational; neither owns API
 * state.
 *
 * State machine mirrors `TeachingAssignmentsPage`/`SubjectsPanel`
 * exactly (`PageState`/`RefreshState`/`performWrite`/authoritative
 * refetch-after-every-mutation), extended with two Reserved-Activity-
 * specific corrections from the closed frontend contract gate:
 *
 * 1. A `referenceStale` write outcome (`UNKNOWN_REFERENCE`/
 *    `NON_SPECIAL_ACTIVITY_TARGET`) closes the editor and discards its
 *    draft immediately, rather than preserving it -- a Reserved
 *    Activity draft carries five independent potentially-dangling
 *    references, making partial client-side reconciliation
 *    meaningfully more error-prone than any single-reference page's.
 * 2. A page-level prerequisite surface (missing Special Activities/
 *    Classes/instructional calendar data) replaces the Add toolbar
 *    entirely rather than ever exposing a broken/blocked editor --
 *    computed fresh from each successful GET, and deliberately
 *    subordinate to the lock/stale banners (`missingPrerequisites` is
 *    only consulted once the page is confirmed unlocked and fresh).
 */

type PageState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; projection: ReservedActivitiesProjectionResponse };

type AppConfigResult =
  | { ok: true; schoolId: string; academicYearId: string }
  | { ok: false; message: string };

type RefreshState = { status: "idle" } | { status: "refreshing" } | { status: "stale"; message: string };

type EditorState =
  | { mode: "create" }
  | { mode: "edit"; item: ReservedActivityItem };

type WriteOutcome =
  | { ok: true }
  | { ok: false; kind: "lockRace" }
  | { ok: false; kind: "notFound" }
  | { ok: false; kind: "referenceStale" }
  | { ok: false; kind: "inline" };

const REFRESH_FAILURE_MESSAGE =
  "The change was saved, but the latest reserved activities could not be refreshed. Retry to load the current data.";
const LOCK_RACE_MESSAGE = "A schedule was generated since this page loaded. Reserved activities are now read-only.";
const REFERENCE_STALE_MESSAGE = "The configuration changed. Review the latest data and try again.";

function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  return "Something went wrong. Please try again.";
}

function resolveName<T extends { id: string; name: string }>(catalog: readonly T[], id: string | undefined): string | null {
  if (id === undefined) {
    return null;
  }
  return catalog.find((entry) => entry.id === id)?.name ?? null;
}

function readContextString(context: unknown, field: string): string | undefined {
  if (typeof context !== "object" || context === null) {
    return undefined;
  }
  const value = (context as Record<string, unknown>)[field];
  return typeof value === "string" ? value : undefined;
}

function readContextNumber(context: unknown, field: string): number | undefined {
  if (typeof context !== "object" || context === null) {
    return undefined;
  }
  const value = (context as Record<string, unknown>)[field];
  return typeof value === "number" ? value : undefined;
}

/** Maps one raw `INVALID_RESERVED_ACTIVITY` diagnostic (`{code,
 * message, context}`) to a human-facing string, resolving every
 * natural ID in `context` against the current projection's own
 * catalogs -- never showing a raw ID. An unrecognized code falls back
 * to the diagnostic's own backend `message`, matching every other
 * page's existing convention. */
function describeOneDiagnostic(diagnostic: unknown, projection: ReservedActivitiesProjectionResponse): string {
  if (typeof diagnostic !== "object" || diagnostic === null) {
    return "This reserved activity is invalid.";
  }
  const code = (diagnostic as { code?: unknown }).code;
  const message = (diagnostic as { message?: unknown }).message;
  const context = (diagnostic as { context?: unknown }).context;
  const fallback = typeof message === "string" ? message : "This reserved activity is invalid.";
  if (typeof code !== "string") {
    return fallback;
  }

  const dayName = resolveName(projection.days, readContextString(context, "day_id"));
  const periodName = resolveName(projection.periods, readContextString(context, "period_id"));
  const slotLabel = dayName !== null && periodName !== null ? `${dayName} ${periodName}` : "the selected time slot";

  switch (code) {
    case "RESERVED_BLOCK_REQUIRES_CLASS_SECTION":
      return "At least one class is required.";
    case "RESERVED_BLOCK_REQUIRES_SLOT":
      return "At least one time slot is required.";
    case "DUPLICATE_RESERVED_BLOCK_CLASS_SECTION": {
      const className = resolveName(projection.class_sections, readContextString(context, "class_id"));
      return `A class was selected more than once${className !== null ? `: ${className}` : ""}.`;
    }
    case "DUPLICATE_RESERVED_BLOCK_SLOT":
      return `A time slot was selected more than once: ${slotLabel}.`;
    case "RESERVED_BLOCK_NON_INSTRUCTIONAL_SLOT":
      return `${slotLabel} is not an instructional period.`;
    case "RESERVED_BLOCK_TEACHER_UNAVAILABLE": {
      const teacherName = resolveName(projection.teachers, readContextString(context, "teacher_id"));
      return `${teacherName ?? "The selected teacher"} is unavailable on ${slotLabel}.`;
    }
    case "RESERVED_BLOCK_CLASS_SLOT_COLLISION": {
      const className = resolveName(projection.class_sections, readContextString(context, "class_section_id"));
      const conflictingName = resolveConflictingSpecialActivityName(projection, readContextString(context, "conflicting_reserved_block_id"));
      if (conflictingName !== null) {
        return `Conflicts with the existing ${conflictingName} reservation: ${className ?? "a class"}, ${slotLabel}.`;
      }
      return "Conflicts with another existing Reserved Activity.";
    }
    case "RESERVED_BLOCK_TEACHER_SLOT_COLLISION": {
      const teacherName = resolveName(projection.teachers, readContextString(context, "teacher_id"));
      const conflictingName = resolveConflictingSpecialActivityName(projection, readContextString(context, "conflicting_reserved_block_id"));
      if (conflictingName !== null) {
        return `Conflicts with the existing ${conflictingName} reservation: ${teacherName ?? "a teacher"}, ${slotLabel}.`;
      }
      return "Conflicts with another existing Reserved Activity.";
    }
    case "RESERVED_RESOURCE_CAPACITY_EXCEEDED": {
      const resourceName = resolveName(projection.resources, readContextString(context, "resource_id"));
      const capacity = readContextNumber(context, "capacity");
      return `${resourceName ?? "The selected resource"} is already fully booked at ${slotLabel}${capacity !== undefined ? ` (capacity ${capacity})` : ""}.`;
    }
    default:
      return fallback;
  }
}

function resolveConflictingSpecialActivityName(
  projection: ReservedActivitiesProjectionResponse,
  conflictingId: string | undefined,
): string | null {
  if (conflictingId === undefined) {
    return null;
  }
  const conflictingItem = projection.reserved_activities.find((item) => item.id === conflictingId);
  if (conflictingItem === undefined) {
    return null;
  }
  return resolveName(projection.special_activities, conflictingItem.special_activity_id);
}

function describeMutationErrors(error: unknown, projection: ReservedActivitiesProjectionResponse): string[] {
  if (!(error instanceof ApiError)) {
    return ["Something went wrong. Please try again."];
  }
  if (error.code === "INVALID_RESERVED_ACTIVITY") {
    const rawErrors = error.body?.["errors"];
    if (Array.isArray(rawErrors) && rawErrors.length > 0) {
      return rawErrors.map((diagnostic) => describeOneDiagnostic(diagnostic, projection));
    }
  }
  return [error.detail];
}

interface MissingPrerequisite {
  key: string;
  heading: string;
  body: string;
  navigationState?: SchoolSetupNavigationState;
}

function computeMissingPrerequisites(projection: ReservedActivitiesProjectionResponse): MissingPrerequisite[] {
  const missing: MissingPrerequisite[] = [];
  if (projection.special_activities.length === 0) {
    missing.push({
      key: "special_activities",
      heading: "No Special Activities yet.",
      body: "Create a Special Activity before adding a Reserved Activity.",
      navigationState: { requestedTab: "special-activities" },
    });
  }
  if (projection.class_sections.length === 0) {
    missing.push({
      key: "class_sections",
      heading: "No Classes yet.",
      body: "Create a Class before adding a Reserved Activity.",
      navigationState: { requestedTab: "classes" },
    });
  }
  const instructionalPeriods = projection.periods.filter((period) => period.is_instructional);
  if (projection.days.length === 0 || instructionalPeriods.length === 0) {
    missing.push({
      key: "instructional_slots",
      heading: "No instructional scheduling slots yet.",
      body: "The school calendar currently has no instructional scheduling slots.",
    });
  }
  return missing;
}

function ReservedActivitiesPage() {
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
  const [transientMessage, setTransientMessage] = useState<string | null>(null);

  const [editor, setEditor] = useState<EditorState | null>(null);
  const [editorSubmitting, setEditorSubmitting] = useState(false);
  const [editorErrors, setEditorErrors] = useState<string[] | null>(null);
  const [editorOpener, setEditorOpener] = useState<HTMLElement | null>(null);
  const firstFieldRef = useRef<HTMLSelectElement | null>(null);

  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    if (!appConfigResult.ok) {
      return;
    }
    const controller = new AbortController();
    setPageState({ status: "loading" });

    getReservedActivities(appConfigResult.schoolId, appConfigResult.academicYearId, controller.signal)
      .then((projection) => {
        if (controller.signal.aborted) {
          return;
        }
        setPageState({ status: "ready", projection });
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

  const refreshProjection = useCallback(
    async (staleFailureMessage: string): Promise<boolean> => {
      if (!appConfigResult.ok) {
        return false;
      }
      setRefreshState({ status: "refreshing" });
      try {
        const projection = await getReservedActivities(appConfigResult.schoolId, appConfigResult.academicYearId);
        setPageState({ status: "ready", projection });
        setRefreshState({ status: "idle" });
        return true;
      } catch {
        setRefreshState({ status: "stale", message: staleFailureMessage });
        return false;
      }
    },
    [appConfigResult],
  );

  const performWrite = useCallback(
    async (
      action: () => Promise<unknown>,
      projection: ReservedActivitiesProjectionResponse,
      onInlineErrors: (messages: string[]) => void,
    ): Promise<WriteOutcome> => {
      try {
        await action();
        setStaleLockNotice(null);
        return { ok: true };
      } catch (error) {
        if (error instanceof ApiError && error.code === "SCHEDULING_CONFIGURATION_LOCKED") {
          setStaleLockNotice(LOCK_RACE_MESSAGE);
          return { ok: false, kind: "lockRace" };
        }
        if (error instanceof ApiError && error.status === 404) {
          setPageState({ status: "error", message: error.detail });
          return { ok: false, kind: "notFound" };
        }
        if (error instanceof ApiError && (error.code === "UNKNOWN_REFERENCE" || error.code === "NON_SPECIAL_ACTIVITY_TARGET")) {
          return { ok: false, kind: "referenceStale" };
        }
        onInlineErrors(describeMutationErrors(error, projection));
        return { ok: false, kind: "inline" };
      }
    },
    [],
  );

  const closeEditor = useCallback(() => {
    setEditor(null);
    setEditorErrors(null);
  }, []);

  // Returns focus to the button that opened the editor, once it
  // closes -- exact `TeachingAssignmentsPage`/`AssignmentDrawer` opener
  // pattern, adapted from a drawer to this contained panel.
  useEffect(() => {
    if (editor === null && editorOpener !== null) {
      editorOpener.focus();
      setEditorOpener(null);
    }
  }, [editor, editorOpener]);

  const handleAddClick = useCallback((event: React.MouseEvent<HTMLButtonElement>) => {
    setEditorOpener(event.currentTarget);
    setEditorErrors(null);
    setStaleLockNotice(null);
    setTransientMessage(null);
    setEditor({ mode: "create" });
  }, []);

  const handleEditClick = useCallback((item: ReservedActivityItem) => {
    setEditorErrors(null);
    setStaleLockNotice(null);
    setTransientMessage(null);
    setEditor({ mode: "edit", item });
  }, []);

  const handleEditorCancel = useCallback(() => {
    closeEditor();
  }, [closeEditor]);

  const handleEditorSubmit = useCallback(
    async (values: ReservedActivityDraftValues) => {
      if (!appConfigResult.ok || editor === null || pageState.status !== "ready") {
        return;
      }
      setEditorSubmitting(true);
      setEditorErrors(null);
      const body = {
        special_activity_id: values.specialActivityId,
        class_section_ids: values.classSectionIds,
        teacher_id: values.teacherId,
        slots: values.slots,
        resource_id: values.resourceId,
      };
      const action =
        editor.mode === "create"
          ? () => createReservedActivity(appConfigResult.schoolId, appConfigResult.academicYearId, body)
          : () =>
              updateReservedActivity(
                appConfigResult.schoolId,
                appConfigResult.academicYearId,
                editor.item.id,
                body,
              );

      const outcome = await performWrite(action, pageState.projection, (messages) => setEditorErrors(messages));
      setEditorSubmitting(false);

      if (outcome.ok || outcome.kind === "lockRace" || outcome.kind === "referenceStale" || outcome.kind === "notFound") {
        closeEditor();
      }
      if (outcome.ok || outcome.kind === "lockRace") {
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      } else if (outcome.kind === "referenceStale") {
        const refreshed = await refreshProjection(REFRESH_FAILURE_MESSAGE);
        if (refreshed) {
          setTransientMessage(REFERENCE_STALE_MESSAGE);
        }
      }
    },
    [appConfigResult, editor, pageState, performWrite, closeEditor, refreshProjection],
  );

  const handleDeleteClick = useCallback((id: string) => {
    setDeleteConfirmId(id);
    setDeleteError(null);
    setStaleLockNotice(null);
    setTransientMessage(null);
  }, []);

  const handleCancelDelete = useCallback(() => {
    setDeleteConfirmId(null);
    setDeleteError(null);
  }, []);

  const handleConfirmDelete = useCallback(
    async (id: string) => {
      if (!appConfigResult.ok || pageState.status !== "ready") {
        return;
      }
      setDeleteSubmitting(true);
      setDeleteError(null);

      const outcome = await performWrite(
        () => deleteReservedActivity(appConfigResult.schoolId, appConfigResult.academicYearId, id),
        pageState.projection,
        (messages) => setDeleteError(messages.join(" ")),
      );
      setDeleteSubmitting(false);

      if (outcome.ok || outcome.kind === "lockRace" || outcome.kind === "notFound" || outcome.kind === "referenceStale") {
        setDeleteConfirmId(null);
      }
      if (outcome.ok || outcome.kind === "lockRace") {
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [appConfigResult, pageState, performWrite, refreshProjection],
  );

  if (!appConfigResult.ok) {
    return <p role="alert">{appConfigResult.message}</p>;
  }

  return (
    <div className="reserved-activities-page">
      <h1>Reserved Activities</h1>
      <p className="page-subtitle">
        These activities are fixed at the exact classes, teacher, and time slots you choose here — the schedule
        builder places everything else around them.
      </p>

      {pageState.status === "loading" && <p>Loading reserved activities…</p>}

      {pageState.status === "error" && (
        <div role="alert" className="page-error">
          <p>{pageState.message}</p>
          <button type="button" onClick={handleRetry}>
            Retry
          </button>
        </div>
      )}

      {pageState.status === "ready" && (
        <ReadyReservedActivities
          projection={pageState.projection}
          refreshState={refreshState}
          staleLockNotice={staleLockNotice}
          transientMessage={transientMessage}
          onDismissTransientMessage={() => setTransientMessage(null)}
          onRetryRefresh={() => refreshProjection(refreshState.status === "stale" ? refreshState.message : REFRESH_FAILURE_MESSAGE)}
          editor={editor}
          editorSubmitting={editorSubmitting}
          editorErrors={editorErrors}
          firstFieldRef={firstFieldRef}
          onAddClick={handleAddClick}
          onEditorCancel={handleEditorCancel}
          onEditorSubmit={handleEditorSubmit}
          deleteConfirmId={deleteConfirmId}
          deleteSubmitting={deleteSubmitting}
          deleteError={deleteError}
          onEditClick={handleEditClick}
          onDeleteClick={handleDeleteClick}
          onConfirmDelete={handleConfirmDelete}
          onCancelDelete={handleCancelDelete}
        />
      )}
    </div>
  );
}

interface ReadyReservedActivitiesProps {
  projection: ReservedActivitiesProjectionResponse;
  refreshState: RefreshState;
  staleLockNotice: string | null;
  transientMessage: string | null;
  onDismissTransientMessage: () => void;
  onRetryRefresh: () => void;
  editor: EditorState | null;
  editorSubmitting: boolean;
  editorErrors: string[] | null;
  firstFieldRef: React.RefObject<HTMLSelectElement | null>;
  onAddClick: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onEditorCancel: () => void;
  onEditorSubmit: (values: ReservedActivityDraftValues) => void;
  deleteConfirmId: string | null;
  deleteSubmitting: boolean;
  deleteError: string | null;
  onEditClick: (item: ReservedActivityItem) => void;
  onDeleteClick: (id: string) => void;
  onConfirmDelete: (id: string) => void;
  onCancelDelete: () => void;
}

function ReadyReservedActivities({
  projection,
  refreshState,
  staleLockNotice,
  transientMessage,
  onDismissTransientMessage,
  onRetryRefresh,
  editor,
  editorSubmitting,
  editorErrors,
  firstFieldRef,
  onAddClick,
  onEditorCancel,
  onEditorSubmit,
  deleteConfirmId,
  deleteSubmitting,
  deleteError,
  onEditClick,
  onDeleteClick,
  onConfirmDelete,
  onCancelDelete,
}: ReadyReservedActivitiesProps) {
  const locked = projection.configuration_locked;
  const isStale = refreshState.status === "stale";
  const isFresh = !locked && !isStale && refreshState.status === "idle";
  const missingPrerequisites = isFresh ? computeMissingPrerequisites(projection) : [];
  const controlsDisabled = refreshState.status !== "idle" || editor !== null || deleteConfirmId !== null;
  const instructionalPeriods = projection.periods.filter((period) => period.is_instructional);

  return (
    <>
      {locked && (
        <div className="lock-banner" id="reserved-activities-lock-banner-text">
          Scheduling configuration is locked because a schedule already exists.
        </div>
      )}

      {staleLockNotice !== null && (
        <div className="lock-race-banner" role="alert">
          {staleLockNotice}
        </div>
      )}

      {isStale && (
        <div className="stale-banner" role="alert">
          <p>{refreshState.status === "stale" ? refreshState.message : REFRESH_FAILURE_MESSAGE}</p>
          <button type="button" onClick={onRetryRefresh}>
            Retry
          </button>
        </div>
      )}

      {transientMessage !== null && (
        <div className="lock-race-banner" role="status">
          <span>{transientMessage}</span>
          <button type="button" aria-label="Dismiss" onClick={onDismissTransientMessage}>
            ×
          </button>
        </div>
      )}

      {refreshState.status === "refreshing" && (
        <p className="refreshing-note" role="status">
          Refreshing…
        </p>
      )}

      {!locked && !isStale && missingPrerequisites.length > 0 && (
        <div className="reserved-activities-prerequisites">
          {missingPrerequisites.map((prerequisite) => (
            <div key={prerequisite.key} className="reserved-activities-prerequisite">
              <p className="reserved-activities-prerequisite-heading">{prerequisite.heading}</p>
              <p>{prerequisite.body}</p>
              {prerequisite.navigationState !== undefined && (
                <Link to="/configuration/setup" state={prerequisite.navigationState} className="action-button">
                  Go to School Setup
                </Link>
              )}
            </div>
          ))}
        </div>
      )}

      {!locked && !isStale && missingPrerequisites.length === 0 && (
        <div className="reserved-activities-toolbar">
          <button
            type="button"
            className="btn-primary"
            onClick={onAddClick}
            disabled={controlsDisabled}
          >
            + Add reserved activity
          </button>
        </div>
      )}

      {editor !== null && (
        <ReservedActivityEditor
          mode={editor.mode}
          specialActivities={projection.special_activities}
          classSections={projection.class_sections}
          teachers={projection.teachers}
          days={projection.days}
          instructionalPeriods={instructionalPeriods}
          resources={projection.resources}
          initialValues={
            editor.mode === "edit"
              ? {
                  specialActivityId: editor.item.special_activity_id,
                  classSectionIds: editor.item.class_section_ids,
                  teacherId: editor.item.teacher_id,
                  slots: editor.item.slots,
                  resourceId: editor.item.resource_id,
                }
              : undefined
          }
          submitting={editorSubmitting}
          errors={editorErrors}
          firstFieldRef={firstFieldRef}
          onCancel={onEditorCancel}
          onSubmit={onEditorSubmit}
        />
      )}

      {projection.reserved_activities.length === 0 ? (
        <p>No reserved activities yet.</p>
      ) : (
        <ul className="reserved-activities-list">
          {projection.reserved_activities.map((item) => (
            <ReservedActivityCard
              key={item.id}
              item={item}
              specialActivities={projection.special_activities}
              classSections={projection.class_sections}
              teachers={projection.teachers}
              resources={projection.resources}
              days={projection.days}
              periods={projection.periods}
              locked={locked}
              controlsDisabled={controlsDisabled}
              deleteConfirmId={deleteConfirmId}
              deleteSubmitting={deleteSubmitting}
              deleteError={deleteError}
              onEditClick={onEditClick}
              onDeleteClick={onDeleteClick}
              onConfirmDelete={onConfirmDelete}
              onCancelDelete={onCancelDelete}
            />
          ))}
        </ul>
      )}
    </>
  );
}

export default ReservedActivitiesPage;
