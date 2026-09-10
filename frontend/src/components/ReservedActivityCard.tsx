import { useState } from "react";
import type {
  ReservedActivityClassSectionOption,
  ReservedActivityDay,
  ReservedActivityItem,
  ReservedActivityPeriod,
  ReservedActivitySpecialActivityOption,
  ReservedActivityTeacherOption,
} from "../api/reservedActivities";

/**
 * Reserved Activities Slice B: one stacked summary card -- never a
 * table row (item 14 of the frontend contract: multiple Classes and
 * multiple slots must remain readable, which a fixed-column table
 * cannot guarantee). Purely presentational: resolves every bare ID in
 * `item` against the current projection's own catalogs and renders
 * Edit/Delete action controls; owns no API/projection state itself.
 *
 * Defensive rendering (item 32): a reference that cannot be resolved
 * against the supplied catalogs -- legacy/corrupt data, never expected
 * from the real single-snapshot projection -- falls back to
 * "Unknown Special Activity"/"Unknown Class"/"Unknown Teacher"/
 * "Unknown time slot" rather than ever showing a raw natural ID. Such
 * a card stays fully readable and Delete-able; clicking Edit on it
 * shows a small inline notice instead of opening the (would-be
 * invalid) editor.
 */

interface ReservedActivityCardProps {
  item: ReservedActivityItem;
  specialActivities: ReservedActivitySpecialActivityOption[];
  classSections: ReservedActivityClassSectionOption[];
  teachers: ReservedActivityTeacherOption[];
  days: ReservedActivityDay[];
  periods: ReservedActivityPeriod[];
  locked: boolean;
  controlsDisabled: boolean;
  deleteConfirmId: string | null;
  deleteSubmitting: boolean;
  deleteError: string | null;
  onEditClick: (item: ReservedActivityItem) => void;
  onDeleteClick: (id: string) => void;
  onConfirmDelete: (id: string) => void;
  onCancelDelete: () => void;
}

interface SlotGroup {
  dayName: string;
  dayIndex: number;
  periodNames: string[];
}

function resolveName<T extends { id: string; name: string }>(catalog: T[], id: string): string | null {
  return catalog.find((entry) => entry.id === id)?.name ?? null;
}

function buildSlotGroups(
  item: ReservedActivityItem,
  days: ReservedActivityDay[],
  periods: ReservedActivityPeriod[],
): { groups: SlotGroup[]; hasUnknown: boolean } {
  let hasUnknown = false;
  const byDay = new Map<string, { dayIndex: number; periodEntries: { index: number; name: string }[] }>();

  for (const slot of item.slots) {
    const day = days.find((d) => d.id === slot.day_id);
    const period = periods.find((p) => p.id === slot.period_id);
    if (day === undefined || period === undefined) {
      hasUnknown = true;
      continue;
    }
    const existing = byDay.get(day.id);
    if (existing === undefined) {
      byDay.set(day.id, { dayIndex: day.index, periodEntries: [{ index: period.index, name: period.name }] });
    } else {
      existing.periodEntries.push({ index: period.index, name: period.name });
    }
  }

  const groups: SlotGroup[] = Array.from(byDay.entries())
    .map(([dayId, entry]) => {
      const day = days.find((d) => d.id === dayId);
      return {
        dayName: day?.name ?? "Unknown day",
        dayIndex: entry.dayIndex,
        periodNames: entry.periodEntries.sort((a, b) => a.index - b.index).map((p) => p.name),
      };
    })
    .sort((a, b) => a.dayIndex - b.dayIndex);

  return { groups, hasUnknown };
}

function ReservedActivityCard({
  item,
  specialActivities,
  classSections,
  teachers,
  days,
  periods,
  locked,
  controlsDisabled,
  deleteConfirmId,
  deleteSubmitting,
  deleteError,
  onEditClick,
  onDeleteClick,
  onConfirmDelete,
  onCancelDelete,
}: ReservedActivityCardProps) {
  const [cannotEditNotice, setCannotEditNotice] = useState(false);

  const specialActivityName = resolveName(specialActivities, item.special_activity_id);
  const classNames = item.class_section_ids.map((id) => resolveName(classSections, id));
  const teacherName = item.teacher_id === null ? null : resolveName(teachers, item.teacher_id);
  const { groups: slotGroups, hasUnknown: hasUnknownSlot } = buildSlotGroups(item, days, periods);

  const isResolvable =
    specialActivityName !== null &&
    classNames.every((name) => name !== null) &&
    (item.teacher_id === null || teacherName !== null) &&
    !hasUnknownSlot &&
    item.slots.length > 0;

  function handleEditClick() {
    if (!isResolvable) {
      setCannotEditNotice(true);
      return;
    }
    setCannotEditNotice(false);
    onEditClick(item);
  }

  return (
    <li className="reserved-activity-card">
      <div className="reserved-activity-card-primary">{specialActivityName ?? "Unknown Special Activity"}</div>

      <div className="reserved-activity-card-secondary">
        <span className="reserved-activity-card-classes">
          {classNames.map((name) => name ?? "Unknown Class").join(", ")}
        </span>
        <span className="reserved-activity-card-teacher">
          {item.teacher_id === null ? "No teacher" : (teacherName ?? "Unknown Teacher")}
        </span>
      </div>

      <div className="reserved-activity-card-slots">
        {slotGroups.length === 0 && hasUnknownSlot ? (
          <span>Unknown time slot</span>
        ) : (
          slotGroups.map((group) => (
            <div key={group.dayName} className="reserved-activity-card-slot-row">
              <span className="reserved-activity-card-day">{group.dayName}:</span> {group.periodNames.join(", ")}
            </div>
          ))
        )}
        {hasUnknownSlot && slotGroups.length > 0 && <div>Unknown time slot</div>}
      </div>

      {cannotEditNotice && (
        <p role="alert" className="reserved-activity-card-notice">
          This Reserved Activity cannot be edited because some referenced configuration data is unavailable.
        </p>
      )}

      <div className="reserved-activity-card-actions">
        {deleteConfirmId === item.id ? (
          <span className="delete-confirm">
            <span className="delete-confirm-text">Delete this reserved activity?</span>
            {deleteError !== null && (
              <p role="alert" className="delete-confirm-error">
                {deleteError}
              </p>
            )}
            <span className="delete-confirm-actions">
              <button type="button" onClick={() => onConfirmDelete(item.id)} disabled={deleteSubmitting}>
                {deleteSubmitting ? "Deleting…" : "Confirm delete"}
              </button>
              <button type="button" onClick={onCancelDelete} disabled={deleteSubmitting}>
                Cancel
              </button>
            </span>
          </span>
        ) : locked ? (
          <span className="actions-group">
            <button type="button" className="action-button" disabled aria-describedby="reserved-activities-lock-banner-text">
              Edit
            </button>
            <button
              type="button"
              className="action-button action-button-delete"
              disabled
              aria-describedby="reserved-activities-lock-banner-text"
            >
              Delete
            </button>
          </span>
        ) : (
          <span className="actions-group">
            <button type="button" className="action-button" onClick={handleEditClick} disabled={controlsDisabled}>
              Edit
            </button>
            <button
              type="button"
              className="action-button action-button-delete"
              onClick={() => onDeleteClick(item.id)}
              disabled={controlsDisabled}
            >
              Delete
            </button>
          </span>
        )}
      </div>
    </li>
  );
}

export default ReservedActivityCard;
