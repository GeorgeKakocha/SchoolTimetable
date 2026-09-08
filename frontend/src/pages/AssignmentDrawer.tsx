import { useEffect, useRef, useState } from "react";
import type { ActivityOption, TeacherOption, WholeClassTarget } from "../api/types";

/**
 * Phase 3C.3b: the right-side Add/Edit drawer for a plain `WHOLE_CLASS`
 * teaching assignment (`docs/DECISIONS.md` #34-#36's locked write
 * scope). Purely presentational/controlled -- it never calls the API
 * itself; `TeachingAssignmentsPage` owns the actual
 * create/update request, the post-success authoritative refetch, and
 * every error-classification decision (this component only ever shows
 * whatever single-line `error` string it is handed). No UI library:
 * a plain fixed-position overlay + panel, matching this project's
 * "no component library" discipline (`DECISIONS.md` Owner Decision 10).
 *
 * Option lists (`teachers`/`activities`/`wholeClassTargets`) are always
 * exactly the authoritative GET projection's own arrays -- this
 * component never invents, sorts, or filters them, and never defaults
 * a selection to their first entry; every selector starts on an
 * explicit, unselected placeholder option in create mode so a save can
 * never silently target the wrong teacher/class/activity.
 */

export interface AssignmentDrawerValues {
  teacherId: string;
  participantGroupId: string;
  activityId: string;
  weeklyPeriods: number;
}

interface AssignmentDrawerProps {
  mode: "create" | "edit";
  teachers: TeacherOption[];
  activities: ActivityOption[];
  wholeClassTargets: WholeClassTarget[];
  initialValues?: AssignmentDrawerValues | undefined;
  submitting: boolean;
  error: string | null;
  onCancel: () => void;
  onSubmit: (values: AssignmentDrawerValues) => void;
}

const UNSELECTED = "";

function isPositiveIntegerText(text: string): boolean {
  return /^[0-9]+$/.test(text) && Number(text) >= 1;
}

function AssignmentDrawer({
  mode,
  teachers,
  activities,
  wholeClassTargets,
  initialValues,
  submitting,
  error,
  onCancel,
  onSubmit,
}: AssignmentDrawerProps) {
  const [teacherId, setTeacherId] = useState(initialValues?.teacherId ?? UNSELECTED);
  const [participantGroupId, setParticipantGroupId] = useState(initialValues?.participantGroupId ?? UNSELECTED);
  const [activityId, setActivityId] = useState(initialValues?.activityId ?? UNSELECTED);
  const [weeklyPeriodsText, setWeeklyPeriodsText] = useState(
    initialValues !== undefined ? String(initialValues.weeklyPeriods) : "1",
  );

  const panelRef = useRef<HTMLDivElement>(null);
  const firstFieldRef = useRef<HTMLSelectElement>(null);

  // Initial focus on open + focus-return-on-close is owned by the
  // parent (it alone knows which button triggered the open, and
  // outlives this component's own mount/unmount). This effect only
  // places focus *inside* the drawer once, on mount.
  useEffect(() => {
    firstFieldRef.current?.focus();
  }, []);

  // Escape closes, and Tab/Shift+Tab are trapped inside the panel --
  // both ignored while a submit is in flight, so a stray keypress can
  // never abandon an in-progress request's outcome mid-air.
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (submitting) {
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key !== "Tab" || panelRef.current === null) {
        return;
      }
      const focusable = panelRef.current.querySelectorAll<HTMLElement>(
        'button, select, input, [href], [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) {
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (first === undefined || last === undefined) {
        return;
      }
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [submitting, onCancel]);

  const weeklyPeriodsValid = isPositiveIntegerText(weeklyPeriodsText);
  const isValid =
    teacherId !== UNSELECTED && participantGroupId !== UNSELECTED && activityId !== UNSELECTED && weeklyPeriodsValid;

  const headingId = "assignment-drawer-heading";

  function handleOverlayMouseDown(event: React.MouseEvent<HTMLDivElement>) {
    if (submitting) {
      return;
    }
    if (event.target === event.currentTarget) {
      onCancel();
    }
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting || !isValid) {
      return;
    }
    onSubmit({
      teacherId,
      participantGroupId,
      activityId,
      weeklyPeriods: Number(weeklyPeriodsText),
    });
  }

  return (
    <div className="drawer-overlay" onMouseDown={handleOverlayMouseDown}>
      <div className="drawer-panel" role="dialog" aria-modal="true" aria-labelledby={headingId} ref={panelRef}>
        <div className="drawer-header">
          <h2 id={headingId}>{mode === "create" ? "Add assignment" : "Edit assignment"}</h2>
          <button
            type="button"
            className="drawer-close"
            aria-label="Close"
            onClick={onCancel}
            disabled={submitting}
          >
            ×
          </button>
        </div>

        <form className="drawer-form" onSubmit={handleSubmit}>
          <label>
            Teacher
            <select
              ref={firstFieldRef}
              value={teacherId}
              onChange={(event) => setTeacherId(event.target.value)}
              disabled={submitting}
              required
            >
              <option value={UNSELECTED}>Select a teacher…</option>
              {teachers.map((teacher) => (
                <option key={teacher.id} value={teacher.id}>
                  {teacher.name}
                </option>
              ))}
            </select>
          </label>

          <label>
            Class
            <select
              value={participantGroupId}
              onChange={(event) => setParticipantGroupId(event.target.value)}
              disabled={submitting}
              required
            >
              <option value={UNSELECTED}>Select a class…</option>
              {wholeClassTargets.map((target) => (
                <option key={target.participant_group_id} value={target.participant_group_id}>
                  {target.class_section_name}
                </option>
              ))}
            </select>
          </label>

          <label>
            Activity
            <select
              value={activityId}
              onChange={(event) => setActivityId(event.target.value)}
              disabled={submitting}
              required
            >
              <option value={UNSELECTED}>Select an activity…</option>
              {activities.map((activity) => (
                <option key={activity.id} value={activity.id}>
                  {activity.name}
                </option>
              ))}
            </select>
          </label>

          <label>
            Weekly periods
            <input
              type="number"
              min={1}
              step={1}
              value={weeklyPeriodsText}
              onChange={(event) => setWeeklyPeriodsText(event.target.value)}
              disabled={submitting}
              required
            />
          </label>
          <p className="field-hint">Must be a whole number, 1 or more.</p>

          {error !== null && (
            <p role="alert" className="drawer-error">
              {error}
            </p>
          )}

          <div className="drawer-actions">
            <button type="button" onClick={onCancel} disabled={submitting}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={submitting || !isValid}>
              {submitting ? "Saving…" : "Save"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default AssignmentDrawer;
