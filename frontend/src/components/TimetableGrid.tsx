import type { ClassTimetableEntry, ClassTimetableResponse } from "../api/types";

interface TimetableGridProps {
  timetable: ClassTimetableResponse;
}

/**
 * Purely presentational: renders `timetable.days`/`timetable.rows` in
 * exactly the order the backend already provides -- no re-sorting, no
 * grouping/deduplication, no assumption of a fixed day/period count
 * (Phase 3B first-view Decision #32, Owner Decisions 3/4/7). A cell may
 * hold zero, one, or many entries; each entry renders as its own
 * distinct block, never merged into one synthetic label.
 */
function TimetableGrid({ timetable }: TimetableGridProps) {
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
              {row.cells.map((cell) => (
                <td key={cell.day_id}>
                  {cell.entries.map((entry, index) => (
                    <TimetableEntryBlock entry={entry} key={entryKey(entry, index)} />
                  ))}
                </td>
              ))}
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

function TimetableEntryBlock({ entry }: { entry: ClassTimetableEntry }) {
  const hasGroup = entry.participant_group_name !== null;
  const hasTeacher = entry.teacher_name !== null;

  return (
    <div className="timetable-entry">
      <div className="timetable-entry-activity">
        {entry.activity_name}
        {entry.source === "RESERVED_BLOCK" && <span className="timetable-entry-reserved">Reserved</span>}
      </div>
      {(hasGroup || hasTeacher) && (
        <div className="timetable-entry-secondary">
          {hasGroup && <span className="timetable-entry-group">{entry.participant_group_name}</span>}
          {hasGroup && hasTeacher && <span className="timetable-entry-separator"> · </span>}
          {hasTeacher && <span className="timetable-entry-teacher">{entry.teacher_name}</span>}
        </div>
      )}
    </div>
  );
}

export default TimetableGrid;
