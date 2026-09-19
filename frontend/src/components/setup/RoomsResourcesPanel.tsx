import { useCallback, useEffect, useMemo, useState } from "react";
import { createResource, deleteResource, getResources, updateResource } from "../../api/resources";
import type { ResourceItem, ResourcesProjectionResponse, ResourceWriteRequest } from "../../api/resources";
import { ApiError } from "../../api/client";
import { useActiveSchoolYearContext } from "../../context/ActiveSchoolYearContext";

/**
 * Resources C: the "Rooms & Resources" tab of `SchoolSetupPage`.
 * Self-contained, matching `SpecialActivitiesPanel`'s architecture
 * exactly -- "Rooms & Resources" is the user-facing label for the
 * existing `Resource(id, name, capacity)` entity; this component never
 * renders a natural ID, ordinal, or database surrogate ID, and never
 * uses solver/technical vocabulary for `capacity` ("maximum
 * simultaneous uses", never seat/headcount capacity).
 */

type PanelState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; projection: ResourcesProjectionResponse };

type AppConfigResult =
  | { ok: true; schoolId: string; academicYearId: string }
  | { ok: false; message: string };

type RefreshState = { status: "idle" } | { status: "refreshing" } | { status: "stale"; message: string };

const REFRESH_FAILURE_MESSAGE =
  "The change was saved, but the latest rooms and resources list could not be refreshed. Retry to load the current data.";
const LOCK_RACE_MESSAGE = "A schedule was generated since this page loaded. Rooms & Resources are now read-only.";
const DEFAULT_CAPACITY_TEXT = "1";

const REFERENCED_BY_LABELS: Record<string, string> = {
  TEACHING_REQUIREMENT: "Teaching Assignments",
  RESERVED_BLOCK: "Reserved Activities",
};

function friendlyReferencedBy(code: string): string {
  return REFERENCED_BY_LABELS[code] ?? code;
}

function isPositiveIntegerText(text: string): boolean {
  return /^[0-9]+$/.test(text) && Number(text) >= 1;
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
    case "DUPLICATE_RESOURCE":
      return "A room or resource with this name already exists.";
    case "RESOURCE_IN_USE": {
      const rawReferencedBy = error.body?.["referenced_by"];
      if (Array.isArray(rawReferencedBy) && rawReferencedBy.length > 0) {
        const labels = rawReferencedBy
          .filter((item): item is string => typeof item === "string")
          .map(friendlyReferencedBy);
        return `This resource can't be deleted because it is used by: ${labels.join(", ")}.`;
      }
      return error.detail;
    }
    case "INVALID_RESOURCE": {
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

type EditState = { resourceId: string; name: string; capacityText: string };

function isBlank(value: string): boolean {
  return value.trim() === "";
}

function RoomsResourcesPanel() {
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
  const [createCapacityText, setCreateCapacityText] = useState(DEFAULT_CAPACITY_TEXT);
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

    getResources(appConfigResult.schoolId, appConfigResult.academicYearId, controller.signal)
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
        const projection = await getResources(appConfigResult.schoolId, appConfigResult.academicYearId);
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
    setCreateCapacityText(DEFAULT_CAPACITY_TEXT);
    setCreateError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelCreate = useCallback(() => {
    setCreateOpen(false);
    setCreateName("");
    setCreateCapacityText(DEFAULT_CAPACITY_TEXT);
    setCreateError(null);
  }, []);

  const createValid = !isBlank(createName) && isPositiveIntegerText(createCapacityText);

  const handleSubmitCreate = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!appConfigResult.ok || createSubmitting || !createValid) {
        return;
      }
      setCreateSubmitting(true);
      setCreateError(null);
      const body: ResourceWriteRequest = { name: createName.trim(), capacity: Number(createCapacityText) };
      const outcome = await performWrite(
        () => createResource(appConfigResult.schoolId, appConfigResult.academicYearId, body),
        setCreateError,
      );
      setCreateSubmitting(false);

      if (outcome.ok || outcome.lockRace) {
        setCreateOpen(false);
        setCreateName("");
        setCreateCapacityText(DEFAULT_CAPACITY_TEXT);
        await refreshProjection(REFRESH_FAILURE_MESSAGE);
      }
    },
    [appConfigResult, createSubmitting, createValid, createName, createCapacityText, performWrite, refreshProjection],
  );

  const handleStartEdit = useCallback((resource: ResourceItem) => {
    setEditState({ resourceId: resource.id, name: resource.name, capacityText: String(resource.capacity) });
    setEditError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelEdit = useCallback(() => {
    setEditState(null);
    setEditError(null);
  }, []);

  const editValid = editState !== null && !isBlank(editState.name) && isPositiveIntegerText(editState.capacityText);

  const handleSubmitEdit = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!appConfigResult.ok || editState === null || editSubmitting || !editValid) {
        return;
      }
      setEditSubmitting(true);
      setEditError(null);
      const body: ResourceWriteRequest = { name: editState.name.trim(), capacity: Number(editState.capacityText) };
      const outcome = await performWrite(
        () => updateResource(appConfigResult.schoolId, appConfigResult.academicYearId, editState.resourceId, body),
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
    [appConfigResult, editState, editSubmitting, editValid, performWrite, refreshProjection],
  );

  const handleDeleteClick = useCallback((resourceId: string) => {
    setDeleteConfirmId(resourceId);
    setDeleteError(null);
    setStaleLockNotice(null);
  }, []);

  const handleCancelDelete = useCallback(() => {
    setDeleteConfirmId(null);
    setDeleteError(null);
  }, []);

  const handleConfirmDelete = useCallback(
    async (resourceId: string) => {
      if (!appConfigResult.ok) {
        return;
      }
      setDeleteSubmitting(true);
      setDeleteError(null);

      const outcome = await performWrite(
        () => deleteResource(appConfigResult.schoolId, appConfigResult.academicYearId, resourceId),
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
    return <p>Loading rooms and resources…</p>;
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
        Manage the rooms, equipment, and other shared resources that lessons and activities can be tied to.
      </p>

      {locked && (
        <div className="lock-banner" id="rooms-resources-lock-banner-text">
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

      {projection.resources.length > 0 && (
        <div className="setup-toolbar">
          <button type="button" className="btn-primary" onClick={handleOpenCreate} disabled={createDisabled}>
            + Add room or resource
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
          <h3 className="create-panel-heading">Add room or resource</h3>
          <label>
            Name
            <input
              type="text"
              value={createName}
              onChange={(event) => setCreateName(event.target.value)}
              disabled={createSubmitting}
              autoFocus
              required
            />
          </label>
          <label>
            Capacity
            <input
              type="number"
              min={1}
              step={1}
              value={createCapacityText}
              onChange={(event) => setCreateCapacityText(event.target.value)}
              disabled={createSubmitting}
              required
            />
          </label>
          <p className="capacity-hint">
            Capacity is the maximum number of simultaneous uses. Capacity 1 means only one lesson or activity may
            use this at a time; capacity 2 allows two at once, and so on.
          </p>
          {createError !== null && (
            <p role="alert" className="create-panel-error">
              {createError}
            </p>
          )}
          <div className="create-panel-actions">
            <button type="button" onClick={handleCancelCreate} disabled={createSubmitting}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={createSubmitting || !createValid}>
              {createSubmitting ? "Saving…" : "Save"}
            </button>
          </div>
        </form>
      )}

      {projection.resources.length === 0 ? (
        <div className="setup-empty-state">
          <p>No Rooms & Resources yet.</p>
          <p>Add the rooms, equipment, and other shared resources this school uses.</p>
          {!createOpen && (
            <button type="button" className="btn-primary" onClick={handleOpenCreate} disabled={createDisabled}>
              + Add room or resource
            </button>
          )}
        </div>
      ) : (
        <div className="table-scroll">
          <table className="setup-table">
            <colgroup>
              <col className="col-setup-name" />
              <col className="col-setup-capacity" />
              <col className="col-setup-actions" />
            </colgroup>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Capacity</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {projection.resources.map((resource) => (
                <tr key={resource.id}>
                  {editState !== null && editState.resourceId === resource.id ? (
                    <>
                      <td colSpan={2}>
                        <form className="row-edit-form" onSubmit={handleSubmitEdit}>
                          <label>
                            Name
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
                          <label>
                            Capacity
                            <input
                              type="number"
                              min={1}
                              step={1}
                              value={editState.capacityText}
                              onChange={(event) =>
                                setEditState((current) =>
                                  current === null ? current : { ...current, capacityText: event.target.value },
                                )
                              }
                              disabled={editSubmitting}
                              required
                            />
                          </label>
                          <p className="capacity-hint">Maximum number of simultaneous uses.</p>
                          {editError !== null && (
                            <p role="alert" className="row-edit-error">
                              {editError}
                            </p>
                          )}
                          <span className="row-edit-actions">
                            <button type="submit" className="btn-primary" disabled={editSubmitting || !editValid}>
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
                      <th scope="row" data-label="Name">
                        {resource.name}
                      </th>
                      <td data-label="Capacity">{resource.capacity}</td>
                      <td data-label="Actions">
                        {deleteConfirmId === resource.id ? (
                          <span className="delete-confirm">
                            <span className="delete-confirm-text">Delete {resource.name}?</span>
                            {deleteError !== null && (
                              <p role="alert" className="delete-confirm-error">
                                {deleteError}
                              </p>
                            )}
                            <span className="delete-confirm-actions">
                              <button
                                type="button"
                                onClick={() => handleConfirmDelete(resource.id)}
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
                              aria-describedby="rooms-resources-lock-banner-text"
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="action-button action-button-delete"
                              disabled
                              aria-describedby="rooms-resources-lock-banner-text"
                            >
                              Delete
                            </button>
                          </span>
                        ) : (
                          <span className="actions-group">
                            <button
                              type="button"
                              className="action-button"
                              onClick={() => handleStartEdit(resource)}
                              disabled={controlsDisabled || createOpen || editState !== null || deleteConfirmId !== null}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="action-button action-button-delete"
                              onClick={() => handleDeleteClick(resource.id)}
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

export default RoomsResourcesPanel;
