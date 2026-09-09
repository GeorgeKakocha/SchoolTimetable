import { useCallback, useEffect, useState } from "react";
import { createTeacher, deleteTeacher, getTeachers, updateTeacher } from "../../api/teachers";
import { ApiError } from "../../api/client";
import type { TeacherProjectionItem, TeachersProjectionResponse, TeacherWriteRequest } from "../../api/types";
import { AppConfigError, loadAppConfig } from "../../config/appConfig";

/**
 * Real-School Setup MVP Slice E2: the Teachers tab of `SchoolSetupPage`.
 * Self-contained, matching `TeachingAssignmentsPage`'s own architecture
 * exactly -- its own load/create/edit/delete state, its own GET
 * projection as sole source of truth, refetched (never hand-patched)
 * after every successful write (design gate #21/#26). No duplicate-name
 * rule exists on this resource (Owner Decision #37/Slice B) -- this
 * panel never adds one client-side.
 */

type PanelState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; projection: TeachersProjectionResponse };

type AppConfigResult =
  | { ok: true; schoolId: string; academicYearId: string }
  | { ok: false; message: string };

type RefreshState = { status: "idle" } | { status: "refreshing" } | { status: "stale"; message: string };

const REFRESH_FAILURE_MESSAGE =
  "The change was saved, but the latest teacher list could not be refreshed. Retry to load the current data.";
const LOCK_RACE_MESSAGE =
  "A schedule was generated since this page loaded. Teachers are now read-only.";

/** `teacher_rules.referenced_by` (backend): the deterministic kind
 * codes a `TEACHER_IN_USE` error can carry, mapped to a friendly label
 * -- purely presentational. An unrecognized future code falls back to
 * its raw form rather than breaking render. */
const REFERENCED_BY_LABELS: Record<string, string> = {
  TEACHING_REQUIREMENT: "Teaching assignments",
  TEACHER_AVAILABILITY: "Teacher availability",
  RESERVED_BLOCK: "Reserved activities",
  SUBGROUP: "Subgroups",
  MERGED_CLASSES: "Merged classes",
};

function friendlyReferencedBy(code: string): string {
  return REFERENCED_BY_LABELS[code] ?? code;
}

function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  return "Something went wrong. Please try again.";
}

/** Maps a mutation's structured `ApiError` to a safe, concise inline
 * message. `SCHEDULING_CONFIGURATION_LOCKED` (stale-lock race) and a
 * 404 (stale client) are handled one level up, before this is called. */
function describeMutationError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Something went wrong. Please try again.";
  }
  switch (error.code) {
    case "TEACHER_IN_USE": {
      const rawReferencedBy = error.body?.["referenced_by"];
      if (Array.isArray(rawReferencedBy) && rawReferencedBy.length > 0) {
        const labels = rawReferencedBy
          .filter((item): item is string => typeof item === "string")
          .map(friendlyReferencedBy);
        return `This teacher can't be deleted because it is used by: ${labels.join(", ")}.`;
      }
      return error.detail;
    }
    case "INVALID_TEACHER": {
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

type EditState = { teacherId: string; firstName: string; lastName: string };

function isBlank(value: string): boolean {
  return value.trim() === "";
}

function TeachersPanel() {
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

  const [createOpen, setCreateOpen] = useState(false);
  const [createFirstName, setCreateFirstName] = useState("");
  const [createLastName, setCreateLastName] = useState("");
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [editState, setEditState] = useState<EditState | null>(null);
  const [editSubmitting, setEditSubmitting] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    if (!appConfigResult.ok) {
      return;
    }
    const controller = new AbortController();
    setPanelState({ status: "loading" });

    getTeachers(appConfigResult.schoolId, appConfigResult.academicYearId, controller.signal)
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
        const projection = await getTeachers(appConfigResult.schoolId, appConfigResult.academicYearId);
        setPanelState({ status: "ready", projection });
        setRefreshState({ status: "idle" });
      } catch {
        setRefreshState({ status: "stale", message: staleFailureMessage });
      }
    },
    [appConfigResult],
  );

  const performWrite = useCallback(
    async (
      action: () => Promise<unknown>,
      onInlineError: (message: string) => void,
    ): Promise<WriteOutcome> => {
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
        onInlineError(describeMutationError(error));
        return { ok: false, lockRace: false, notFound: false };
      }
    },
    [],
  );

  const handleOpenCreate = useCallback(() => {
    setCreateOpen(true);
    setCreateFirstName("");
    setCreateLastName("");
    setCreateError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelCreate = useCallback(() => {
    setCreateOpen(false);
    setCreateFirstName("");
    setCreateLastName("");
    setCreateError(null);
  }, []);

  const handleSubmitCreate = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!appConfigResult.ok || createSubmitting) {
        return;
      }
      if (isBlank(createFirstName) || isBlank(createLastName)) {
        return;
      }
      setCreateSubmitting(true);
      setCreateError(null);
      const body: TeacherWriteRequest = { first_name: createFirstName.trim(), last_name: createLastName.trim() };
      const outcome = await performWrite(
        () => createTeacher(appConfigResult.schoolId, appConfigResult.academicYearId, body),
        setCreateError,
      );
      setCreateSubmitting(false);

      if (outcome.ok || outcome.lockRace) {
        setCreateOpen(false);
        setCreateFirstName("");
        setCreateLastName("");
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [appConfigResult, createSubmitting, createFirstName, createLastName, performWrite, refreshProjection],
  );

  const handleStartEdit = useCallback((teacher: TeacherProjectionItem) => {
    setEditState({ teacherId: teacher.id, firstName: teacher.first_name, lastName: teacher.last_name });
    setEditError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelEdit = useCallback(() => {
    setEditState(null);
    setEditError(null);
  }, []);

  const handleSubmitEdit = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!appConfigResult.ok || editState === null || editSubmitting) {
        return;
      }
      if (isBlank(editState.firstName) || isBlank(editState.lastName)) {
        return;
      }
      setEditSubmitting(true);
      setEditError(null);
      const body: TeacherWriteRequest = { first_name: editState.firstName.trim(), last_name: editState.lastName.trim() };
      const outcome = await performWrite(
        () => updateTeacher(appConfigResult.schoolId, appConfigResult.academicYearId, editState.teacherId, body),
        setEditError,
      );
      setEditSubmitting(false);

      if (outcome.ok || outcome.lockRace || outcome.notFound) {
        setEditState(null);
      }
      if (outcome.ok || outcome.lockRace) {
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [appConfigResult, editState, editSubmitting, performWrite, refreshProjection],
  );

  const handleDeleteClick = useCallback((teacherId: string) => {
    setDeleteConfirmId(teacherId);
    setDeleteError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelDelete = useCallback(() => {
    setDeleteConfirmId(null);
    setDeleteError(null);
  }, []);

  const handleConfirmDelete = useCallback(
    async (teacherId: string) => {
      if (!appConfigResult.ok) {
        return;
      }
      setDeleteSubmitting(true);
      setDeleteError(null);

      const outcome = await performWrite(
        () => deleteTeacher(appConfigResult.schoolId, appConfigResult.academicYearId, teacherId),
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

  const controlsDisabled =
    refreshState.status !== "idle" || createSubmitting || editSubmitting || deleteSubmitting;

  if (!appConfigResult.ok) {
    return <p role="alert">{appConfigResult.message}</p>;
  }

  if (panelState.status === "loading") {
    return <p>Loading teachers…</p>;
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
  const createDisabled = locked || controlsDisabled || createOpen || editState !== null || deleteConfirmId !== null;

  return (
    <div className="setup-panel">
      {locked && (
        <div className="lock-banner" id="teachers-lock-banner-text">
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

      {projection.teachers.length > 0 && (
        <div className="setup-toolbar">
          <button type="button" className="btn-primary" onClick={handleOpenCreate} disabled={createDisabled}>
            + Add teacher
          </button>
        </div>
      )}

      {refreshState.status === "refreshing" && (
        <p className="refreshing-note" role="status">
          Refreshing…
        </p>
      )}

      {createOpen && (
        <form className="create-panel" onSubmit={handleSubmitCreate}>
          <h3 className="create-panel-heading">Add teacher</h3>
          <label>
            First name
            <input
              type="text"
              value={createFirstName}
              onChange={(event) => setCreateFirstName(event.target.value)}
              disabled={createSubmitting}
              autoFocus
              required
            />
          </label>
          <label>
            Last name
            <input
              type="text"
              value={createLastName}
              onChange={(event) => setCreateLastName(event.target.value)}
              disabled={createSubmitting}
              required
            />
          </label>
          {createError !== null && (
            <p role="alert" className="create-panel-error">
              {createError}
            </p>
          )}
          <div className="create-panel-actions">
            <button type="button" onClick={handleCancelCreate} disabled={createSubmitting}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={createSubmitting || isBlank(createFirstName) || isBlank(createLastName)}
            >
              {createSubmitting ? "Saving…" : "Save"}
            </button>
          </div>
        </form>
      )}

      {projection.teachers.length === 0 ? (
        <div className="setup-empty-state">
          <p>No teachers yet.</p>
          <p>Add the teachers who will appear in the timetable.</p>
          {!createOpen && (
            <button type="button" className="btn-primary" onClick={handleOpenCreate} disabled={createDisabled}>
              + Add teacher
            </button>
          )}
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
                <th scope="col">Teacher</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {projection.teachers.map((teacher) => (
                <tr key={teacher.id}>
                  {editState !== null && editState.teacherId === teacher.id ? (
                    <>
                      <td>
                        <form className="row-edit-form" onSubmit={handleSubmitEdit}>
                          <label>
                            First name
                            <input
                              type="text"
                              value={editState.firstName}
                              onChange={(event) =>
                                setEditState((current) =>
                                  current === null ? current : { ...current, firstName: event.target.value },
                                )
                              }
                              disabled={editSubmitting}
                              autoFocus
                              required
                            />
                          </label>
                          <label>
                            Last name
                            <input
                              type="text"
                              value={editState.lastName}
                              onChange={(event) =>
                                setEditState((current) =>
                                  current === null ? current : { ...current, lastName: event.target.value },
                                )
                              }
                              disabled={editSubmitting}
                              required
                            />
                          </label>
                          {editError !== null && (
                            <p role="alert" className="row-edit-error">
                              {editError}
                            </p>
                          )}
                          <span className="row-edit-actions">
                            <button
                              type="submit"
                              className="btn-primary"
                              disabled={editSubmitting || isBlank(editState.firstName) || isBlank(editState.lastName)}
                            >
                              {editSubmitting ? "Saving…" : "Save"}
                            </button>
                            <button type="button" onClick={handleCancelEdit} disabled={editSubmitting}>
                              Cancel
                            </button>
                          </span>
                        </form>
                      </td>
                      <td />
                    </>
                  ) : (
                    <>
                      <th scope="row" data-label="Teacher">
                        {teacher.name}
                      </th>
                      <td data-label="Actions">
                        {deleteConfirmId === teacher.id ? (
                          <span className="delete-confirm">
                            <span className="delete-confirm-text">Delete {teacher.name}?</span>
                            {deleteError !== null && (
                              <p role="alert" className="delete-confirm-error">
                                {deleteError}
                              </p>
                            )}
                            <span className="delete-confirm-actions">
                              <button
                                type="button"
                                onClick={() => handleConfirmDelete(teacher.id)}
                                disabled={deleteSubmitting}
                              >
                                {deleteSubmitting ? "Deleting…" : "Confirm delete"}
                              </button>
                              <button type="button" onClick={handleCancelDelete} disabled={deleteSubmitting}>
                                Cancel
                              </button>
                            </span>
                          </span>
                        ) : locked ? (
                          <span className="actions-group">
                            <button type="button" className="action-button" disabled aria-describedby="teachers-lock-banner-text">
                              Edit
                            </button>
                            <button
                              type="button"
                              className="action-button action-button-delete"
                              disabled
                              aria-describedby="teachers-lock-banner-text"
                            >
                              Delete
                            </button>
                          </span>
                        ) : (
                          <span className="actions-group">
                            <button
                              type="button"
                              className="action-button"
                              onClick={() => handleStartEdit(teacher)}
                              disabled={controlsDisabled || createOpen || editState !== null || deleteConfirmId !== null}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="action-button action-button-delete"
                              onClick={() => handleDeleteClick(teacher.id)}
                              disabled={controlsDisabled || createOpen || editState !== null || deleteConfirmId !== null}
                            >
                              Delete
                            </button>
                          </span>
                        )}
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default TeachersPanel;
