import { useMemo, useState } from "react";
import type {
  ReservedActivityClassSectionOption,
  ReservedActivityDay,
  ReservedActivityPeriod,
  ReservedActivitySlot,
  ReservedActivitySpecialActivityOption,
  ReservedActivityTeacherOption,
} from "../api/reservedActivities";
import ReservedActivitySlotGrid from "./ReservedActivitySlotGrid";

/**
 * Reserved Activities Slice B: the ONE contained, full-width Add/Edit
 * editor panel -- never a modal, never a drawer, never a card-inline
 * expansion (the corrected frontend contract, item 6/18: a narrow
 * side-drawer like `AssignmentDrawer`'s cannot hold the slot matrix
 * without horizontal scroll, so this reuses the full-width contained-
 * panel pattern `SubjectsPanel`'s own `create-panel` already
 * establishes instead).
 *
 * Purely a controlled form: owns only its own local field state and
 * structural validity (mirrors `AssignmentDrawer`'s role exactly) --
 * it never calls the API itself and never classifies a write failure;
 * `ReservedActivitiesPage` owns the actual create/update request, the
 * post-success authoritative refetch, and every error-classification
 * decision, handing back only an already-human `errors` list to
 * render in one summary.
 *
 * Class/slot selection is tracked as `Set`s locally (so click order
 * never matters), but the value handed to `onSubmit` -- and the
 * `isDirty` comparison against `initialValues` in edit mode -- is
 * always canonically ordered: classes in the same order as the
 * `classSections` catalog (the projection's own authoritative order),
 * slots in Day-then-Period order (from the `days`/`instructionalPeriods`
 * catalogs' own already-index-ordered arrays) -- never selection/click
 * order. This is what keeps `isDirty` from going spuriously true after
 * toggling a checkbox off and back on.
 */

export interface ReservedActivityDraftValues {
  specialActivityId: string;
  classSectionIds: string[];
  teacherId: string | null;
  slots: ReservedActivitySlot[];
}

interface ReservedActivityEditorProps {
  mode: "create" | "edit";
  specialActivities: ReservedActivitySpecialActivityOption[];
  classSections: ReservedActivityClassSectionOption[];
  teachers: ReservedActivityTeacherOption[];
  days: ReservedActivityDay[];
  instructionalPeriods: ReservedActivityPeriod[];
  initialValues?: ReservedActivityDraftValues | undefined;
  submitting: boolean;
  errors: string[] | null;
  firstFieldRef?: React.RefObject<HTMLSelectElement | null>;
  onCancel: () => void;
  onSubmit: (values: ReservedActivityDraftValues) => void;
}

const UNSELECTED = "";
const NO_TEACHER = "";

function slotKey(dayId: string, periodId: string): string {
  return JSON.stringify([dayId, periodId]);
}

function canonicalClassIds(
  classSections: ReservedActivityClassSectionOption[],
  selected: ReadonlySet<string>,
): string[] {
  return classSections.filter((classSection) => selected.has(classSection.id)).map((classSection) => classSection.id);
}

function canonicalSlots(
  days: ReservedActivityDay[],
  instructionalPeriods: ReservedActivityPeriod[],
  selected: ReadonlySet<string>,
): ReservedActivitySlot[] {
  const slots: ReservedActivitySlot[] = [];
  for (const day of days) {
    for (const period of instructionalPeriods) {
      if (selected.has(slotKey(day.id, period.id))) {
        slots.push({ day_id: day.id, period_id: period.id });
      }
    }
  }
  return slots;
}

function serializeDraft(specialActivityId: string, teacherId: string | null, classIds: string[], slots: ReservedActivitySlot[]): string {
  const classPart = classIds.join(",");
  const slotPart = slots.map((slot) => `${slot.day_id}|${slot.period_id}`).join(",");
  return `${specialActivityId}::${teacherId ?? ""}::${classPart}::${slotPart}`;
}

function ReservedActivityEditor({
  mode,
  specialActivities,
  classSections,
  teachers,
  days,
  instructionalPeriods,
  initialValues,
  submitting,
  errors,
  firstFieldRef,
  onCancel,
  onSubmit,
}: ReservedActivityEditorProps) {
  const [specialActivityId, setSpecialActivityId] = useState(initialValues?.specialActivityId ?? UNSELECTED);
  const [teacherId, setTeacherId] = useState<string | null>(initialValues?.teacherId ?? null);
  const [selectedClassIds, setSelectedClassIds] = useState<Set<string>>(
    () => new Set(initialValues?.classSectionIds ?? []),
  );
  const [selectedSlotKeys, setSelectedSlotKeys] = useState<Set<string>>(
    () => new Set((initialValues?.slots ?? []).map((slot) => slotKey(slot.day_id, slot.period_id))),
  );

  const handleToggleClass = (classSectionId: string) => {
    setSelectedClassIds((current) => {
      const next = new Set(current);
      if (next.has(classSectionId)) {
        next.delete(classSectionId);
      } else {
        next.add(classSectionId);
      }
      return next;
    });
  };

  const handleToggleSlot = (dayId: string, periodId: string) => {
    const key = slotKey(dayId, periodId);
    setSelectedSlotKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const orderedClassIds = useMemo(
    () => canonicalClassIds(classSections, selectedClassIds),
    [classSections, selectedClassIds],
  );
  const orderedSlots = useMemo(
    () => canonicalSlots(days, instructionalPeriods, selectedSlotKeys),
    [days, instructionalPeriods, selectedSlotKeys],
  );

  const isStructurallyValid = specialActivityId !== UNSELECTED && orderedClassIds.length > 0 && orderedSlots.length > 0;

  const isDirty = useMemo(() => {
    if (initialValues === undefined) {
      return true;
    }
    const current = serializeDraft(specialActivityId, teacherId, orderedClassIds, orderedSlots);
    const initial = serializeDraft(
      initialValues.specialActivityId,
      initialValues.teacherId,
      canonicalClassIds(classSections, new Set(initialValues.classSectionIds)),
      canonicalSlots(days, instructionalPeriods, new Set(initialValues.slots.map((slot) => slotKey(slot.day_id, slot.period_id)))),
    );
    return current !== initial;
  }, [initialValues, specialActivityId, teacherId, orderedClassIds, orderedSlots, classSections, days, instructionalPeriods]);

  const canSubmit = isStructurallyValid && isDirty && !submitting;

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    onSubmit({
      specialActivityId,
      classSectionIds: orderedClassIds,
      teacherId,
      slots: orderedSlots,
    });
  }

  const headingId = "reserved-activity-editor-heading";

  return (
    <div className="reserved-activity-editor">
      <h2 id={headingId}>{mode === "create" ? "Add reserved activity" : "Edit reserved activity"}</h2>

      {errors !== null && errors.length > 0 && (
        <div className="reserved-activity-editor-errors" role="alert">
          <ul>
            {errors.map((message, index) => (
              <li key={index}>{message}</li>
            ))}
          </ul>
        </div>
      )}

      <form aria-labelledby={headingId} onSubmit={handleSubmit}>
        <label>
          Special Activity
          <select
            ref={firstFieldRef}
            value={specialActivityId}
            onChange={(event) => setSpecialActivityId(event.target.value)}
            disabled={submitting}
            autoFocus
            required
          >
            <option value={UNSELECTED}>Select a Special Activity…</option>
            {specialActivities.map((specialActivity) => (
              <option key={specialActivity.id} value={specialActivity.id}>
                {specialActivity.name}
              </option>
            ))}
          </select>
        </label>

        <fieldset className="reserved-class-fieldset" disabled={submitting}>
          <legend>Classes</legend>
          {classSections.map((classSection) => (
            <label key={classSection.id} className="reserved-class-checkbox-row">
              <input
                type="checkbox"
                checked={selectedClassIds.has(classSection.id)}
                onChange={() => handleToggleClass(classSection.id)}
                disabled={submitting}
              />
              {classSection.name}
            </label>
          ))}
        </fieldset>

        <label>
          Teacher
          <select
            value={teacherId ?? NO_TEACHER}
            onChange={(event) => setTeacherId(event.target.value === NO_TEACHER ? null : event.target.value)}
            disabled={submitting}
          >
            <option value={NO_TEACHER}>No teacher</option>
            {teachers.map((teacher) => (
              <option key={teacher.id} value={teacher.id}>
                {teacher.name}
              </option>
            ))}
          </select>
        </label>

        <ReservedActivitySlotGrid
          days={days}
          instructionalPeriods={instructionalPeriods}
          isSelected={(dayId, periodId) => selectedSlotKeys.has(slotKey(dayId, periodId))}
          onToggle={handleToggleSlot}
          disabled={submitting}
        />

        {!isStructurallyValid && (
          <p className="field-hint">Select a Special Activity, at least one Class, and at least one time slot.</p>
        )}

        <div className="reserved-activity-editor-actions">
          <button type="button" onClick={onCancel} disabled={submitting}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={!canSubmit}>
            {submitting ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default ReservedActivityEditor;
