/**
 * Hand-written TypeScript mirrors of the backend's Pydantic response
 * contracts (`src/school_timetable/api/schemas.py`) -- never generated
 * from OpenAPI. Field-for-field, matching the exact wire shape, so a
 * future backend field never silently starts being consumed without an
 * explicit decision to add it here too (the same discipline the
 * backend's own `api/serializer.py` already applies).
 */

// -- GET /schools/{school_id}/years/{year_id}/config --------------------
//
// The real backend response (`SchedulingConfigResponse`) is a superset of
// this: it also carries days, periods, teachers, activities, teaching
// requirements, resources, teacher availabilities, reserved blocks, and
// fixed placements. This frontend slice only ever needs the class
// selector's index, so only that subset is mirrored here -- dozens of
// unused configuration fields are deliberately NOT reproduced. If a
// later slice needs more of `/config`, extend this type then, not now.

export interface SchoolSummary {
  id: string;
  name: string;
}

export interface AcademicYearSummary {
  id: string;
  label: string;
}

export interface ClassSectionSummary {
  id: string;
  name: string;
}

export interface SchedulingConfigIndexResponse {
  school: SchoolSummary;
  academic_year: AcademicYearSummary;
  class_sections: ClassSectionSummary[];
}

// -- GET /schools/{school_id}/years/{year_id}/schedule/active/classes/{class_section_id} --
//
// Mirrors Phase 3B.1's `ClassTimetableResponse` exactly (`docs/DECISIONS.md`
// #32) -- every field the backend returns, and nothing it deliberately
// omits: no `ordinal`, no `wall_time_seconds`, no `random_seed`, no
// `resource_name`, no persistence surrogate ID, no display color/icon.

export type EntrySource = "REQUIREMENT" | "RESERVED_BLOCK";
export type SolverStatus = "OPTIMAL" | "FEASIBLE";

export interface DayHeader {
  id: string;
  name: string;
}

export interface ClassTimetableEntry {
  source: EntrySource;
  activity_id: string;
  activity_name: string;
  teacher_id: string | null;
  teacher_name: string | null;
  participant_group_id: string | null;
  participant_group_name: string | null;
  requirement_id: string | null;
  reserved_block_id: string | null;
  resource_id: string | null;
}

export interface ClassTimetableCell {
  day_id: string;
  entries: ClassTimetableEntry[];
}

export interface ClassTimetableRow {
  period_id: string;
  period_name: string;
  cells: ClassTimetableCell[];
}

export interface ClassTimetableResponse {
  school_id: string;
  school_name: string;
  academic_year_id: string;
  academic_year_label: string;
  class_section_id: string;
  class_section_name: string;
  version_number: number;
  solver_status: SolverStatus;
  total_soft_penalty: number;
  created_at: string;
  is_active: boolean;
  days: DayHeader[];
  rows: ClassTimetableRow[];
}
