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
// this: it also carries days, periods, activities, teaching
// requirements, resources, teacher availabilities, reserved blocks, and
// fixed placements. This frontend slice only ever needs the class/
// teacher selector index, so only that subset is mirrored here --
// dozens of unused configuration fields are deliberately NOT
// reproduced. If a later slice needs more of `/config`, extend this
// type then, not now. `teachers` was added for the Teacher Timetable
// mode switch (next product slice after Phase 3C.3) -- the same
// authoritative, unconditional teacher list `/config` already returns
// (every teacher, including zero-load ones), reusing the one `/config`
// fetch `TimetablePage` already makes rather than a second endpoint or
// any coupling to Teaching Assignments' own separate `TeacherOption`
// mirror.

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

export interface TeacherSummary {
  id: string;
  name: string;
}

export interface SchedulingConfigIndexResponse {
  school: SchoolSummary;
  academic_year: AcademicYearSummary;
  class_sections: ClassSectionSummary[];
  teachers: TeacherSummary[];
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

// -- GET /schools/{school_id}/years/{year_id}/schedule/active/teachers/{teacher_id} --
//
// Mirrors the backend's `TeacherTimetableResponse` exactly -- the
// sibling teacher-timetable projection (next product slice after
// Phase 3C.3, no new phase number). Deliberately its own type family,
// not a reuse of `ClassTimetable*` -- `TeacherTimetableEntry`
// additionally carries `participant_group_role` and resolved
// `class_sections` (never present on `ClassTimetableEntry`, which
// doesn't need them), required so the frontend can reproduce the
// WHOLE_CLASS/SUBGROUP/MERGED_CLASSES display rule already
// established for Teaching Assignments without ever inferring role
// from name/count. `DayHeader`/`EntrySource`/`SolverStatus` above are
// reused as-is -- genuinely generic, not feature-specific.

export interface TeacherTimetableClassSection {
  id: string;
  name: string;
}

export interface TeacherTimetableEntry {
  source: EntrySource;
  activity_id: string;
  activity_name: string;
  participant_group_id: string | null;
  participant_group_name: string | null;
  participant_group_role: string | null;
  class_sections: TeacherTimetableClassSection[];
  requirement_id: string | null;
  reserved_block_id: string | null;
  resource_id: string | null;
}

export interface TeacherTimetableCell {
  day_id: string;
  entries: TeacherTimetableEntry[];
}

export interface TeacherTimetableRow {
  period_id: string;
  period_name: string;
  cells: TeacherTimetableCell[];
}

export interface TeacherTimetableResponse {
  school_id: string;
  school_name: string;
  academic_year_id: string;
  academic_year_label: string;
  teacher_id: string;
  teacher_name: string;
  version_number: number;
  solver_status: SolverStatus;
  total_soft_penalty: number;
  created_at: string;
  is_active: boolean;
  days: DayHeader[];
  rows: TeacherTimetableRow[];
}

// -- POST /schools/{school_id}/years/{year_id}/schedule/generate --------
//
// Mirrors Phase 3A3.4's `GenerateScheduleResponse` exactly
// (`docs/DECISIONS.md` #31): the same five public version-summary
// fields `ClassTimetableResponse` already carries, and nothing else --
// no `entries` (the caller re-fetches the per-class timetable
// projection for that). Structured generation failures
// (`SCHEDULE_ALREADY_EXISTS`/`SCHEDULE_INFEASIBLE`/
// `CONFIGURATION_CHANGED_DURING_GENERATION`/`INVALID_CONFIGURATION`)
// are read off the existing `ApiError.code`/`.body` (3C.3b) -- no
// dedicated error DTO types are introduced for them.

export interface GenerateScheduleResponse {
  version_number: number;
  solver_status: SolverStatus;
  total_soft_penalty: number;
  created_at: string;
  is_active: boolean;
}

// -- GET /schools/{school_id}/years/{year_id}/teaching-assignments ------
//
// Mirrors Phase 3C.2b's `TeachingAssignmentsProjectionResponse` exactly
// (`docs/DECISIONS.md` #34-#36, `api/schemas.py`), plus Phase 3C.3b's
// write request/response shapes below.
//
// `participant_group_role` is typed as `string`, not a closed literal
// union: the backend schema itself declares it plain `str` (never a
// Pydantic `Literal`), so the frontend must not assume a fixed set
// either -- callers compare against the known values they care about
// and fall back safely for anything else, exactly like
// `advanced_reasons` below.

export interface TeachingAssignmentClassSection {
  id: string;
  name: string;
}

export interface TeachingAssignment {
  id: string;
  teacher_id: string;
  teacher_name: string;
  activity_id: string;
  activity_name: string;
  participant_group_id: string;
  participant_group_name: string;
  participant_group_role: string;
  class_sections: TeachingAssignmentClassSection[];
  weekly_periods: number;
  editable: boolean;
  advanced_reasons: string[];
}

export interface TeacherOption {
  id: string;
  name: string;
}

export interface ActivityOption {
  id: string;
  name: string;
}

export interface WholeClassTarget {
  class_section_id: string;
  class_section_name: string;
  participant_group_id: string;
  participant_group_name: string;
}

export interface TeacherWorkload {
  teacher_id: string;
  teacher_name: string;
  total_weekly_periods: number;
}

export interface ValidationDiagnostic {
  code: string;
  message: string;
  context: Record<string, unknown>;
}

export interface TeachingAssignmentsProjectionResponse {
  configuration_locked: boolean;
  assignments: TeachingAssignment[];
  teachers: TeacherOption[];
  whole_class_targets: WholeClassTarget[];
  activities: ActivityOption[];
  teacher_workloads: TeacherWorkload[];
}

// -- POST/PUT/DELETE .../teaching-assignments[/{requirement_id}] --------
//
// Mirrors Phase 3C.2a/3C.3b's write contract exactly
// (`docs/DECISIONS.md` #34-#36, `api/schemas.py`'s
// `TeachingAssignmentWriteRequest`/`TeachingAssignmentWriteResponse`/
// `TeachingAssignmentDeleteResponse`). `participant_group_id` must be
// sourced verbatim from a prior GET's
// `whole_class_targets[*].participant_group_id` -- this frontend never
// infers or constructs it (matching the backend docstring's own
// requirement). No backend validation/business rule is reproduced
// here; this is a pure wire-shape mirror, exactly like every other
// type in this file.

export interface TeachingAssignmentWriteRequest {
  teacher_id: string;
  participant_group_id: string;
  activity_id: string;
  weekly_periods: number;
}

export interface TeachingAssignmentWriteResponse {
  id: string;
  warnings: ValidationDiagnostic[];
}

export interface TeachingAssignmentDeleteResponse {
  deleted_id: string;
  warnings: ValidationDiagnostic[];
}
