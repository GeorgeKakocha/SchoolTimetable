import type { TeacherTimetableEntry, TeacherTimetableResponse } from "../api/types";

interface TeacherTimetableGridProps {
  timetable: TeacherTimetableResponse;
}

/** Every `participant_group_role` `TeacherTimetableEntry` can carry,
 * mapped to the exact friendly label already established by
 * `TeachingAssignmentsPage.tsx`'s own (page-private) mapping --
 * duplicated here rather than extracted/shared, since it is a two-entry
 * constant and extracting it would be a disproportionate refactor for
 * this slice. `WHOLE_CLASS` needs no badge at all (handled separately
 * below); an unrecognized future role falls back to its raw form. */
const GROUP_ROLE_LABELS: Record<string, string> = {
  SUBGROUP: "Subgroup",
  MERGED_CLASSES: "Merged classes",
};

/**
 * A dedicated grid for the teacher-timetable projection -- deliberately
 * its own component, not a generalization of `TimetableGrid`
 * (`ClassTimetableEntry`/`TeacherTimetableEntry` differ enough --
 * `participant_group_role`/resolved `class_sections` vs.
 * `teacher_name` -- that forcing both through one shared prop shape
 * would blur, not clarify, either one). Reuses the exact same table/
 * entry visual language (`.timetable-grid`/`.timetable-entry*`
 * classes) and the exact same target-display rule Teaching Assignments
 * already established: `WHOLE_CLASS` shows the class name directly, no
 * badge; `SUBGROUP`/`MERGED_CLASSES` show the participant group's name
 * plus the existing quiet `.group-role-badge`. The authoritative
 * `participant_group_role` decides which -- never inferred from name
 * or `class_sections` length. Purely presentational: renders
 * `timetable.days`/`timetable.rows` in exactly the order the backend
 * already provides, no re-sorting/re-grouping.
 */
function TeacherTimetableGrid({ timetable }: TeacherTimetableGridProps) {
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
                    <TeacherTimetableEntryBlock entry={entry} key={entryKey(entry, index)} />
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

function entryKey(entry: TeacherTimetableEntry, index: number): string {
  // Exactly one of these is ever non-null (backend invariant), so this
  // is stable and unique within one cell; `index` is only a fallback.
  return entry.requirement_id ?? entry.reserved_block_id ?? String(index);
}

function TeacherTimetableEntryBlock({ entry }: { entry: TeacherTimetableEntry }) {
  return (
    <div className="timetable-entry">
      <div className="timetable-entry-activity">
        {entry.activity_name}
        {entry.source === "RESERVED_BLOCK" && <span className="timetable-entry-reserved">Reserved</span>}
      </div>
      <TeacherTimetableTarget entry={entry} />
    </div>
  );
}

function TeacherTimetableTarget({ entry }: { entry: TeacherTimetableEntry }) {
  if (entry.participant_group_role === null) {
    // A reserved activity with no participant-group target -- no
    // fabricated class/group label.
    return null;
  }
  const soleClassSection = entry.class_sections.length === 1 ? entry.class_sections[0] : undefined;
  if (entry.participant_group_role === "WHOLE_CLASS" && soleClassSection !== undefined) {
    return (
      <div className="timetable-entry-secondary">
        <span className="timetable-entry-group">{soleClassSection.name}</span>
      </div>
    );
  }
  const roleLabel = GROUP_ROLE_LABELS[entry.participant_group_role] ?? entry.participant_group_role;
  return (
    <div className="timetable-entry-secondary">
      <span className="timetable-entry-group">{entry.participant_group_name}</span>
      <span className="group-role-badge">{roleLabel}</span>
    </div>
  );
}

export default TeacherTimetableGrid;
