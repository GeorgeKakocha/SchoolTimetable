import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createSpecialActivity,
  deleteSpecialActivity,
  getSpecialActivities,
  updateSpecialActivity,
} from "../../api/specialActivities";
import type { SpecialActivitiesProjectionResponse, SpecialActivityItem, SpecialActivityWriteRequest } from "../../api/specialActivities";
import { ApiError } from "../../api/client";
import { useActiveSchoolYearContext } from "../../context/ActiveSchoolYearContext";

/**
 * Reserved Activities Slice A1/Reserved B: the Special Activities tab
 * of `SchoolSetupPage`. Self-contained, matching `SubjectsPanel`'s
 * architecture exactly -- "Special Activity" is the user-facing name
 * for `Activity(kind=CLUB)`, and `GET /special-activities` is already
 * a CLUB-only projection with no `kind` field on the wire, so this
 * component never filters by kind itself and never renders any raw
 * `CLUB`/`ORDINARY`/`ReservedBlock` vocabulary.
 */

type PanelState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; projection: SpecialActivitiesProjectionResponse };

type AppConfigResult =
  | { ok: true; schoolId: string; academicYearId: string }
  | { ok: false; message: string };

type RefreshState = { status: "idle" } | { status: "refreshing" } | { status: "stale"; message: string };

const REFRESH_FAILURE_MESSAGE =
  "The change was saved, but the latest special activity list could not be refreshed. Retry to load the current data.";
const LOCK_RACE_MESSAGE = "A schedule was generated since this page loaded. Special activities are now read-only.";

const REFERENCED_BY_LABELS: Record<string, string> = {
  RESERVED_BLOCK: "Reserved activities",
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

function describeMutationError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Something went wrong. Please try again.";
  }
  switch (error.code) {
    case "DUPLICATE_SPECIAL_ACTIVITY":
      return "A special activity with this name already exists.";
    case "SPECIAL_ACTIVITY_IN_USE": {
      const rawReferencedBy = error.body?.["referenced_by"];
      if (Array.isArray(rawReferencedBy) && rawReferencedBy.length > 0) {
        const labels = rawReferencedBy
          .filter((item): item is string => typeof item === "string")
          .map(friendlyReferencedBy);
        return `This special activity can't be deleted because it is used by: ${labels.join(", ")}.`;
      }
      return error.detail;
    }
    case "INVALID_SPECIAL_ACTIVITY": {
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

type EditState = { specialActivityId: string; name: string };

function isBlank(value: string): boolean {
  return value.trim() === "";
}

function SpecialActivitiesPanel() {
  const { activeContext } = useActiveSchoolYearContext();
  const appConfigResult = useMemo<AppConfigResult>(
    () => activeContext === null
      ? { ok: false, message: "No active school and academic year selected." }
      : { ok: true, schoolId: activeContext.schoolId, academicYearId: activeContext.academicYearId },
    [activeContext],
  );

  const [panelState, setPanelState] = useState<PanelState>({ status: "loading" });
  const [retryToken, setRetryToken] = useState(0);
  const [refreshState, setRefreshState] = useState<RefreshState>({ status: "idle" });
  const [staleLockNotice, setStaleLockNotice] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
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

    getSpecialActivities(appConfigResult.schoolId, appConfigResult.academicYearId, controller.signal)
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
        const projection = await getSpecialActivities(appConfigResult.schoolId, appConfigResult.academicYearId);
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
    setCreateName("");
    setCreateError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelCreate = useCallback(() => {
    setCreateOpen(false);
    setCreateName("");
    setCreateError(null);
  }, []);

  const handleSubmitCreate = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!appConfigResult.ok || createSubmitting || isBlank(createName)) {
        return;
      }
      setCreateSubmitting(true);
      setCreateError(null);
      const body: SpecialActivityWriteRequest = { name: createName.trim() };
      const outcome = await performWrite(
        () => createSpecialActivity(appConfigResult.schoolId, appConfigResult.academicYearId, body),
        setCreateError,
      );
      setCreateSubmitting(false);

      if (outcome.ok || outcome.lockRace) {
        setCreateOpen(false);
        setCreateName("");
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [appConfigResult, createSubmitting, createName, performWrite, refreshProjection],
  );

  const handleStartEdit = useCallback((specialActivity: SpecialActivityItem) => {
    setEditState({ specialActivityId: specialActivity.id, name: specialActivity.name });
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
      if (!appConfigResult.ok || editState === null || editSubmitting || isBlank(editState.name)) {
        return;
      }
      setEditSubmitting(true);
      setEditError(null);
      const body: SpecialActivityWriteRequest = { name: editState.name.trim() };
      const outcome = await performWrite(
        () =>
          updateSpecialActivity(
            appConfigResult.schoolId,
            appConfigResult.academicYearId,
            editState.specialActivityId,
            body,
          ),
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

  const handleDeleteClick = useCallback((specialActivityId: string) => {
    setDeleteConfirmId(specialActivityId);
    setDeleteError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelDelete = useCallback(() => {
    setDeleteConfirmId(null);
    setDeleteError(null);
  }, []);

  const handleConfirmDelete = useCallback(
    async (specialActivityId: string) => {
      if (!appConfigResult.ok) {
        return;
      }
      setDeleteSubmitting(true);
      setDeleteError(null);

      const outcome = await performWrite(
        () => deleteSpecialActivity(appConfigResult.schoolId, appConfigResult.academicYearId, specialActivityId),
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
    return <p>Loading special activities…</p>;
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
      <p className="page-subtitle">
        Create school activities that can later be placed at fixed times in Reserved Activities.
      </p>

      {locked && (
        <div className="lock-banner" id="special-activities-lock-banner-text">
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

      {projection.special_activities.length > 0 && (
        <div className="setup-toolbar">
          <button type="button" className="btn-primary" onClick={handleOpenCreate} disabled={createDisabled}>
            + Add special activity
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
          <h3 className="create-panel-heading">Add special activity</h3>
          <label>
            Special activity name
            <input
              type="text"
              value={createName}
              onChange={(event) => setCreateName(event.target.value)}
              disabled={createSubmitting}
              autoFocus
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
            <button type="submit" className="btn-primary" disabled={createSubmitting || isBlank(createName)}>
              {createSubmitting ? "Saving…" : "Save"}
            </button>
          </div>
        </form>
      )}

      {projection.special_activities.length === 0 ? (
        <div className="setup-empty-state">
          <p>No Special Activities yet.</p>
          <p>Add the special activities offered by this school.</p>
          {!createOpen && (
            <button type="button" className="btn-primary" onClick={handleOpenCreate} disabled={createDisabled}>
              + Add special activity
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
                <th scope="col">Special activity</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {projection.special_activities.map((specialActivity) => (
                <tr key={specialActivity.id}>
                  {editState !== null && editState.specialActivityId === specialActivity.id ? (
                    <>
                      <td>
                        <form className="row-edit-form" onSubmit={handleSubmitEdit}>
                          <label>
                            Special activity name
                            <input
                              type="text"
                              value={editState.name}
                              onChange={(event) =>
                                setEditState((current) => (current === null ? current : { ...current, name: event.target.value }))
                              }
                              disabled={editSubmitting}
                              autoFocus
                              required
                            />
                          </label>
                          {editError !== null && (
                            <p role="alert" className="row-edit-error">
                              {editError}
                            </p>
                          )}
                          <span className="row-edit-actions">
                            <button type="submit" className="btn-primary" disabled={editSubmitting || isBlank(editState.name)}>
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
                      <th scope="row" data-label="Special activity">
                        {specialActivity.name}
                      </th>
                      <td data-label="Actions">
                        {deleteConfirmId === specialActivity.id ? (
                          <span className="delete-confirm">
                            <span className="delete-confirm-text">Delete {specialActivity.name}?</span>
                            {deleteError !== null && (
                              <p role="alert" className="delete-confirm-error">
                                {deleteError}
                              </p>
                            )}
                            <span className="delete-confirm-actions">
                              <button
                                type="button"
                                onClick={() => handleConfirmDelete(specialActivity.id)}
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
                            <button
                              type="button"
                              className="action-button"
                              disabled
                              aria-describedby="special-activities-lock-banner-text"
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="action-button action-button-delete"
                              disabled
                              aria-describedby="special-activities-lock-banner-text"
                            >
                              Delete
                            </button>
                          </span>
                        ) : (
                          <span className="actions-group">
                            <button
                              type="button"
                              className="action-button"
                              onClick={() => handleStartEdit(specialActivity)}
                              disabled={controlsDisabled || createOpen || editState !== null || deleteConfirmId !== null}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="action-button action-button-delete"
                              onClick={() => handleDeleteClick(specialActivity.id)}
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

export default SpecialActivitiesPanel;
