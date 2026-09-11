import type { ClassTimetableEntry, ClassTimetableResponse, MoveViolation } from "../api/types";

/** One candidate destination slot's `validate_move` outcome, exactly as
 * the backend's `move/preview` response reported it -- this component
 * never re-derives `allowed`/`violations` itself. */
export interface PreviewTargetState {
  allowed: boolean;
  violations: MoveViolation[];
}

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
  /** Only meaningful while `targetMode` is true. `null` while the
   * `move/preview` request for the current source is in flight, hasn't
   * been dispatched yet, or failed -- every non-source cell must render
   * neutral/not-yet-evaluated in that case, NEVER default to
   * allowed/green. Once populated, keyed by `"${day_id}|${period_id}"`,
   * one entry per candidate destination the backend's preview reported
   * (the source's own slot is never a key here). */
  previewTargets: Map<string, PreviewTargetState> | null;
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
  /** Fired only for a target the preview already reported `allowed:
   * true`. */
  onSelectTarget: (dayId: string, periodId: string) => void;
  /** Fired for a target the preview reported `allowed: false` (on click
   * or focus) -- surfaces its violations for inspection; never sends a
   * move request and never enables a confirm step. */
  onInspectForbiddenTarget: (dayId: string, periodId: string, violations: MoveViolation[]) => void;
}

function targetKey(dayId: string, periodId: string): string {
  return `${dayId}|${periodId}`;
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
 * `editing.targetMode` is true, every instructional cell renders one of
 * four states, entirely driven by `editing.previewTargets` (itself
 * sourced from the backend's authoritative `move/preview` response --
 * this component never re-derives validity): the source occurrence's own
 * cell (BLUE, `.timetable-cell-target-source`, never clickable); a
 * candidate not yet evaluated -- `previewTargets` is `null` while the
 * preview request is in flight or has failed (GRAY,
 * `.timetable-cell-target-loading`, never clickable, and never treated
 * as allowed); an allowed candidate (GREEN, `.timetable-cell-target-
 * allowed`, ✓ indicator, clicking calls `onSelectTarget`); and a
 * forbidden candidate (RED, `.timetable-cell-target-forbidden`, ×
 * indicator, clicking/focusing/hovering calls `onInspectForbiddenTarget`
 * to surface its reasons elsewhere -- never sends a move request).
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
                  const dayLabel = timetable.days.find((d) => d.id === cell.day_id)?.name ?? cell.day_id;
                  const isSource =
                    editing.selected !== null &&
                    editing.selected.dayId === cell.day_id &&
                    editing.selected.periodId === row.period_id;

                  if (isSource) {
                    return (
                      <td key={cell.day_id} className="timetable-cell-target-source">
                        {cell.entries.map((entry, index) => (
                          <TimetableEntryBlock
                            entry={entry}
                            key={entryKey(entry, index)}
                            locked={false}
                            interactive={false}
                          />
                        ))}
                      </td>
                    );
                  }

                  const previewState = editing.previewTargets?.get(targetKey(cell.day_id, row.period_id)) ?? null;

                  if (previewState === null) {
                    return (
                      <td key={cell.day_id} className="timetable-cell-target-loading">
                        {cell.entries.map((entry, index) => (
                          <TimetableEntryBlock
                            entry={entry}
                            key={entryKey(entry, index)}
                            locked={false}
                            interactive={false}
                          />
                        ))}
                      </td>
                    );
                  }

                  const statusClass = previewState.allowed
                    ? "timetable-cell-target-allowed"
                    : "timetable-cell-target-forbidden";
                  const statusLabel = previewState.allowed ? "Allowed destination" : "Not allowed";
                  const inspect = () => editing.onInspectForbiddenTarget(cell.day_id, row.period_id, previewState.violations);

                  return (
                    <td key={cell.day_id} className={statusClass}>
                      <button
                        type="button"
                        className="timetable-target-slot"
                        disabled={editing.disabled}
                        aria-label={`${statusLabel}: ${dayLabel}, ${row.period_name}`}
                        onClick={previewState.allowed ? () => editing.onSelectTarget(cell.day_id, row.period_id) : inspect}
                        onFocus={previewState.allowed ? undefined : inspect}
                        onMouseEnter={previewState.allowed ? undefined : inspect}
                      >
                        <span className="timetable-target-indicator" aria-hidden="true">
                          {previewState.allowed ? "✓" : "×"}
                        </span>
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
