import type { ClassSectionSummary } from "../api/types";

interface ClassSelectorProps {
  classSections: ClassSectionSummary[];
  selectedClassId: string;
  onChange: (classId: string) => void;
  disabled?: boolean;
}

/**
 * Presentation-only: no API call, no school/year selector (Phase 3B first-view
 * Decision #32). `classSections` is rendered in exactly the order supplied --
 * the backend's `/config` response already preserves canonical order, so
 * this component never re-sorts (alphabetically or otherwise).
 */
function ClassSelector({ classSections, selectedClassId, onChange, disabled = false }: ClassSelectorProps) {
  return (
    <label>
      Class{" "}
      <select
        value={selectedClassId}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {classSections.map((classSection) => (
          <option key={classSection.id} value={classSection.id}>
            {classSection.name}
          </option>
        ))}
      </select>
    </label>
  );
}

export default ClassSelector;
