import { useEffect, useState } from "react";
import TimetableGrid from "./TimetableGrid";
import type { PreviewTargetState } from "./TimetableGrid";
import {
  getActiveSchedule,
  lockOccurrence,
  moveScheduleEntry,
  previewMove,
  reoptimizeSchedule,
  unlockOccurrence,
} from "../api/scheduleEditing";
import { ApiError } from "../api/client";
import type { ClassTimetableEntry, ClassTimetableResponse, MoveViolation } from "../api/types";

/**
 * Manual timetable editing MVP (backend slice already shipped): wraps
 * the existing, purely-presentational `TimetableGrid` with the
 * click-based move/lock/unlock/re-optimize workflow, self-contained
 * exactly like `RoomsResourcesPanel`/`CalendarBellSchedulePanel` --
 * `TimetablePage` only supplies the current authoritative
 * `ClassTimetableResponse` and a callback to trigger its own re-fetch
 * after a successful mutation (the same `generationRefreshToken` bump
 * the existing Generate flow already uses -- no second display path,
 * no full-page reload).
 *
 * Click-based, never drag-and-drop (Owner decision, this slice): safer
 * for a swap-based backend contract, keyboard-accessible, works at
 * narrow widths, and gives room for a real confirmation step and actual
 * conflict reasons -- see `docs/SCHEDULE_EDITING.md` for why a manual
 * move is a validated swap, never a plain relocation.
 *
 * Move is available from this (Class) view only -- never from the
 * Teacher view, which stays read-only in this slice: the class grid is
 * the one place a source AND destination slot are both unambiguous
 * (Decision, this slice).
 *
 * Lock-state badges are read from a SEPARATE `GET .../schedule/active`
 * call (`locked_occurrences`), re-fetched whenever the class
 * projection's own `version_number` changes -- seeing `TimetableGrid`'s
 * own doc comment for the one known display-fidelity limitation this
 * carries (a locked multi-period REQUIRED/PREFERRED-double block's
 * badge only ever appears on its anchor period).
 */

interface ClassTimetableEditorProps {
  schoolId: string;
  academicYearId: string;
  timetable: ClassTimetableResponse;
  onMutationSuccess: () => void;
}

type Selection = {
  requirementId: string;
  activityName: string;
  teacherName: string | null;
  groupName: string | null;
  dayId: string;
  dayName: string;
  periodId: string;
  periodName: string;
  locked: boolean;
};

type Mode = "view" | "choosing-target" | "confirming-move";

function dayName(timetable: ClassTimetableResponse, dayId: string): string {
  return timetable.days.find((d) => d.id === dayId)?.name ?? dayId;
}

function periodName(timetable: ClassTimetableResponse, periodId: string): string {
  return timetable.rows.find((r) => r.period_id === periodId)?.period_name ?? periodId;
}

/** Every structured editing-command error this panel must distinguish,
 * mapped to safe, human-readable text -- never a raw JSON dump, never a
 * generic "Something went wrong" in place of a specific backend reason
 * the caller already supplied. */
function describeEditingError(error: unknown): { message: string; details: string[] } {
  if (!(error instanceof ApiError)) {
    return { message: "Something went wrong. Please try again.", details: [] };
  }
  if (error.code === "MOVE_NOT_ALLOWED") {
    const rawViolations = error.body?.["violations"];
    const details = Array.isArray(rawViolations)
      ? rawViolations
          .map((v) => (typeof v === "object" && v !== null ? (v as Record<string, unknown>)["message"] : null))
          .filter((m): m is string => typeof m === "string")
      : [];
    return { message: "This move isn't allowed.", details };
  }
  if (error.code === "INVALID_EDIT_TARGET") {
    return { message: "That lesson couldn't be found in the current timetable. Refresh and try again.", details: [] };
  }
  if (error.code === "REOPTIMIZATION_INFEASIBLE") {
    return {
      message: "No feasible re-optimized timetable exists with the current locks and configuration.",
      details: [],
    };
  }
  if (error.code === "REOPTIMIZATION_INVALID_INPUT") {
    const rawErrors = error.body?.["errors"];
    const details = Array.isArray(rawErrors)
      ? rawErrors
          .map((e) => (typeof e === "object" && e !== null ? (e as Record<string, unknown>)["message"] : null))
          .filter((m): m is string => typeof m === "string")
      : [];
    return { message: "Re-optimization could not be attempted.", details };
  }
  // 404 ("Active schedule not found"/"Scheduling configuration not
  // found"), and any other/unexpected structured error, fall back to
  // the backend's own safe `detail` string.
  return { message: error.detail, details: [] };
}

function lockKey(requirementId: string, dayId: string, periodId: string): string {
  return `${requirementId}|${dayId}|${periodId}`;
}

function ClassTimetableEditor({ schoolId, academicYearId, timetable, onMutationSuccess }: ClassTimetableEditorProps) {
  const [lockedKeys, setLockedKeys] = useState<Set<string>>(new Set());
  const [selection, setSelection] = useState<Selection | null>(null);
  const [mode, setMode] = useState<Mode>("view");
  const [target, setTarget] = useState<{ dayId: string; periodId: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionErrorDetails, setActionErrorDetails] = useState<string[]>([]);
  const [staleNotice, setStaleNotice] = useState<string | null>(null);

  const [reoptimizeConfirming, setReoptimizeConfirming] = useState(false);
  const [reoptimizeBusy, setReoptimizeBusy] = useState(false);
  const [reoptimizeError, setReoptimizeError] = useState<string | null>(null);
  const [reoptimizeErrorDetails, setReoptimizeErrorDetails] = useState<string[]>([]);

  // Move-target preview (backend's authoritative `move/preview`): `null`
  // means "not yet evaluated" -- the grid must render every candidate
  // neutral/gray in that state, never defaulting to allowed/green.
  const [previewTargets, setPreviewTargets] = useState<Map<string, PreviewTargetState> | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [previewRetryToken, setPreviewRetryToken] = useState(0);
  const [inspectedForbidden, setInspectedForbidden] = useState<{
    dayId: string;
    periodId: string;
    violations: MoveViolation[];
  } | null>(null);

  // Fires exactly once per "enter Move mode" -- no separate "Check"
  // button. Re-fires only if the admin cancels and re-enters Move mode,
  // or hits Retry after a failure (`previewRetryToken`).
  useEffect(() => {
    if (mode !== "choosing-target" || selection === null) {
      return;
    }
    const controller = new AbortController();
    setPreviewTargets(null);
    setPreviewFailed(false);
    setInspectedForbidden(null);
    previewMove(
      schoolId,
      academicYearId,
      {
        base_version_number: timetable.version_number,
        requirement_id: selection.requirementId,
        source_day_id: selection.dayId,
        source_period_id: selection.periodId,
      },
      controller.signal,
    )
      .then((response) => {
        if (controller.signal.aborted) {
          return;
        }
        const map = new Map<string, PreviewTargetState>();
        for (const target of response.targets) {
          map.set(`${target.day_id}|${target.period_id}`, { allowed: target.allowed, violations: target.violations });
        }
        setPreviewTargets(map);
      })
      .catch((error) => {
        if (controller.signal.aborted) {
          return;
        }
        if (error instanceof ApiError && error.code === "STALE_SCHEDULE_VERSION") {
          setStaleNotice("The timetable changed since this page loaded. Refreshing — please try your move again.");
          onMutationSuccess();
          return;
        }
        setPreviewFailed(true);
      });
    return () => {
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, selection, schoolId, academicYearId, timetable.version_number, previewRetryToken]);

  // Lock badges come from a separate GET -- re-fetched whenever the
  // class projection's own version changes (a fresh version means the
  // lock set may have changed too, whether or not THIS panel caused it).
  useEffect(() => {
    const controller = new AbortController();
    getActiveSchedule(schoolId, academicYearId, controller.signal)
      .then((active) => {
        if (controller.signal.aborted) {
          return;
        }
        setLockedKeys(
          new Set(active.locked_occurrences.map((k) => lockKey(k.requirement_id, k.day_id, k.anchor_period_id))),
        );
      })
      .catch(() => {
        // Non-fatal: lock badges simply stay as they were/empty. The
        // grid and every mutation remain fully usable without them.
      });
    return () => {
      controller.abort();
    };
  }, [schoolId, academicYearId, timetable.version_number]);

  // A fresh `timetable.version_number` means a refresh actually landed
  // -- clear any stale-version notice and reset in-flight editing state
  // so the panel never keeps showing a selection/target that predates
  // the refreshed timetable.
  useEffect(() => {
    setStaleNotice(null);
    setSelection(null);
    setMode("view");
    setTarget(null);
    setPreviewTargets(null);
    setPreviewFailed(false);
    setInspectedForbidden(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timetable.version_number]);

  function handleSelectOccurrence(entry: ClassTimetableEntry, dayId: string, periodId: string) {
    if (entry.requirement_id === null || entry.source !== "REQUIREMENT") {
      return; // Reserved Activities are fixed placements -- never selectable here.
    }
    setActionError(null);
    setActionErrorDetails([]);
    setSelection({
      requirementId: entry.requirement_id,
      activityName: entry.activity_name,
      teacherName: entry.teacher_name,
      groupName: entry.participant_group_name,
      dayId,
      dayName: dayName(timetable, dayId),
      periodId,
      periodName: periodName(timetable, periodId),
      locked: lockedKeys.has(lockKey(entry.requirement_id, dayId, periodId)),
    });
    setMode("view");
    setTarget(null);
  }

  function handleCancelSelection() {
    setSelection(null);
    setMode("view");
    setTarget(null);
    setActionError(null);
    setActionErrorDetails([]);
    setPreviewTargets(null);
    setPreviewFailed(false);
    setInspectedForbidden(null);
  }

  function handleStartMove() {
    setMode("choosing-target");
    setActionError(null);
    setActionErrorDetails([]);
  }

  function handleSelectTarget(dayId: string, periodId: string) {
    setTarget({ dayId, periodId });
    setInspectedForbidden(null);
    setMode("confirming-move");
  }

  function handleInspectForbiddenTarget(dayId: string, periodId: string, violations: MoveViolation[]) {
    setInspectedForbidden({ dayId, periodId, violations });
  }

  function handleCancelTarget() {
    setMode("view");
    setTarget(null);
    setPreviewTargets(null);
    setPreviewFailed(false);
    setInspectedForbidden(null);
  }

  function handleRetryPreview() {
    setPreviewRetryToken((token) => token + 1);
  }

  async function handleConfirmMove() {
    if (selection === null || target === null || busy) {
      return;
    }
    setBusy(true);
    setActionError(null);
    setActionErrorDetails([]);
    try {
      await moveScheduleEntry(schoolId, academicYearId, {
        base_version_number: timetable.version_number,
        requirement_id: selection.requirementId,
        source_day_id: selection.dayId,
        source_period_id: selection.periodId,
        target_day_id: target.dayId,
        target_period_id: target.periodId,
      });
      onMutationSuccess();
    } catch (error) {
      if (error instanceof ApiError && error.code === "STALE_SCHEDULE_VERSION") {
        setStaleNotice("The timetable changed since this page loaded. Refreshing — please try your move again.");
        onMutationSuccess();
        return;
      }
      const { message, details } = describeEditingError(error);
      setActionError(message);
      setActionErrorDetails(details);
      setMode("view");
      setTarget(null);
    } finally {
      setBusy(false);
    }
  }

  async function handleLockToggle() {
    if (selection === null || busy) {
      return;
    }
    setBusy(true);
    setActionError(null);
    setActionErrorDetails([]);
    const action = selection.locked ? unlockOccurrence : lockOccurrence;
    try {
      await action(schoolId, academicYearId, {
        base_version_number: timetable.version_number,
        requirement_id: selection.requirementId,
        day_id: selection.dayId,
        period_id: selection.periodId,
      });
      onMutationSuccess();
    } catch (error) {
      if (error instanceof ApiError && error.code === "STALE_SCHEDULE_VERSION") {
        setStaleNotice("The timetable changed since this page loaded. Refreshing — please try again.");
        onMutationSuccess();
        return;
      }
      const { message, details } = describeEditingError(error);
      setActionError(message);
      setActionErrorDetails(details);
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirmReoptimize() {
    if (reoptimizeBusy) {
      return;
    }
    setReoptimizeBusy(true);
    setReoptimizeError(null);
    setReoptimizeErrorDetails([]);
    try {
      await reoptimizeSchedule(schoolId, academicYearId, { base_version_number: timetable.version_number });
      setReoptimizeConfirming(false);
      onMutationSuccess();
    } catch (error) {
      if (error instanceof ApiError && error.code === "STALE_SCHEDULE_VERSION") {
        setStaleNotice("The timetable changed since this page loaded. Refreshing — please try again.");
        setReoptimizeConfirming(false);
        onMutationSuccess();
        return;
      }
      const { message, details } = describeEditingError(error);
      setReoptimizeError(message);
      setReoptimizeErrorDetails(details);
    } finally {
      setReoptimizeBusy(false);
    }
  }

  const gridEditing = {
    selected: selection !== null ? { requirementId: selection.requirementId, dayId: selection.dayId, periodId: selection.periodId } : null,
    targetMode: mode === "choosing-target",
    previewTargets: previewFailed ? null : previewTargets,
    lockedKeys,
    disabled: busy,
    onSelectOccurrence: handleSelectOccurrence,
    onSelectTarget: handleSelectTarget,
    onInspectForbiddenTarget: handleInspectForbiddenTarget,
  };

  return (
    <div className="editing-panel-wrapper">
      {staleNotice !== null && (
        <div className="stale-banner" role="alert">
          <p>{staleNotice}</p>
        </div>
      )}

      <div className="reoptimize-bar">
        {!reoptimizeConfirming ? (
          <button
            type="button"
            className="action-button"
            onClick={() => {
              setReoptimizeConfirming(true);
              setReoptimizeError(null);
              setReoptimizeErrorDetails([]);
            }}
            disabled={busy || reoptimizeBusy}
          >
            Re-optimize timetable
          </button>
        ) : (
          <div className="reoptimize-confirm">
            <p>
              Re-optimizing keeps every locked lesson fixed and may move unlocked lessons to find a better fit. A new
              timetable version will be created.
            </p>
            <span className="reoptimize-confirm-actions">
              <button type="button" className="btn-primary" onClick={handleConfirmReoptimize} disabled={reoptimizeBusy}>
                {reoptimizeBusy ? "Re-optimizing…" : "Confirm re-optimize"}
              </button>
              <button
                type="button"
                onClick={() => setReoptimizeConfirming(false)}
                disabled={reoptimizeBusy}
              >
                Cancel
              </button>
            </span>
          </div>
        )}
        {reoptimizeError !== null && (
          <div role="alert" className="generate-error">
            <p>{reoptimizeError}</p>
            {reoptimizeErrorDetails.length > 0 && (
              <ul className="generate-diagnostics">
                {reoptimizeErrorDetails.map((detail, index) => (
                  <li key={index}>{detail}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {selection !== null && (
        <div className="lesson-edit-panel">
          <h2 className="lesson-edit-panel-heading">{selection.activityName}</h2>
          <p className="lesson-edit-panel-meta">
            {selection.groupName !== null && <span>{selection.groupName}</span>}
            {selection.groupName !== null && selection.teacherName !== null && <span> · </span>}
            {selection.teacherName !== null && <span>{selection.teacherName}</span>}
          </p>
          <p className="lesson-edit-panel-slot">
            {selection.dayName}, {selection.periodName}
            {selection.locked && <span className="timetable-entry-locked"> 🔒 Locked</span>}
          </p>

          {mode === "view" && (
            <span className="lesson-edit-panel-actions">
              <button type="button" className="btn-primary" onClick={handleStartMove} disabled={busy || selection.locked}>
                Move
              </button>
              <button type="button" className="action-button" onClick={handleLockToggle} disabled={busy}>
                {busy ? "Working…" : selection.locked ? "Unlock" : "Lock"}
              </button>
              <button type="button" onClick={handleCancelSelection} disabled={busy}>
                Cancel
              </button>
            </span>
          )}
          {selection.locked && mode === "view" && (
            <p className="lesson-edit-panel-hint">Unlock this lesson before moving it.</p>
          )}

          {mode === "choosing-target" && (
            <>
              {previewFailed ? (
                <div role="alert" className="lesson-edit-panel-error">
                  <p>Couldn't check available destinations. The timetable is unchanged.</p>
                  <span className="lesson-edit-panel-actions">
                    <button type="button" className="action-button" onClick={handleRetryPreview}>
                      Retry
                    </button>
                  </span>
                </div>
              ) : previewTargets === null ? (
                <p className="lesson-edit-panel-hint" aria-live="polite">
                  Checking available destinations…
                </p>
              ) : (
                <p className="lesson-edit-panel-hint">
                  Green destinations are allowed; red destinations are not -- select one to see why.
                </p>
              )}
              {inspectedForbidden !== null && (
                <div className="lesson-edit-panel-error">
                  <p>
                    Not allowed: {dayName(timetable, inspectedForbidden.dayId)},{" "}
                    {periodName(timetable, inspectedForbidden.periodId)}
                  </p>
                  {inspectedForbidden.violations.length > 0 && (
                    <ul className="generate-diagnostics">
                      {inspectedForbidden.violations.map((violation, index) => (
                        <li key={index}>{violation.message}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
              <span className="lesson-edit-panel-actions">
                <button type="button" onClick={handleCancelTarget}>
                  Cancel
                </button>
              </span>
            </>
          )}

          {mode === "confirming-move" && target !== null && (
            <>
              <p className="lesson-edit-panel-confirm">
                Move <strong>{selection.activityName}</strong>: {selection.dayName}, {selection.periodName} →{" "}
                {dayName(timetable, target.dayId)}, {periodName(timetable, target.periodId)}?
              </p>
              <span className="lesson-edit-panel-actions">
                <button type="button" className="btn-primary" onClick={handleConfirmMove} disabled={busy}>
                  {busy ? "Moving…" : "Confirm move"}
                </button>
                <button type="button" onClick={handleCancelTarget} disabled={busy}>
                  Cancel
                </button>
              </span>
            </>
          )}

          {actionError !== null && (
            <div role="alert" className="lesson-edit-panel-error">
              <p>{actionError}</p>
              {actionErrorDetails.length > 0 && (
                <ul className="generate-diagnostics">
                  {actionErrorDetails.map((detail, index) => (
                    <li key={index}>{detail}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}

      <TimetableGrid timetable={timetable} editing={gridEditing} />
    </div>
  );
}

export default ClassTimetableEditor;
