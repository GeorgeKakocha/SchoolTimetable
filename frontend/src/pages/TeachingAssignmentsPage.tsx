import { useCallback, useEffect, useState } from "react";
import {
  createTeachingAssignment,
  deleteTeachingAssignment,
  getTeachingAssignments,
  updateTeachingAssignment,
} from "../api/teachingAssignments";
import { ApiError } from "../api/client";
import type {
  TeacherWorkload,
  TeachingAssignment,
  TeachingAssignmentResourceOption,
  TeachingAssignmentsProjectionResponse,
  TeachingAssignmentWriteRequest,
  ValidationDiagnostic,
  WholeClassTarget,
} from "../api/types";
import { AppConfigError, loadAppConfig } from "../config/appConfig";
import AssignmentDrawer, { type AssignmentDrawerValues } from "./AssignmentDrawer";

/**
 * Phase 3C.3a: `/configuration/teaching-assignments`, read-only
 * foundation. Loads the dedicated Phase 3C.2b page projection
 * (`GET .../teaching-assignments`) directly -- never reconstructs
 * assignment/workload/target semantics from `/config` itself, matching
 * the locked design gate.
 *
 * Visual-correction follow-up (based on a real manual-browser pass):
 * workload moved from a narrow single-column table to a wider
 * two-column row grid; the assignments table's columns are explicitly
 * width-balanced (`col-*` classes) so "Class / Group" no longer
 * dominates; the "Type" header/column is now "Configuration"; advanced
 * rows show small individual reason chips instead of one
 * comma-joined sentence; `SUBGROUP`/`MERGED_CLASSES` badges are styled
 * quieter than the "Advanced" status badge (target semantics, not a
 * warning). No data/contract change accompanies any of this.
 *
 * Phase 3C.3b (`docs/DECISIONS.md` #34-#36's locked write scope) adds
 * create/edit/delete for plain `WHOLE_CLASS` assignments only --
 * advanced rows stay conceptually read-only for their own reason
 * (unrelated to the global lock), never conflated with it (see
 * `ActionsCell` below). Every mutation is followed by an authoritative
 * re-fetch of this same unified GET projection -- assignments,
 * workloads, and `configuration_locked` are only ever taken from that
 * response, never hand-patched locally from a write's own `{id,
 * warnings}`/`{deleted_id, warnings}` body (Decision #34's write
 * response deliberately carries nothing else). State stays
 * component-local `useState`, matching every other page in this
 * frontend -- no Redux/Zustand/query library.
 */

type PageState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; projection: TeachingAssignmentsProjectionResponse };

type AppConfigResult =
  | { ok: true; schoolId: string; academicYearId: string }
  | { ok: false; message: string };

/** After a successful mutation, `refreshProjection` re-fetches the
 * unified GET projection to become the page's one source of truth
 * again. If that specific re-fetch fails, the write itself already
 * succeeded server-side -- so this is never reported as a failed
 * mutation; instead the (now possibly outdated) projection already on
 * screen is kept visible but marked `stale`, every mutation control is
 * disabled, and `Retry` re-runs the same re-fetch. */
type RefreshState = { status: "idle" } | { status: "refreshing" } | { status: "stale"; message: string };

type DrawerState = { mode: "create" } | { mode: "edit"; assignment: TeachingAssignment };

const REFRESH_FAILURE_MESSAGE =
  "The change was saved, but the latest configuration could not be refreshed. Retry to load the current data.";
const LOCK_RACE_MESSAGE =
  "A schedule was generated since this page loaded. Teaching assignments are now read-only.";

/** Every `advanced_reasons` code `teaching_assignment_rules.plain_reasons`
 * (backend) can currently produce, mapped to a friendly label -- purely
 * presentational, never a change to backend semantics. An unrecognized
 * future code falls back to its raw form rather than breaking render. */
const ADVANCED_REASON_LABELS: Record<string, string> = {
  participant_group_role: "Non-whole-class target",
  split_group_id: "Split group",
  block_policy: "Block pattern",
  distribution_policy: "Distribution rule",
  time_preferences: "Preferred time",
  resource_requirement: "Resource requirement",
  fixed_placement: "Fixed placement",
};

function friendlyReason(code: string): string {
  return ADVANCED_REASON_LABELS[code] ?? code;
}

/** `teaching_assignment_rules.WARNING_ONLY_VALIDATION_CODES` (backend):
 * the two save-time diagnostics that block nothing and are surfaced as
 * informational, post-success warnings rather than errors. An
 * unrecognized future code still renders (via its raw `code`/
 * `message`), never dropped silently. */
const WARNING_LABELS: Record<string, string> = {
  TEACHER_OVERLOADED: "Teacher workload is high",
  CLASS_OCCUPANCY_MISMATCH: "Class occupancy mismatch",
};

const GROUP_ROLE_LABELS: Record<string, string> = {
  SUBGROUP: "Subgroup",
  MERGED_CLASSES: "Merged classes",
};

function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  return "Something went wrong. Please try again.";
}

/** Maps a mutation's structured `ApiError` (`docs/DECISIONS.md`
 * #34-#36's locked error contract) to a safe, concise inline message.
 * `SCHEDULING_CONFIGURATION_LOCKED` (stale-lock race) and a 404 (stale
 * client) are handled one level up, before this is ever called --
 * this only covers the errors that stay inline on the initiating
 * drawer/delete-confirmation. */
function describeMutationError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Something went wrong. Please try again.";
  }
  switch (error.code) {
    case "DUPLICATE_TEACHING_ASSIGNMENT":
      return "This teacher already has an assignment for this class and activity.";
    case "NON_WHOLE_CLASS_TARGET":
      return "This class is not a valid whole-class assignment target.";
    case "UNKNOWN_REFERENCE": {
      const kind = typeof error.body?.["reference_kind"] === "string" ? (error.body["reference_kind"] as string) : "reference";
      return `Unknown ${kind}. Please close this form and try again.`;
    }
    case "ADVANCED_REQUIREMENT_NOT_EDITABLE":
      return "This assignment is no longer plain/editable -- it now has advanced configuration.";
    case "INVALID_TEACHING_ASSIGNMENT": {
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

type WriteOutcome = { ok: true } | { ok: false; lockRace: boolean; notFound: boolean };

function TeachingAssignmentsPage() {
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

  const [pageState, setPageState] = useState<PageState>({ status: "loading" });
  const [retryToken, setRetryToken] = useState(0);
  const [refreshState, setRefreshState] = useState<RefreshState>({ status: "idle" });
  const [warnings, setWarnings] = useState<ValidationDiagnostic[]>([]);
  const [staleLockNotice, setStaleLockNotice] = useState<string | null>(null);

  const [drawer, setDrawer] = useState<DrawerState | null>(null);
  const [drawerSubmitting, setDrawerSubmitting] = useState(false);
  const [drawerError, setDrawerError] = useState<string | null>(null);
  const [drawerOpener, setDrawerOpener] = useState<HTMLElement | null>(null);

  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Initial load (and the page-level error state's own Retry) -- unchanged
  // from Phase 3C.3a. A post-mutation refresh deliberately uses the
  // separate `refreshProjection` below instead, so it never blanks the
  // page back to this "loading" state.
  useEffect(() => {
    if (!appConfigResult.ok) {
      return;
    }
    const controller = new AbortController();
    setPageState({ status: "loading" });

    getTeachingAssignments(appConfigResult.schoolId, appConfigResult.academicYearId, controller.signal)
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

  /** The one authoritative re-fetch every successful (or lock-raced)
   * mutation triggers. Keeps the current page visible throughout --
   * never resets `pageState` to "loading" -- and on failure marks the
   * existing projection `stale` rather than discarding it.
   *
   * Deliberately never clears `staleLockNotice` itself: that transient
   * notice is set (by `performWrite`) in the very same tick this is
   * called from, and a same-tick set-then-clear here would let React
   * batch both into one commit -- the notice would never actually
   * render before disappearing. It's cleared instead the moment the
   * user moves on (starting a new mutation) or the moment a later
   * mutation succeeds outright (see `performWrite` below). */
  const refreshProjection = useCallback(
    async (staleFailureMessage: string) => {
      if (!appConfigResult.ok) {
        return;
      }
      setRefreshState({ status: "refreshing" });
      try {
        const projection = await getTeachingAssignments(appConfigResult.schoolId, appConfigResult.academicYearId);
        setPageState({ status: "ready", projection });
        setRefreshState({ status: "idle" });
      } catch {
        setRefreshState({ status: "stale", message: staleFailureMessage });
      }
    },
    [appConfigResult],
  );

  /** Shared by create/update/delete: runs the write, classifies any
   * failure once, and applies every side effect that does NOT depend
   * on which specific surface (drawer vs. delete-confirmation)
   * initiated it. The caller only decides what to close and whether to
   * refresh. */
  const performWrite = useCallback(
    async (
      action: () => Promise<{ warnings: ValidationDiagnostic[] }>,
      onInlineError: (message: string) => void,
    ): Promise<WriteOutcome> => {
      try {
        const result = await action();
        setWarnings(result.warnings);
        setStaleLockNotice(null);
        return { ok: true };
      } catch (error) {
        if (error instanceof ApiError && error.code === "SCHEDULING_CONFIGURATION_LOCKED") {
          setStaleLockNotice(LOCK_RACE_MESSAGE);
          return { ok: false, lockRace: true, notFound: false };
        }
        if (error instanceof ApiError && error.status === 404) {
          setPageState({ status: "error", message: error.detail });
          return { ok: false, lockRace: false, notFound: true };
        }
        onInlineError(describeMutationError(error));
        return { ok: false, lockRace: false, notFound: false };
      }
    },
    [],
  );

  const closeDrawer = useCallback(() => {
    setDrawer(null);
    setDrawerError(null);
  }, []);

  // Returns focus to the button that opened the drawer, once it closes.
  // Deliberately an effect, not a direct `.focus()` call inside
  // `closeDrawer` itself: while the drawer is open the opener button is
  // disabled (`controlsDisabled`, so it can't be clicked again mid-flow)
  // -- a disabled element silently refuses `.focus()` -- and that
  // `disabled` attribute in the DOM only flips back once this
  // component re-renders with `drawer === null`. Running here, after
  // that render has committed, is what makes the button focusable
  // again in time.
  useEffect(() => {
    if (drawer === null && drawerOpener !== null) {
      drawerOpener.focus();
      setDrawerOpener(null);
    }
  }, [drawer, drawerOpener]);

  const handleAddClick = useCallback((event: React.MouseEvent<HTMLButtonElement>) => {
    setDrawerOpener(event.currentTarget);
    setDrawerError(null);
    setStaleLockNotice(null);
    setDrawer({ mode: "create" });
  }, []);

  const handleEditClick = useCallback((event: React.MouseEvent<HTMLButtonElement>, assignment: TeachingAssignment) => {
    setDrawerOpener(event.currentTarget);
    setDrawerError(null);
    setStaleLockNotice(null);
    setDrawer({ mode: "edit", assignment });
  }, []);

  const handleDrawerSubmit = useCallback(
    async (values: AssignmentDrawerValues) => {
      if (!appConfigResult.ok || drawer === null) {
        return;
      }
      setDrawerSubmitting(true);
      setDrawerError(null);
      const body: TeachingAssignmentWriteRequest = {
        teacher_id: values.teacherId,
        participant_group_id: values.participantGroupId,
        activity_id: values.activityId,
        weekly_periods: values.weeklyPeriods,
        resource_id: values.resourceId,
      };
      const action =
        drawer.mode === "create"
          ? () => createTeachingAssignment(appConfigResult.schoolId, appConfigResult.academicYearId, body)
          : () =>
              updateTeachingAssignment(
                appConfigResult.schoolId,
                appConfigResult.academicYearId,
                drawer.assignment.id,
                body,
              );

      const outcome = await performWrite(action, setDrawerError);
      setDrawerSubmitting(false);

      if (outcome.ok || outcome.lockRace || outcome.notFound) {
        closeDrawer();
      }
      if (outcome.ok || outcome.lockRace) {
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [appConfigResult, drawer, performWrite, closeDrawer, refreshProjection],
  );

  const handleDeleteClick = useCallback((_event: React.MouseEvent<HTMLButtonElement>, assignment: TeachingAssignment) => {
    setDeleteConfirmId(assignment.id);
    setDeleteError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelDelete = useCallback(() => {
    setDeleteConfirmId(null);
    setDeleteError(null);
  }, []);

  const handleConfirmDelete = useCallback(
    async (assignment: TeachingAssignment) => {
      if (!appConfigResult.ok) {
        return;
      }
      setDeleteSubmitting(true);
      setDeleteError(null);

      const outcome = await performWrite(
        () => deleteTeachingAssignment(appConfigResult.schoolId, appConfigResult.academicYearId, assignment.id),
        setDeleteError,
      );
      setDeleteSubmitting(false);

      if (outcome.ok || outcome.lockRace || outcome.notFound) {
        setDeleteConfirmId(null);
      }
      if (outcome.ok || outcome.lockRace) {
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [appConfigResult, performWrite, refreshProjection],
  );

  const controlsDisabled = refreshState.status !== "idle" || drawer !== null || deleteConfirmId !== null;

  return (
    <div className="assignments-page">
      <h1>Teaching Assignments</h1>
      <p className="page-subtitle">
        Which teacher teaches which class, in which activity, for how many periods a week.
      </p>

      {!appConfigResult.ok && <p role="alert">{appConfigResult.message}</p>}

      {appConfigResult.ok && pageState.status === "loading" && <p>Loading teaching assignments…</p>}

      {appConfigResult.ok && pageState.status === "error" && (
        <div role="alert" className="page-error">
          <p>{pageState.message}</p>
          <button type="button" onClick={handleRetry}>
            Retry
          </button>
        </div>
      )}

      {appConfigResult.ok && pageState.status === "ready" && (
        <>
          {pageState.projection.configuration_locked && (
            <div className="lock-banner" id="lock-banner-text">
              Assignments are read-only because a schedule has already been generated for this configuration.
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

          {warnings.length > 0 && <WarningBanner warnings={warnings} onDismiss={() => setWarnings([])} />}

          <section className="page-section" aria-labelledby="workload-heading">
            <h2 id="workload-heading">Teacher workload</h2>
            <p className="section-caption">Weekly periods assigned per teacher.</p>
            <WorkloadOverview workloads={pageState.projection.teacher_workloads} />
          </section>

          <section className="page-section" aria-labelledby="assignments-heading">
            <div className="assignments-toolbar">
              <h2 id="assignments-heading">Assignments</h2>
              <button
                type="button"
                className="btn-primary"
                onClick={handleAddClick}
                disabled={pageState.projection.configuration_locked || controlsDisabled}
              >
                Add assignment
              </button>
            </div>
            {refreshState.status === "refreshing" && (
              <p className="refreshing-note" role="status">
                Refreshing…
              </p>
            )}
            {pageState.projection.assignments.length === 0 ? (
              <p>No teaching assignments configured yet.</p>
            ) : (
              <AssignmentsTable
                assignments={pageState.projection.assignments}
                configurationLocked={pageState.projection.configuration_locked}
                wholeClassTargets={pageState.projection.whole_class_targets}
                resources={pageState.projection.resources}
                controlsDisabled={controlsDisabled}
                deleteConfirmId={deleteConfirmId}
                deleteSubmitting={deleteSubmitting}
                deleteError={deleteError}
                onEditClick={handleEditClick}
                onDeleteClick={handleDeleteClick}
                onConfirmDelete={handleConfirmDelete}
                onCancelDelete={handleCancelDelete}
              />
            )}
          </section>

          {drawer !== null && (
            <AssignmentDrawer
              mode={drawer.mode}
              teachers={pageState.projection.teachers}
              activities={pageState.projection.activities}
              wholeClassTargets={pageState.projection.whole_class_targets}
              resources={pageState.projection.resources}
              initialValues={
                drawer.mode === "edit"
                  ? {
                      teacherId: drawer.assignment.teacher_id,
                      participantGroupId: drawer.assignment.participant_group_id,
                      activityId: drawer.assignment.activity_id,
                      weeklyPeriods: drawer.assignment.weekly_periods,
                      resourceId: drawer.assignment.resource_id,
                    }
                  : undefined
              }
              submitting={drawerSubmitting}
              error={drawerError}
              onCancel={closeDrawer}
              onSubmit={handleDrawerSubmit}
            />
          )}
        </>
      )}
    </div>
  );
}

function WarningBanner({ warnings, onDismiss }: { warnings: ValidationDiagnostic[]; onDismiss: () => void }) {
  return (
    <div className="warning-banner" role="status">
      <div className="warning-banner-header">
        <span>Assignment saved, but the current configuration has warnings.</span>
        <button type="button" className="warning-dismiss" aria-label="Dismiss warnings" onClick={onDismiss}>
          ×
        </button>
      </div>
      <ul className="warning-list">
        {warnings.map((warning, index) => (
          <li key={`${warning.code}-${index}`}>
            <span className="warning-code">{WARNING_LABELS[warning.code] ?? warning.code}</span>
            {warning.message !== "" && <span className="warning-message">{warning.message}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A compact, responsive two-column row grid (not a table, not cards) --
 * uses the available horizontal width instead of leaving a narrow
 * left-aligned block; collapses to one column at narrow widths via a
 * plain media query. Every teacher renders, including zero-period
 * ones; no invented target/remaining/capacity presentation. */
function WorkloadOverview({ workloads }: { workloads: TeacherWorkload[] }) {
  return (
    <div className="workload-grid">
      {workloads.map((workload) => (
        <div className="workload-row" key={workload.teacher_id}>
          <span className="workload-row-name">{workload.teacher_name}</span>
          <span className="workload-row-periods">{workload.total_weekly_periods}</span>
        </div>
      ))}
    </div>
  );
}

interface AssignmentsTableProps {
  assignments: TeachingAssignment[];
  configurationLocked: boolean;
  wholeClassTargets: WholeClassTarget[];
  resources: TeachingAssignmentResourceOption[];
  controlsDisabled: boolean;
  deleteConfirmId: string | null;
  deleteSubmitting: boolean;
  deleteError: string | null;
  onEditClick: (event: React.MouseEvent<HTMLButtonElement>, assignment: TeachingAssignment) => void;
  onDeleteClick: (event: React.MouseEvent<HTMLButtonElement>, assignment: TeachingAssignment) => void;
  onConfirmDelete: (assignment: TeachingAssignment) => void;
  onCancelDelete: () => void;
}

function AssignmentsTable({
  assignments,
  configurationLocked,
  wholeClassTargets,
  resources,
  controlsDisabled,
  deleteConfirmId,
  deleteSubmitting,
  deleteError,
  onEditClick,
  onDeleteClick,
  onConfirmDelete,
  onCancelDelete,
}: AssignmentsTableProps) {
  return (
    <div className="table-scroll">
      <table className="assignments-table">
        <colgroup>
          <col className="col-teacher" />
          <col className="col-group" />
          <col className="col-activity" />
          <col className="col-periods" />
          <col className="col-configuration" />
          <col className="col-actions" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">Teacher</th>
            <th scope="col">Class / Group</th>
            <th scope="col">Activity</th>
            <th scope="col" className="numeric-cell">
              Weekly periods
            </th>
            <th scope="col">Configuration</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          {assignments.map((assignment) => (
            <tr key={assignment.id}>
              <th scope="row">{assignment.teacher_name}</th>
              <td>
                <GroupCell assignment={assignment} />
              </td>
              <td>
                <ActivityCell assignment={assignment} resources={resources} />
              </td>
              <td className="numeric-cell">{assignment.weekly_periods}</td>
              <td>
                <StatusCell assignment={assignment} />
              </td>
              <td>
                <ActionsCell
                  assignment={assignment}
                  configurationLocked={configurationLocked}
                  wholeClassTargets={wholeClassTargets}
                  resources={resources}
                  controlsDisabled={controlsDisabled}
                  deleteConfirmId={deleteConfirmId}
                  deleteSubmitting={deleteSubmitting}
                  deleteError={deleteError}
                  onEditClick={onEditClick}
                  onDeleteClick={onDeleteClick}
                  onConfirmDelete={onConfirmDelete}
                  onCancelDelete={onCancelDelete}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Resources B1: resolves an assignment's `resource_id` against the
 * authoritative `resources` option list -- never guessed, never a raw
 * ID shown to the user. A `resource_id` present on the assignment but
 * absent from `resources` is a genuine data inconsistency (e.g. a
 * Resource deleted by another admin session since this page loaded),
 * safely rendered as "Unknown resource" rather than crashing. */
function resourceLabel(resourceId: string | null, resources: TeachingAssignmentResourceOption[]): string {
  if (resourceId === null) {
    return "No resource";
  }
  const match = resources.find((resource) => resource.id === resourceId);
  return match !== undefined ? match.name : "Unknown resource";
}

function resourceResolvable(resourceId: string | null, resources: TeachingAssignmentResourceOption[]): boolean {
  return resourceId === null || resources.some((resource) => resource.id === resourceId);
}

function ActivityCell({
  assignment,
  resources,
}: {
  assignment: TeachingAssignment;
  resources: TeachingAssignmentResourceOption[];
}) {
  return (
    <>
      <span>{assignment.activity_name}</span>
      <span className="resource-subtext">{resourceLabel(assignment.resource_id, resources)}</span>
    </>
  );
}

function GroupCell({ assignment }: { assignment: TeachingAssignment }) {
  const soleClassSection = assignment.class_sections.length === 1 ? assignment.class_sections[0] : undefined;
  if (assignment.participant_group_role === "WHOLE_CLASS" && soleClassSection !== undefined) {
    return <span>{soleClassSection.name}</span>;
  }
  const roleLabel = GROUP_ROLE_LABELS[assignment.participant_group_role] ?? assignment.participant_group_role;
  return (
    <>
      <span>{assignment.participant_group_name}</span>
      <span className="group-role-badge">{roleLabel}</span>
    </>
  );
}

function StatusCell({ assignment }: { assignment: TeachingAssignment }) {
  if (assignment.editable) {
    return <span className="status-standard">Standard</span>;
  }
  return (
    <>
      <span className="status-advanced">Advanced</span>
      <div className="reason-chips">
        {assignment.advanced_reasons.map((reason) => (
          <span className="reason-chip" key={reason}>
            {friendlyReason(reason)}
          </span>
        ))}
      </div>
    </>
  );
}

interface ActionsCellProps {
  assignment: TeachingAssignment;
  configurationLocked: boolean;
  wholeClassTargets: WholeClassTarget[];
  resources: TeachingAssignmentResourceOption[];
  controlsDisabled: boolean;
  deleteConfirmId: string | null;
  deleteSubmitting: boolean;
  deleteError: string | null;
  onEditClick: (event: React.MouseEvent<HTMLButtonElement>, assignment: TeachingAssignment) => void;
  onDeleteClick: (event: React.MouseEvent<HTMLButtonElement>, assignment: TeachingAssignment) => void;
  onConfirmDelete: (assignment: TeachingAssignment) => void;
  onCancelDelete: () => void;
}

/** Deliberately keeps two disabled-looking states visually and
 * semantically distinct (`docs/PROJECT_STATE.md`'s Phase 3C.3b note):
 * a row that is globally locked (`configurationLocked`, explained by
 * the one page-level `.lock-banner`) vs. a row that is individually
 * "Advanced" (`!assignment.editable`, explained by its own status
 * badge/reason chips in the Configuration column, unrelated to the
 * lock). Neither ever borrows the other's explanation. */
function ActionsCell({
  assignment,
  configurationLocked,
  wholeClassTargets,
  resources,
  controlsDisabled,
  deleteConfirmId,
  deleteSubmitting,
  deleteError,
  onEditClick,
  onDeleteClick,
  onConfirmDelete,
  onCancelDelete,
}: ActionsCellProps) {
  if (!assignment.editable) {
    return <span className="action-readonly">Read-only</span>;
  }

  if (configurationLocked) {
    return (
      <span className="actions-group">
        <button type="button" className="action-button" disabled aria-describedby="lock-banner-text">
          Edit
        </button>
        <button type="button" className="action-button action-button-delete" disabled aria-describedby="lock-banner-text">
          Delete
        </button>
      </span>
    );
  }

  if (deleteConfirmId === assignment.id) {
    return (
      <span className="delete-confirm">
        <span className="delete-confirm-text">
          Delete {assignment.teacher_name} · {assignment.participant_group_name} · {assignment.activity_name} (
          {assignment.weekly_periods}/wk)?
        </span>
        {deleteError !== null && (
          <p role="alert" className="delete-confirm-error">
            {deleteError}
          </p>
        )}
        <span className="delete-confirm-actions">
          <button type="button" onClick={() => onConfirmDelete(assignment)} disabled={deleteSubmitting}>
            {deleteSubmitting ? "Deleting…" : "Confirm delete"}
          </button>
          <button type="button" onClick={onCancelDelete} disabled={deleteSubmitting}>
            Cancel
          </button>
        </span>
      </span>
    );
  }

  const matchingTarget = wholeClassTargets.find(
    (target) => target.participant_group_id === assignment.participant_group_id,
  );
  // Resources B1: mirrors the `matchingTarget` orphan guard above -- an
  // assignment whose `resource_id` cannot be resolved against the
  // current `resources` option list can't be safely preselected in the
  // edit form either (silently defaulting to "No resource" would risk
  // discarding a real, just-not-yet-visible Resource on save), so Edit
  // is blocked the same way. Delete never depends on either mapping.
  const canEdit = matchingTarget !== undefined && resourceResolvable(assignment.resource_id, resources);

  return (
    <span className="actions-group">
      {canEdit ? (
        <button
          type="button"
          className="action-button"
          onClick={(event) => onEditClick(event, assignment)}
          disabled={controlsDisabled}
        >
          Edit
        </button>
      ) : (
        <span className="action-readonly">This assignment can no longer be edited here.</span>
      )}
      <button
        type="button"
        className="action-button action-button-delete"
        onClick={(event) => onDeleteClick(event, assignment)}
        disabled={controlsDisabled}
      >
        Delete
      </button>
    </span>
  );
}

export default TeachingAssignmentsPage;
