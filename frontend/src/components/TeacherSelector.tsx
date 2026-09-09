import type { TeacherSummary } from "../api/types";

interface TeacherSelectorProps {
  teachers: TeacherSummary[];
  selectedTeacherId: string;
  onChange: (teacherId: string) => void;
  disabled?: boolean;
}

/**
 * Presentation-only, mirroring `ClassSelector.tsx` exactly: no API
 * call, no school/year selector. `teachers` is rendered in exactly the
 * order supplied -- the backend's `/config` response already preserves
 * canonical order, so this component never re-sorts (alphabetically or
 * otherwise). Every teacher renders, including zero-load ones.
 */
function TeacherSelector({ teachers, selectedTeacherId, onChange, disabled = false }: TeacherSelectorProps) {
  return (
    <label>
      Teacher{" "}
      <select
        value={selectedTeacherId}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {teachers.map((teacher) => (
          <option key={teacher.id} value={teacher.id}>
            {teacher.name}
          </option>
        ))}
      </select>
    </label>
  );
}

export default TeacherSelector;
