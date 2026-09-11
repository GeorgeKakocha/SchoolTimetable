import type { ClassTimetableEntry, ClassTimetableResponse } from "../api/types";

/** Manual timetable editing (backend slice already shipped): optional
 * bundle of editing state/callbacks. Omitted entirely, the grid renders
 * exactly as it always has -- fully read-only, no behavior change for
 * any future consumer that doesn't pass it. */
export interface TimetableGridEditing {
  /** The occurrence currently shown in the edit panel, or `null`. */
  selected: { requirementId: string; dayId: string; periodId: string } | null;
  /** True after the admin clicks "Move" -- every instructional cell
   * becomes a destination-choosing target; entries stop being
   * individually clickable (source selection is not evaluated while
   * choosing a target). */
  targetMode: boolean;
  /** `"${requirement_id}|${day_id}|${period_id}"` for the ANCHOR period
   * of every currently locked occurrence -- built from `GET .../
   * schedule/active`'s `locked_occurrences`. Known limitation: a locked
   * multi-period REQUIRED/PREFERRED-double block shows the locked badge
   * only on its anchor (first) period's cell, not on every period the
   * block spans -- `OccurrenceKey` only ever names the anchor, and nothing
   * in the class timetable projection describes a block's remaining
   * span, so reconstructing "every period this locked occurrence covers"
   * would require guessing block shape here rather than reflecting a
   * value the backend actually returned. */
  lockedKeys: Set<string>;
  /** True while a move/lock/unlock request is in flight -- disables
   * every click target so a second click can never fire a duplicate
   * request. */
  disabled: boolean;
  onSelectOccurrence: (entry: ClassTimetableEntry, dayId: string, periodId: string) => void;
  onSelectTarget: (dayId: string, periodId: string) => void;
}

interface TimetableGridProps {
  timetable: ClassTimetableResponse;
  editing?: TimetableGridEditing;
}

function lockKey(requirementId: string, dayId: string, periodId: string): string {
  return `${requirementId}|${dayId}|${periodId}`;
}

/**
 * Renders `timetable.days`/`timetable.rows` in exactly the order the
 * backend already provides -- no re-sorting, no grouping/deduplication,
 * no assumption of a fixed day/period count (Phase 3B first-view
 * Decision #32, Owner Decisions 3/4/7). A cell may hold zero, one, or
 * many entries; each entry renders as its own distinct block, never
 * merged into one synthetic label.
 *
 * With `editing` supplied (manual timetable editing MVP): an ordinary
 * `REQUIREMENT` entry becomes a real `<button>` (source selection); a
 * `RESERVED_BLOCK` entry never does (Reserved Activities are fixed
 * placements, never movable/lockable from this grid). While
 * `editing.targetMode` is true, every instructional cell instead
 * becomes one `<button>` wrapping its whole (read-only, un-clickable)
 * content -- clicking anywhere in the cell chooses it as the
 * destination; this is "choose a destination to validate", never a
 * claim that every cell is a guaranteed-valid target (the backend
 * remains authoritative and may still reject it).
 */
function TimetableGrid({ timetable, editing }: TimetableGridProps) {
  return (
    <div className="timetable-scroll">
      <table className="timetable-grid">
        <colgroup>
          <col className="timetable-col-period" />
          {timetable.days.map((day) => (
            <col className="timetable-col-day" key={day.id} />
          ))}
        </colgroup>
        <thead>
          <tr>
            <th scope="col">Period</th>
            {timetable.days.map((day) => (
              <th scope="col" key={day.id}>
                {day.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {timetable.rows.map((row) => (
            <tr key={row.period_id}>
              <th scope="row">{row.period_name}</th>
              {row.cells.map((cell) => {
                const isSelectedCell =
                  editing?.selected !== null &&
                  editing?.selected !== undefined &&
                  editing.selected.dayId === cell.day_id &&
                  editing.selected.periodId === row.period_id;

                if (editing !== undefined && editing.targetMode) {
                  return (
                    <td key={cell.day_id} className="timetable-cell-target-candidate">
                      <button
                        type="button"
                        className="timetable-target-slot"
                        disabled={editing.disabled}
                        aria-label={`Move to ${timetable.days.find((d) => d.id === cell.day_id)?.name ?? cell.day_id}, ${row.period_name}`}
                        onClick={() => editing.onSelectTarget(cell.day_id, row.period_id)}
                      >
                        {cell.entries.map((entry, index) => (
                          <TimetableEntryBlock
                            entry={entry}
                            key={entryKey(entry, index)}
                            locked={false}
                            interactive={false}
                          />
                        ))}
                      </button>
                    </td>
                  );
                }

                return (
                  <td key={cell.day_id} className={isSelectedCell ? "timetable-cell-selected" : undefined}>
                    {cell.entries.map((entry, index) => {
                      const locked =
                        entry.requirement_id !== null &&
                        (editing?.lockedKeys.has(lockKey(entry.requirement_id, cell.day_id, row.period_id)) ?? false);
                      const interactive = editing !== undefined && entry.source === "REQUIREMENT";
                      return (
                        <TimetableEntryBlock
                          entry={entry}
                          key={entryKey(entry, index)}
                          locked={locked}
                          interactive={interactive}
                          disabled={editing?.disabled}
                          onClick={
                            interactive ? () => editing!.onSelectOccurrence(entry, cell.day_id, row.period_id) : undefined
                          }
                        />
                      );
                    })}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function entryKey(entry: ClassTimetableEntry, index: number): string {
  // Exactly one of these is ever non-null (backend invariant), so this
  // is stable and unique within one cell; `index` is only a fallback.
  return entry.requirement_id ?? entry.reserved_block_id ?? String(index);
}

function TimetableEntryBlock({
  entry,
  locked,
  interactive,
  disabled,
  onClick,
}: {
  entry: ClassTimetableEntry;
  locked: boolean;
  interactive: boolean;
  disabled?: boolean | undefined;
  onClick?: (() => void) | undefined;
}) {
  const hasGroup = entry.participant_group_name !== null;
  const hasTeacher = entry.teacher_name !== null;

  const content = (
    <>
      <div className="timetable-entry-activity">
        {entry.activity_name}
        {entry.source === "RESERVED_BLOCK" && <span className="timetable-entry-reserved">Reserved</span>}
        {locked && (
          <span className="timetable-entry-locked" title="Locked -- will not move during re-optimization">
            🔒 Locked
          </span>
        )}
      </div>
      {(hasGroup || hasTeacher) && (
        <div className="timetable-entry-secondary">
          {hasGroup && <span className="timetable-entry-group">{entry.participant_group_name}</span>}
          {hasGroup && hasTeacher && <span className="timetable-entry-separator"> · </span>}
          {hasTeacher && <span className="timetable-entry-teacher">{entry.teacher_name}</span>}
        </div>
      )}
    </>
  );

  if (interactive) {
    return (
      <button
        type="button"
        className="timetable-entry timetable-entry-button"
        disabled={disabled}
        onClick={onClick}
        aria-label={`Edit ${entry.activity_name}${hasTeacher ? `, ${entry.teacher_name}` : ""}${
          locked ? ", locked" : ""
        }`}
      >
        {content}
      </button>
    );
  }

  return <div className="timetable-entry">{content}</div>;
}

export default TimetableGrid;
