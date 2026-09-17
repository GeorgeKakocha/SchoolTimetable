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

// -- GET/POST/DELETE .../configuration/{state,draft} ------------------
// Revision numbers are public audit/presentation values. The backend never
// exposes its ConfigurationRevision database identity here.
export interface ConfigurationRevisionStateResponse {
  published_revision_number: number | null;
  draft_revision_number: number | null;
  configuration_locked: boolean;
  timetable_out_of_date: boolean;
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

// -- Whole-school Teacher Matrix -----------------------------------------
//
// Sparse by contract: `teachers[].cells` contains occupied coordinates
// only. Absence of a teacher/day/period coordinate means the teacher is
// free. The entry shape is exactly the existing teacher-timetable entry
// contract, so it is reused rather than renamed or duplicated.

export interface TeacherMatrixPeriod {
  id: string;
  name: string;
}

export interface TeacherMatrixCell {
  day_id: string;
  period_id: string;
  entries: TeacherTimetableEntry[];
}

export interface TeacherMatrixTeacher {
  id: string;
  name: string;
  cells: TeacherMatrixCell[];
}

export interface TeacherTimetableMatrixResponse {
  school_id: string;
  school_name: string;
  academic_year_id: string;
  academic_year_label: string;
  version_number: number;
  solver_status: SolverStatus;
  total_soft_penalty: number;
  created_at: string;
  is_active: boolean;
  days: DayHeader[];
  periods: TeacherMatrixPeriod[];
  teachers: TeacherMatrixTeacher[];
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

// -- Manual timetable editing backend slice: POST .../schedule/active/
// {move,lock,unlock,reoptimize}, all returning `ActiveScheduleResponse`
// -- the exact same shape `GET .../schedule/active` already returns
// (`docs/DECISIONS.md`'s manual-editing entries), so a successful
// mutation's response is never rendered directly: the caller re-fetches
// the per-class projection (`ClassTimetableResponse` above) for actual
// display, exactly like the existing generate flow already does. This
// type is used only to read `version_number` (the new
// `base_version_number` for the next mutation) and, via a separate
// `GET .../schedule/active` call, `locked_occurrences` for the grid's
// lock-state badges.

export interface ScheduleEntry {
  source: EntrySource;
  day_id: string;
  period_id: string;
  requirement_id: string | null;
  reserved_block_id: string | null;
  activity_id: string;
  teacher_id: string | null;
  participant_group_id: string | null;
  resource_id: string | null;
  class_sections: string[];
}

export interface LockedOccurrence {
  requirement_id: string;
  day_id: string;
  anchor_period_id: string;
}

export interface ActiveScheduleResponse {
  version_number: number;
  solver_status: SolverStatus;
  total_soft_penalty: number;
  created_at: string;
  is_active: boolean;
  entries: ScheduleEntry[];
  locked_occurrences: LockedOccurrence[];
}

export interface OccurrenceKey {
  requirement_id: string;
  day_id: string;
  anchor_period_id: string;
}

export interface RegenerateScheduleRequest {
  base_version_number: number;
  confirmed_incompatible_lock_keys: readonly OccurrenceKey[];
}

export interface IncompatibleLock {
  requirement_id: string;
  day_id: string;
  anchor_period_id: string;
  reason_code: string;
  message: string;
}

export interface IncompatibleLocksRequireConfirmationErrorBody {
  code: "INCOMPATIBLE_LOCKS_REQUIRE_CONFIRMATION";
  detail: string;
  incompatible_locks: IncompatibleLock[];
}

export interface StaleScheduleVersionErrorBody {
  code: "STALE_SCHEDULE_VERSION";
  detail: string;
  expected_base_version_number: number;
  actual_active_version_number: number;
}

export interface ConfigurationChangedDuringGenerationErrorBody {
  code: "CONFIGURATION_CHANGED_DURING_GENERATION";
  detail: string;
}

export interface NoConfigurationDraftErrorBody {
  code: "NO_CONFIGURATION_DRAFT";
  detail: string;
}

export interface ScheduleInfeasibleErrorBody {
  code: "SCHEDULE_INFEASIBLE";
  detail: string;
}

export interface InvalidConfigurationErrorBody {
  code: "INVALID_CONFIGURATION";
  detail: string;
  errors: ValidationDiagnostic[];
}

// -- POST .../schedule/active/move/preview -------------------------------
// Read-only: reports, for every OTHER instructional (day, period) slot,
// exactly what the backend's authoritative `validate_move` would say
// about moving the given source occurrence there. Never persists
// anything -- `version_number` is simply the currently active version
// this preview was computed against (the same value the caller must
// still pass back as `base_version_number` on the real move).

export interface MoveViolation {
  code: string;
  message: string;
}

export interface MovePreviewTarget {
  day_id: string;
  period_id: string;
  allowed: boolean;
  violations: MoveViolation[];
}

export interface MovePreviewResponse {
  version_number: number;
  targets: MovePreviewTarget[];
}

// -- GET .../schedule/versions, .../schedule/versions/{version_number}/
// {classes,teachers}/{id}, POST .../schedule/versions/{version_number}/
// restore -- schedule version history + restore. The historical class/
// teacher projections reuse `ClassTimetableResponse`/
// `TeacherTimetableResponse` unchanged (`is_active` may now be `false`);
// a successful restore reuses `ActiveScheduleResponse` unchanged too.

export interface ScheduleVersionSummary {
  version_number: number;
  created_at: string;
  solver_status: SolverStatus;
  total_soft_penalty: number;
  is_active: boolean;
  parent_version_number: number | null;
}

export interface ScheduleVersionHistoryResponse {
  versions: ScheduleVersionSummary[];
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
  resource_id: string | null;
}

export interface TeacherOption {
  id: string;
  name: string;
}

export interface ActivityOption {
  id: string;
  name: string;
}

// Resources B1's "fixed Resource" option list -- `Resource` is the
// existing `domain.resources.Resource(id, name, capacity)` (Resources
// Slice A); `capacity` means "maximum simultaneous resource
// occupations," never seat/headcount capacity.
export interface TeachingAssignmentResourceOption {
  id: string;
  name: string;
  capacity: number;
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
  resources: TeachingAssignmentResourceOption[];
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
  // Full-replacement, never PATCH (Resources B1): `null` (explicit,
  // never omitted by this frontend) means "no fixed Resource" -- on a
  // PUT this always clears any Resource currently assigned. A
  // non-null value assigns/replaces that exact Resource.
  resource_id: string | null;
}

export interface TeachingAssignmentWriteResponse {
  id: string;
  warnings: ValidationDiagnostic[];
}

export interface TeachingAssignmentDeleteResponse {
  deleted_id: string;
  warnings: ValidationDiagnostic[];
}

// -- GET/POST /schools/{school_id}/years/{year_id}/teachers -------------
// -- PUT/DELETE .../teachers/{teacher_id} --------------------------------
//
// Mirrors the Real-School Setup MVP Slice B Teacher CRUD contract exactly
// (`docs/DECISIONS.md`, `api/schemas.py`'s `TeacherProjectionItemResponse`/
// `TeachersProjectionResponse`/`TeacherWriteRequest`/`TeacherWriteResponse`/
// `TeacherDeleteResponse`). `name` is the backend's own derived
// `Teacher.full_name` -- included so no consumer re-implements the
// trim/join rule; `first_name`/`last_name` remain the raw authoritative
// edit fields. There is deliberately no duplicate-name rule on this
// resource (unlike Classes/Subjects) -- the frontend must not invent one.

export interface TeacherProjectionItem {
  id: string;
  first_name: string;
  last_name: string;
  name: string;
}

export interface TeachersProjectionResponse {
  configuration_locked: boolean;
  teachers: TeacherProjectionItem[];
}

export interface TeacherWriteRequest {
  first_name: string;
  last_name: string;
}

export interface TeacherWriteResponse {
  id: string;
  first_name: string;
  last_name: string;
  name: string;
}

export interface TeacherDeleteResponse {
  deleted_id: string;
}

// -- GET/POST /schools/{school_id}/years/{year_id}/classes ---------------
// -- PUT/DELETE .../classes/{class_id} ------------------------------------
//
// Mirrors the Real-School Setup MVP Slice C Class CRUD contract exactly
// (`docs/DECISIONS.md`, `api/schemas.py`'s `ClassSectionProjectionItemResponse`/
// `ClassSectionsProjectionResponse`/`ClassSectionWriteRequest`/
// `ClassSectionWriteResponse`/`ClassSectionDeleteResponse`). The canonical
// WHOLE_CLASS `ParticipantGroup` (Owner Decision #33) is never exposed
// here -- no group id/role/membership/ordinal field exists on this type
// family at all.

export interface ClassSectionProjectionItem {
  id: string;
  name: string;
}

export interface ClassesProjectionResponse {
  configuration_locked: boolean;
  classes: ClassSectionProjectionItem[];
}

export interface ClassSectionWriteRequest {
  name: string;
}

export interface ClassSectionWriteResponse {
  id: string;
  name: string;
}

export interface ClassSectionDeleteResponse {
  deleted_id: string;
}

// -- GET/POST /schools/{school_id}/years/{year_id}/subjects --------------
// -- PUT/DELETE .../subjects/{subject_id} ---------------------------------
//
// Mirrors the Real-School Setup MVP Slice D Subject CRUD contract exactly
// (`docs/DECISIONS.md`, `api/schemas.py`'s `SubjectProjectionItemResponse`/
// `SubjectsProjectionResponse`/`SubjectWriteRequest`/`SubjectWriteResponse`/
// `SubjectDeleteResponse`). "Subject" is the user-facing name for
// `Activity(kind=ORDINARY)` -- there is deliberately no `kind` field
// anywhere on this type family; the endpoint contract itself guarantees
// every returned item is ORDINARY, so the frontend never filters by kind
// itself.

export interface SubjectProjectionItem {
  id: string;
  name: string;
}

export interface SubjectsProjectionResponse {
  configuration_locked: boolean;
  subjects: SubjectProjectionItem[];
}

export interface SubjectWriteRequest {
  name: string;
}

export interface SubjectWriteResponse {
  id: string;
  name: string;
}

export interface SubjectDeleteResponse {
  deleted_id: string;
}

// -- GET /schools/{school_id}/years/{year_id}/teacher-availability -------
// -- PUT .../teacher-availability/{teacher_id} ----------------------------
//
// Mirrors the Owner Decision #38 Teacher Availability backend contract
// exactly (`docs/DECISIONS.md`, `api/schemas.py`'s
// `TeacherAvailabilityProjectionResponse`/`TeacherAvailabilityTeacherResponse`/
// `TeacherAvailabilityDayResponse`/`TeacherAvailabilityPeriodResponse`/
// `TeacherAvailabilityExceptionResponse`/`TeacherAvailabilityReplaceRequest`/
// `TeacherAvailabilityWriteResponse`). This is a dedicated *sparse
// exception* projection -- `TeacherAvailabilityExceptionStatus` below is
// deliberately narrower than the full three-state domain model: the
// dedicated GET's `exceptions` array and every PUT request body can only
// ever contain `PREFER_NOT`/`UNAVAILABLE` -- `AVAILABLE` is represented
// purely by a cell's absence, never an explicit member of either wire
// shape. `AvailabilityCellState` (the three-value union) exists
// separately, for the frontend's own *synthesized* UI cell state only --
// never sent over the wire.

export type TeacherAvailabilityExceptionStatus = "PREFER_NOT" | "UNAVAILABLE";

export type AvailabilityCellState = "AVAILABLE" | TeacherAvailabilityExceptionStatus;

export interface TeacherAvailabilityTeacher {
  id: string;
  name: string;
}

export interface TeacherAvailabilityDay {
  id: string;
  name: string;
  index: number;
}

export interface TeacherAvailabilityPeriod {
  id: string;
  name: string;
  index: number;
  block_id: string;
  is_instructional: boolean;
}

export interface TeacherAvailabilityException {
  teacher_id: string;
  day_id: string;
  period_id: string;
  status: TeacherAvailabilityExceptionStatus;
}

export interface TeacherAvailabilityProjectionResponse {
  configuration_locked: boolean;
  teachers: TeacherAvailabilityTeacher[];
  days: TeacherAvailabilityDay[];
  periods: TeacherAvailabilityPeriod[];
  exceptions: TeacherAvailabilityException[];
}

export interface TeacherAvailabilityExceptionRequestItem {
  day_id: string;
  period_id: string;
  status: TeacherAvailabilityExceptionStatus;
}

export interface TeacherAvailabilityReplaceRequest {
  exceptions: TeacherAvailabilityExceptionRequestItem[];
}

export interface TeacherAvailabilityWriteExceptionItem {
  day_id: string;
  period_id: string;
  status: TeacherAvailabilityExceptionStatus;
}

export interface TeacherAvailabilityWriteResponse {
  teacher_id: string;
  exceptions: TeacherAvailabilityWriteExceptionItem[];
}
