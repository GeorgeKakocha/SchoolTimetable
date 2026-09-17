import { buildTeacherMatrixCellLookup } from "../api/teacherTimetableMatrix";
import type {
  TeacherTimetableEntry,
  TeacherTimetableMatrixResponse,
} from "../api/types";
import { toRomanNumeral } from "./teacherMatrixPresentation";

interface TeacherTimetableMatrixProps {
  matrix: TeacherTimetableMatrixResponse;
}

function TeacherTimetableMatrix({ matrix }: TeacherTimetableMatrixProps) {
  if (matrix.teachers.length === 0) {
    return <p>No teachers are available in this schedule configuration.</p>;
  }
  if (matrix.periods.length === 0) {
    return <p>No instructional periods are configured for this schedule.</p>;
  }

  const cells = buildTeacherMatrixCellLookup(matrix);
  return (
    <div className="teacher-matrix-scroll" tabIndex={0} aria-label="Scrollable teacher timetable matrix">
      <table className="teacher-matrix" aria-label="Teacher timetable matrix">
        <thead>
          <tr>
            <th className="teacher-matrix-corner" scope="col" rowSpan={2}>Teacher</th>
            {matrix.days.map((day) => (
              <th
                className="teacher-matrix-day-header"
                scope="colgroup"
                colSpan={matrix.periods.length}
                key={day.id}
              >
                {day.name}
              </th>
            ))}
          </tr>
          <tr>
            {matrix.days.flatMap((day) =>
              matrix.periods.map((period, periodIndex) => (
                <th
                  className={boundaryClass(periodIndex, matrix.periods.length)}
                  scope="col"
                  key={`${day.id}|${period.id}`}
                >
                  {toRomanNumeral(periodIndex + 1)}
                </th>
              )),
            )}
          </tr>
        </thead>
        <tbody>
          {matrix.teachers.map((teacher) => (
            <tr key={teacher.id}>
              <th className="teacher-matrix-teacher" scope="row">{teacher.name}</th>
              {matrix.days.flatMap((day) =>
                matrix.periods.map((period, periodIndex) => {
                  const cell = cells.get(teacher.id)?.get(day.id)?.get(period.id);
                  return (
                    <td
                      className={boundaryClass(periodIndex, matrix.periods.length)}
                      aria-label={`${teacher.name}, ${day.name}, ${period.name}${cell === undefined ? ": Free" : ""}`}
                      key={`${day.id}|${period.id}`}
                    >
                      {cell?.entries.map((entry, entryIndex) => (
                        <MatrixEntry
                          entry={entry}
                          key={entry.requirement_id ?? entry.reserved_block_id ?? String(entryIndex)}
                        />
                      ))}
                    </td>
                  );
                }),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function boundaryClass(periodIndex: number, periodCount: number): string {
  const classes = [];
  if (periodIndex === 0) {
    classes.push("teacher-matrix-day-start");
  }
  if (periodIndex === periodCount - 1) {
    classes.push("teacher-matrix-day-end");
  }
  return classes.join(" ");
}

function MatrixEntry({ entry }: { entry: TeacherTimetableEntry }) {
  const classSectionNames = entry.class_sections.map((section) => section.name).join(", ");
  const mergedClassSectionNames = entry.class_sections.map((section) => section.name).join(" + ");
  let primary = entry.participant_group_name
    ?? (classSectionNames.length > 0 ? classSectionNames : entry.activity_name);
  if (entry.participant_group_role === "WHOLE_CLASS" && classSectionNames.length > 0) {
    primary = classSectionNames;
  } else if (entry.participant_group_role === "MERGED_CLASSES" && mergedClassSectionNames.length > 0) {
    primary = mergedClassSectionNames;
  }

  return (
    <div className="teacher-matrix-entry">
      <div className="teacher-matrix-entry-primary">{primary}</div>
    </div>
  );
}

export default TeacherTimetableMatrix;
