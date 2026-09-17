import { useEffect, useState } from "react";
import ClassSelector from "../components/ClassSelector";
import ClassTimetableEditor from "../components/ClassTimetableEditor";
import TeacherSelector from "../components/TeacherSelector";
import TeacherTimetableGrid from "../components/TeacherTimetableGrid";
import TeacherTimetableMatrix from "../components/TeacherTimetableMatrix";
import TimetableGrid from "../components/TimetableGrid";
import VersionHistoryPanel from "../components/VersionHistoryPanel";
import IncompatibleLocksDialog from "../components/IncompatibleLocksDialog";
import {
  ApiError,
  generateSchedule,
  getClassTimetable,
  getSchedulingConfigIndex,
  getTeacherTimetable,
} from "../api/client";
import { getConfigurationState } from "../api/configurationRevision";
import {
  getActiveTeacherTimetableMatrix,
  getTeacherTimetableMatrixForVersion,
} from "../api/teacherTimetableMatrix";
import {
  isConfigurationChangedDuringGenerationError,
  isIncompatibleLocksRequireConfirmationError,
  isInvalidConfigurationError,
  isNoConfigurationDraftError,
  isScheduleInfeasibleError,
  isStaleScheduleVersionError,
  regenerateActiveSchedule,
} from "../api/scheduleRegeneration";
import {
  getClassTimetableForVersion,
  getTeacherTimetableForVersion,
  restoreScheduleVersion,
} from "../api/scheduleVersions";
import type {
  ClassTimetableResponse,
  ConfigurationRevisionStateResponse,
  IncompatibleLock,
  SchedulingConfigIndexResponse,
  TeacherTimetableResponse,
  TeacherTimetableMatrixResponse,
  ValidationDiagnostic,
} from "../api/types";
import { AppConfigError, loadAppConfig } from "../config/appConfig";

/**
 * Phase 3B.3 (`docs/DECISIONS.md` #32): the first real browser class
 * timetable. Orchestrates, on mount: load the (pilot-fixed) app config
 * -> load the scheduling config index (for the class selector) ->
 * select the first backend-provided class -> load its live timetable.
 * No school/year selectors, no teacher timetable, no history/editing/
 * auth -- still out of scope.
 *
 * Phase 3C.3a: moved from `App.tsx` under the `/timetable` route with no
 * behavior change -- `App.tsx` now only wires up routing/the shared
 * shell (`components/AppShell.tsx`), which owns the page's former
 * outer `.app-shell` width/padding wrapper.
 *
 * The visual-correction follow-up removed this page's own "school —
 * year" subtitle line: `AppShell` now owns the ONE visible school/year
 * context line, shown once regardless of which page is active. The
 * config fetch itself stays here unchanged -- still needed for
 * `class_sections`/the loading/error/no-classes states -- only its
 * former display of `school.name`/`academic_year.label` was removed.
 *
 * Schedule-generation trigger (product-owner locked, no phase number
 * invented): a minimal Generate action living entirely in the Class
 * mode's "no-schedule" empty state -- the one missing step in the
 * school configuration -> generation -> timetable review pipeline that
 * was previously only reachable outside the browser. Reuses the
 * already-merged, no-request-body `POST .../schedule/generate`
 * (Decision #31) unchanged; on success (or a stale-browser
 * `SCHEDULE_ALREADY_EXISTS`) it re-runs the exact same authoritative
 * `getClassTimetable`/`getTeacherTimetable` fetches this page already
 * performs -- never a hand-built timetable from the generate response
 * body, never a second display path. State stays local (`generating`/
 * `generateError`/`generateDiagnostics`/`generationRefreshToken`); no
 * Redux/Zustand/query library, no coupling to `TeachingAssignmentsPage`
 * (it becomes locked purely from its own next `GET`).
 *
 * Teacher Timetable (next product slice after Phase 3C.3, no new phase
 * number): a `mode: "class" | "teacher"` switch, still ONE `/timetable`
 * route (no new route, no new top-nav destination) -- Class mode is
 * entirely unchanged, Teacher mode is a sibling read-only projection
 * using the exact same architecture (`getTeacherTimetable` ->
 * `TeacherTimetableGrid`, never React-side reconstruction). Both
 * modes' data fetch independently of which is currently visible (so
 * switching between them is instant, never a fresh network request),
 * and both refetch together whenever `generationRefreshToken` bumps,
 * since Generate is a school/year-wide action that affects both
 * projections identically. Generate itself stays Class-mode-only --
 * Teacher mode never duplicates it. `configState.config.teachers`
 * (sourced from the same single `/config` fetch this page already
 * makes) is the sole, authoritative teacher-selector source -- no
 * coupling to `TeachingAssignmentsPage`'s own separate `TeacherOption`
 * mirror.
 */

type AppConfigResult =
  | { ok: true; schoolId: string; academicYearId: string }
  | { ok: false; message: string };

type ConfigState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; config: SchedulingConfigIndexResponse };

type TimetableState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "loaded"; timetable: ClassTimetableResponse }
  | { status: "no-schedule" }
  | { status: "error"; message: string };

type TeacherTimetableState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "loaded"; timetable: TeacherTimetableResponse }
  | { status: "no-schedule" }
  | { status: "error"; message: string };

type TeacherMatrixState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "loaded"; matrix: TeacherTimetableMatrixResponse }
  | { status: "no-schedule" }
  | { status: "error"; message: string };

type TimetableMode = "class" | "teacher" | "teacher-matrix";

type ConfigurationState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; state: ConfigurationRevisionStateResponse };

/** Schedule version history + restore's own error mapping -- mirrors
 * `ClassTimetableEditor.tsx`'s `describeEditingError` style: a safe,
 * specific message per structured backend code, never a raw JSON dump. */
function describeRestoreError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Something went wrong. Please try again.";
  }
  if (error.code === "VERSION_ALREADY_ACTIVE") {
    return "This version is already the active one -- there is nothing to restore.";
  }
  if (error.code === "SCHEDULE_VERSION_NOT_FOUND") {
    return "That historical version could not be found.";
  }
  if (error.code === "RESTORE_VERIFICATION_FAILED") {
    return "This historical version could not be restored against the current configuration.";
  }
  // STALE_SCHEDULE_VERSION is handled separately (its own notice, not a
  // generic restore error); 404s and any other/unexpected structured
  // error fall back to the backend's own safe `detail` string.
  return error.detail;
}

function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    // Known backend details (e.g. "Scheduling configuration not
    // found", "Class section not found") are already safe, user-facing
    // text -- shown verbatim. Anything else falls back to a generic
    // message; never a raw Response/stack trace.
    return error.detail;
  }
  return "Something went wrong. Please try again.";
}

function isValidationDiagnostic(value: unknown): value is ValidationDiagnostic {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Record<string, unknown>)["code"] === "string" &&
    typeof (value as Record<string, unknown>)["message"] === "string"
  );
}

function ConfigurationStateLoadNotice({
  configurationState,
  onRetry,
}: {
  configurationState: ConfigurationState;
  onRetry: () => void;
}) {
  if (configurationState.status !== "error") {
    return null;
  }
  return (
    <div className="stale-banner" role="alert">
      <p>Configuration state could not be loaded. The timetable remains available.</p>
      <p>{configurationState.message}</p>
      <button type="button" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

function ActiveConfigurationNotice({ state }: { state: ConfigurationRevisionStateResponse }) {
  if (state.timetable_out_of_date) {
    return (
      <div className="stale-banner" role="alert">
        <p>Configuration changes are waiting to be applied.</p>
        <p>
          This active timetable was generated from the previously published configuration and remains usable. Regeneration is required to create a new timetable version.
        </p>
      </div>
    );
  }
  if (state.draft_revision_number !== null) {
    return <p className="timetable-current-state">Current timetable; an editable draft is open with no pending changes.</p>;
  }
  return <p className="timetable-current-state">Current timetable matches the published configuration.</p>;
}

/** Maps a `POST .../schedule/generate` failure to a safe inline
 * message plus, for `INVALID_CONFIGURATION`, the structured diagnostic
 * list -- reusing `ApiError.code`/`.body` (3C.3b) exactly, no new
 * error-DTO types. `SCHEDULE_ALREADY_EXISTS` is deliberately NOT
 * handled here: the caller treats it as a stale-browser state, not a
 * failure to describe. An unrecognized/malformed diagnostic shape is
 * filtered out rather than rendered or thrown -- fails safe. */
function describeGenerationError(error: unknown): { message: string; diagnostics: ValidationDiagnostic[] } {
  if (!(error instanceof ApiError)) {
    return { message: "Something went wrong. Please try again.", diagnostics: [] };
  }
  if (error.code === "CONFIGURATION_CHANGED_DURING_GENERATION") {
    return { message: "Scheduling configuration changed during generation. Please try again.", diagnostics: [] };
  }
  if (error.code === "INVALID_CONFIGURATION") {
    const rawErrors = error.body?.["errors"];
    const diagnostics = Array.isArray(rawErrors) ? rawErrors.filter(isValidationDiagnostic) : [];
    return { message: error.detail, diagnostics };
  }
  // SCHEDULE_INFEASIBLE, the 404 "Scheduling configuration not found",
  // and any other/unexpected structured error all fall back to the
  // backend's own safe `detail` string -- never a raw JSON dump.
  return { message: error.detail, diagnostics: [] };
}

function describeRegenerationError(error: unknown): { message: string; diagnostics: ValidationDiagnostic[] } {
  if (isStaleScheduleVersionError(error)) {
    return {
      message: "The active timetable changed while regeneration was being prepared. The latest timetable has been loaded. Review it before trying again.",
      diagnostics: [],
    };
  }
  if (isConfigurationChangedDuringGenerationError(error)) {
    return {
      message: "The configuration changed while regeneration was running. The latest state has been loaded. Review it before trying again.",
      diagnostics: [],
    };
  }
  if (isNoConfigurationDraftError(error)) {
    return {
      message: "There is no editable configuration draft to regenerate. The latest configuration state has been loaded.",
      diagnostics: [],
    };
  }
  if (isScheduleInfeasibleError(error)) {
    return { message: "No feasible timetable could be generated from the current draft.", diagnostics: [] };
  }
  if (isInvalidConfigurationError(error)) {
    const rawErrors = error.body?.["errors"];
    const diagnostics = Array.isArray(rawErrors) ? rawErrors.filter(isValidationDiagnostic) : [];
    return { message: error.detail, diagnostics };
  }
  return { message: error instanceof ApiError ? error.detail : "Something went wrong. Please try again.", diagnostics: [] };
}

function TimetablePage() {
  const [appConfigResult] = useState<AppConfigResult>(() => {
    try {
      const config = loadAppConfig();
      return { ok: true, schoolId: config.schoolId, academicYearId: config.academicYearId };
    } catch (error) {
      return {
        ok: false,
        message: error instanceof AppConfigError ? error.message : "Frontend configuration is invalid.",
      };
    }
  });

  const [configState, setConfigState] = useState<ConfigState>({ status: "loading" });
  const [configurationState, setConfigurationState] = useState<ConfigurationState>({ status: "loading" });
  const [selectedClassId, setSelectedClassId] = useState<string>("");
  const [timetableState, setTimetableState] = useState<TimetableState>({ status: "idle" });

  const [mode, setMode] = useState<TimetableMode>("class");
  const [selectedTeacherId, setSelectedTeacherId] = useState<string>("");
  const [teacherTimetableState, setTeacherTimetableState] = useState<TeacherTimetableState>({ status: "idle" });
  const [teacherMatrixState, setTeacherMatrixState] = useState<TeacherMatrixState>({ status: "idle" });

  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [generateDiagnostics, setGenerateDiagnostics] = useState<ValidationDiagnostic[]>([]);
  // Bumped on a successful (or stale-browser "already exists") generate
  // so the existing timetable-fetch effect below re-runs -- the sole
  // mechanism by which a generated schedule becomes visible; never a
  // second, hand-built display path.
  const [generationRefreshToken, setGenerationRefreshToken] = useState(0);
  const [configurationRefreshToken, setConfigurationRefreshToken] = useState(0);
  const [regenerating, setRegenerating] = useState(false);
  const [regenerationError, setRegenerationError] = useState<string | null>(null);
  const [regenerationDiagnostics, setRegenerationDiagnostics] = useState<ValidationDiagnostic[]>([]);
  const [incompatibleLocksForReview, setIncompatibleLocksForReview] = useState<IncompatibleLock[]>([]);
  const [incompatibleLocksBaseVersion, setIncompatibleLocksBaseVersion] = useState<number | null>(null);
  const [regenerationSuccessMessage, setRegenerationSuccessMessage] = useState<string | null>(null);

  // Schedule version history + restore. `viewingVersionNumber === null`
  // means "the current active version" -- the ordinary, pre-existing
  // behavior, completely unchanged. A non-null value switches BOTH the
  // class and teacher timetable-fetch effects below to the historical
  // per-version projection endpoints instead of the live ones, for
  // whichever class/teacher is currently selected -- the class/teacher
  // selectors keep working exactly as before while viewing history.
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyRefreshToken, setHistoryRefreshToken] = useState(0);
  const [viewingVersionNumber, setViewingVersionNumber] = useState<number | null>(null);
  // The live active version's own number -- captured from whichever
  // live fetch (class or teacher) last succeeded, never from a
  // historical fetch. This is the `base_version_number` a restore
  // command needs, and what decides whether "Restore this version" is
  // even offered for the version currently being viewed.
  const [activeVersionNumber, setActiveVersionNumber] = useState<number | null>(null);
  const [restoreConfirming, setRestoreConfirming] = useState(false);
  const [restoreBusy, setRestoreBusy] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [restoreSuccessMessage, setRestoreSuccessMessage] = useState<string | null>(null);
  const [versionStaleNotice, setVersionStaleNotice] = useState<string | null>(null);

  function refreshAuthoritativeScheduleState() {
    setGenerationRefreshToken((token) => token + 1);
    setConfigurationRefreshToken((token) => token + 1);
    setHistoryRefreshToken((token) => token + 1);
  }

  // Load the scheduling config index (for the class selector) once.
  useEffect(() => {
    if (!appConfigResult.ok) {
      return;
    }
    const controller = new AbortController();

    getSchedulingConfigIndex(appConfigResult.schoolId, appConfigResult.academicYearId, controller.signal)
      .then((index) => {
        if (controller.signal.aborted) {
          return;
        }
        setConfigState({ status: "ready", config: index });
        const firstClass = index.class_sections[0];
        if (firstClass !== undefined) {
          setSelectedClassId(firstClass.id);
        }
        const firstTeacher = index.teachers[0];
        if (firstTeacher !== undefined) {
          setSelectedTeacherId(firstTeacher.id);
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setConfigState({ status: "error", message: describeApiError(error) });
      });

    return () => {
      controller.abort();
    };
  }, [appConfigResult]);

  // Configuration lifecycle is authoritative for whether the active
  // timetable is current. This read is deliberately independent from the
  // `/config` index and timetable projections: failure here never hides a
  // usable timetable. The existing generation/mutation refresh token also
  // refreshes this state after Generate and Restore.
  useEffect(() => {
    if (!appConfigResult.ok) {
      return;
    }
    const controller = new AbortController();
    setConfigurationState({ status: "loading" });
    getConfigurationState(appConfigResult.schoolId, appConfigResult.academicYearId, controller.signal)
      .then((state) => {
        if (!controller.signal.aborted) {
          setConfigurationState({ status: "ready", state });
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setConfigurationState({ status: "error", message: describeApiError(error) });
        }
      });
    return () => controller.abort();
  }, [appConfigResult, configurationRefreshToken]);

  // Load the selected class's live timetable. Re-runs whenever the
  // selected class changes, or `generationRefreshToken` bumps (the
  // sole re-fetch trigger after a successful/stale-already-exists
  // generate -- see `handleGenerateClick` below); the previous request
  // is aborted first (via this effect's own cleanup), so a
  // stale/aborted response can never overwrite a newer selection -- the
  // timetable is cleared to "loading" immediately rather than left
  // showing the old class.
  useEffect(() => {
    if (!appConfigResult.ok || configState.status !== "ready" || selectedClassId === "") {
      return;
    }
    const controller = new AbortController();
    setTimetableState({ status: "loading" });

    const request =
      viewingVersionNumber === null
        ? getClassTimetable(appConfigResult.schoolId, appConfigResult.academicYearId, selectedClassId, controller.signal)
        : getClassTimetableForVersion(
            appConfigResult.schoolId,
            appConfigResult.academicYearId,
            viewingVersionNumber,
            selectedClassId,
            controller.signal,
          );

    request
      .then((timetable) => {
        // Guard the success path too, not just rejections: a superseded
        // request must never overwrite a newer selection's state, even
        // if whatever actually issued the request (a mock in tests, a
        // cache, etc.) doesn't itself reject on abort the way a real
        // `fetch` does.
        if (controller.signal.aborted) {
          return;
        }
        setTimetableState({ status: "loaded", timetable });
        if (viewingVersionNumber === null) {
          setActiveVersionNumber(timetable.version_number);
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        if (error instanceof ApiError && error.detail === "Active schedule not found") {
          setTimetableState({ status: "no-schedule" });
          return;
        }
        if (error instanceof ApiError && error.code === "SCHEDULE_VERSION_NOT_FOUND") {
          setViewingVersionNumber(null);
          return;
        }
        setTimetableState({ status: "error", message: describeApiError(error) });
      });

    return () => {
      controller.abort();
    };
  }, [appConfigResult, configState.status, selectedClassId, generationRefreshToken, viewingVersionNumber]);

  // Load the selected teacher's live timetable -- the Teacher mode
  // sibling of the class-timetable effect above, kept as a fully
  // independent fetch/state machine (its own `AbortController`, its
  // own cleanup) so a rapid Teacher A -> Teacher B switch can never let
  // Teacher A's late response overwrite Teacher B's, exactly mirroring
  // the class-switch race guard. Runs regardless of which `mode` is
  // currently visible -- both projections stay loaded together, so
  // switching modes is instant, never a fresh network request -- and
  // re-runs on the same `generationRefreshToken` bump, since Generate
  // is a school/year-wide action affecting both projections identically.
  useEffect(() => {
    if (!appConfigResult.ok || configState.status !== "ready" || selectedTeacherId === "") {
      return;
    }
    const controller = new AbortController();
    setTeacherTimetableState({ status: "loading" });

    const request =
      viewingVersionNumber === null
        ? getTeacherTimetable(appConfigResult.schoolId, appConfigResult.academicYearId, selectedTeacherId, controller.signal)
        : getTeacherTimetableForVersion(
            appConfigResult.schoolId,
            appConfigResult.academicYearId,
            viewingVersionNumber,
            selectedTeacherId,
            controller.signal,
          );

    request
      .then((timetable) => {
        if (controller.signal.aborted) {
          return;
        }
        setTeacherTimetableState({ status: "loaded", timetable });
        if (viewingVersionNumber === null) {
          setActiveVersionNumber(timetable.version_number);
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        if (error instanceof ApiError && error.detail === "Active schedule not found") {
          setTeacherTimetableState({ status: "no-schedule" });
          return;
        }
        if (error instanceof ApiError && error.code === "SCHEDULE_VERSION_NOT_FOUND") {
          setViewingVersionNumber(null);
          return;
        }
        setTeacherTimetableState({ status: "error", message: describeApiError(error) });
      });

    return () => {
      controller.abort();
    };
  }, [appConfigResult, configState.status, selectedTeacherId, generationRefreshToken, viewingVersionNumber]);

  // The whole-school Matrix is intentionally lazy: unlike the existing
  // selected Class/Teacher projections, it is fetched only while its own
  // subview is visible. Mode/version/refresh changes abort the obsolete
  // request, and both success and failure paths check that signal before
  // changing visible state.
  useEffect(() => {
    if (!appConfigResult.ok || configState.status !== "ready" || mode !== "teacher-matrix") {
      return;
    }
    const controller = new AbortController();
    setTeacherMatrixState({ status: "loading" });
    const request = viewingVersionNumber === null
      ? getActiveTeacherTimetableMatrix(
          appConfigResult.schoolId, appConfigResult.academicYearId, controller.signal,
        )
      : getTeacherTimetableMatrixForVersion(
          appConfigResult.schoolId,
          appConfigResult.academicYearId,
          viewingVersionNumber,
          controller.signal,
        );

    request
      .then((matrix) => {
        if (controller.signal.aborted) {
          return;
        }
        setTeacherMatrixState({ status: "loaded", matrix });
        if (viewingVersionNumber === null) {
          setActiveVersionNumber(matrix.version_number);
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        if (error instanceof ApiError && error.detail === "Active schedule not found") {
          setTeacherMatrixState({ status: "no-schedule" });
          return;
        }
        if (error instanceof ApiError && error.code === "SCHEDULE_VERSION_NOT_FOUND") {
          setViewingVersionNumber(null);
          return;
        }
        setTeacherMatrixState({ status: "error", message: describeApiError(error) });
      });

    return () => controller.abort();
  }, [appConfigResult, configState.status, mode, generationRefreshToken, viewingVersionNumber]);

  async function handleGenerateClick() {
    if (!appConfigResult.ok || generating) {
      return;
    }
    setGenerating(true);
    setGenerateError(null);
    setGenerateDiagnostics([]);
    try {
      await generateSchedule(appConfigResult.schoolId, appConfigResult.academicYearId);
      setGenerationRefreshToken((token) => token + 1);
      setConfigurationRefreshToken((token) => token + 1);
    } catch (error) {
      if (error instanceof ApiError && error.code === "SCHEDULE_ALREADY_EXISTS") {
        // Stale browser state, not a generation failure -- someone/
        // something else already generated one. Show the real current
        // state via the same authoritative re-fetch a real success
        // uses, never a scary error and never a second POST.
        setGenerationRefreshToken((token) => token + 1);
        setConfigurationRefreshToken((token) => token + 1);
      } else {
        const { message, diagnostics } = describeGenerationError(error);
        setGenerateError(message);
        setGenerateDiagnostics(diagnostics);
      }
    } finally {
      setGenerating(false);
    }
  }

  async function handleRegenerateClick() {
    if (
      !appConfigResult.ok ||
      regenerating ||
      viewingVersionNumber !== null ||
      activeVersionNumber === null ||
      configurationState.status !== "ready" ||
      !configurationState.state.timetable_out_of_date
    ) {
      return;
    }
    setRegenerating(true);
    setRegenerationError(null);
    setRegenerationDiagnostics([]);
    setIncompatibleLocksForReview([]);
    setRegenerationSuccessMessage(null);
    const baseVersionNumber = activeVersionNumber;
    try {
      const result = await regenerateActiveSchedule(appConfigResult.schoolId, appConfigResult.academicYearId, {
        base_version_number: baseVersionNumber,
        confirmed_incompatible_lock_keys: [],
      });
      setRegenerationSuccessMessage(`Timetable regenerated. Version ${result.version_number} is now active.`);
      refreshAuthoritativeScheduleState();
    } catch (error) {
      if (isIncompatibleLocksRequireConfirmationError(error)) {
        setIncompatibleLocksForReview(error.body.incompatible_locks);
        setIncompatibleLocksBaseVersion(baseVersionNumber);
        setRegenerationError(
          "Some locked lessons cannot be retained with the changed configuration and require review before regeneration can continue.",
        );
      } else {
        const { message, diagnostics } = describeRegenerationError(error);
        setRegenerationError(message);
        setRegenerationDiagnostics(diagnostics);
        if (
          isStaleScheduleVersionError(error) ||
          isConfigurationChangedDuringGenerationError(error) ||
          isNoConfigurationDraftError(error)
        ) {
          refreshAuthoritativeScheduleState();
        }
      }
    } finally {
      setRegenerating(false);
    }
  }

  function clearIncompatibleLockReview() {
    setIncompatibleLocksForReview([]);
    setIncompatibleLocksBaseVersion(null);
  }

  async function handleConfirmIncompatibleLocks() {
    if (
      !appConfigResult.ok ||
      regenerating ||
      incompatibleLocksBaseVersion === null ||
      incompatibleLocksForReview.length === 0 ||
      viewingVersionNumber !== null
    ) {
      return;
    }
    setRegenerating(true);
    setRegenerationError(null);
    setRegenerationDiagnostics([]);
    try {
      const result = await regenerateActiveSchedule(appConfigResult.schoolId, appConfigResult.academicYearId, {
        base_version_number: incompatibleLocksBaseVersion,
        confirmed_incompatible_lock_keys: incompatibleLocksForReview.map(
          ({ requirement_id, day_id, anchor_period_id }) => ({ requirement_id, day_id, anchor_period_id }),
        ),
      });
      clearIncompatibleLockReview();
      setRegenerationSuccessMessage(`Timetable regenerated. Version ${result.version_number} is now active.`);
      refreshAuthoritativeScheduleState();
    } catch (error) {
      if (isIncompatibleLocksRequireConfirmationError(error)) {
        setIncompatibleLocksForReview(error.body.incompatible_locks);
        setRegenerationError(
          "The incompatible lock set changed. Review the updated list before confirming again.",
        );
      } else {
        clearIncompatibleLockReview();
        const { message, diagnostics } = describeRegenerationError(error);
        setRegenerationError(message);
        setRegenerationDiagnostics(diagnostics);
        if (
          isStaleScheduleVersionError(error) ||
          isConfigurationChangedDuringGenerationError(error) ||
          isNoConfigurationDraftError(error)
        ) {
          refreshAuthoritativeScheduleState();
        }
      }
    } finally {
      setRegenerating(false);
    }
  }

  function handleSelectVersion(versionNumber: number) {
    clearIncompatibleLockReview();
    setViewingVersionNumber(versionNumber);
    setRestoreConfirming(false);
    setRestoreError(null);
  }

  function handleBackToCurrentVersion() {
    clearIncompatibleLockReview();
    setViewingVersionNumber(null);
    setRestoreConfirming(false);
    setRestoreError(null);
  }

  async function handleConfirmRestore() {
    if (!appConfigResult.ok || viewingVersionNumber === null || activeVersionNumber === null || restoreBusy) {
      return;
    }
    setRestoreBusy(true);
    setRestoreError(null);
    const sourceVersionNumber = viewingVersionNumber;
    try {
      const result = await restoreScheduleVersion(appConfigResult.schoolId, appConfigResult.academicYearId, sourceVersionNumber, {
        base_version_number: activeVersionNumber,
      });
      setRestoreConfirming(false);
      setViewingVersionNumber(null); // back to current -- current now IS the restored copy
      setRestoreSuccessMessage(`Version ${sourceVersionNumber} was restored as new Version ${result.version_number}.`);
      setHistoryRefreshToken((token) => token + 1);
      setGenerationRefreshToken((token) => token + 1);
      setConfigurationRefreshToken((token) => token + 1);
    } catch (error) {
      if (error instanceof ApiError && error.code === "STALE_SCHEDULE_VERSION") {
        setVersionStaleNotice(
          "The active timetable changed since this page loaded. Review the current version, then try restoring again.",
        );
        setRestoreConfirming(false);
        setHistoryRefreshToken((token) => token + 1);
        return;
      }
      setRestoreConfirming(false);
      if (error instanceof ApiError && error.code === "SCHEDULE_VERSION_NOT_FOUND") {
        setViewingVersionNumber(null);
      }
      setRestoreError(describeRestoreError(error));
    } finally {
      setRestoreBusy(false);
    }
  }

  return (
    <>
      <h1>Timetable</h1>

      {!appConfigResult.ok && <p role="alert">{appConfigResult.message}</p>}

      {appConfigResult.ok && configState.status === "loading" && <p>Loading configuration…</p>}
      {appConfigResult.ok && configState.status === "error" && <p role="alert">{configState.message}</p>}

      {appConfigResult.ok && configState.status === "ready" && (
        <>
          <ConfigurationStateLoadNotice
            configurationState={configurationState}
            onRetry={() => setConfigurationRefreshToken((token) => token + 1)}
          />
          {regenerationSuccessMessage !== null && (
            <div className="version-restore-success" role="status">
              <p>{regenerationSuccessMessage}</p>
            </div>
          )}
          {regenerationError !== null && (
            <div className="stale-banner" role="alert">
              <p>{regenerationError}</p>
              {incompatibleLocksForReview.length > 0 && (
                <p>{incompatibleLocksForReview.length} locked lesson(s) need review before regeneration can continue.</p>
              )}
              {regenerationDiagnostics.length > 0 && (
                <ul className="generate-diagnostics">
                  {regenerationDiagnostics.map((diagnostic, index) => (
                    <li key={`${diagnostic.code}-${index}`}>{diagnostic.message}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {incompatibleLocksForReview.length > 0 && incompatibleLocksBaseVersion !== null && (
            <IncompatibleLocksDialog
              locks={incompatibleLocksForReview}
              submitting={regenerating}
              onCancel={() => {
                if (!regenerating) {
                  clearIncompatibleLockReview();
                  setRegenerationError(null);
                }
              }}
              onConfirm={handleConfirmIncompatibleLocks}
            />
          )}
          <div className="timetable-mode-switch" role="group" aria-label="Timetable view">
            <button
              type="button"
              className="mode-switch-button"
              aria-pressed={mode === "class"}
              onClick={() => setMode("class")}
            >
              Class
            </button>
            <button
              type="button"
              className="mode-switch-button"
              aria-pressed={mode === "teacher"}
              onClick={() => setMode("teacher")}
            >
              Teacher
            </button>
            <button
              type="button"
              className="mode-switch-button"
              aria-pressed={mode === "teacher-matrix"}
              onClick={() => setMode("teacher-matrix")}
            >
              Teacher Matrix
            </button>
            <button
              type="button"
              className="action-button version-history-toggle"
              aria-pressed={historyOpen}
              onClick={() => {
                setHistoryOpen((open) => !open);
                setRestoreSuccessMessage(null);
              }}
            >
              Version History
            </button>
          </div>

          {restoreSuccessMessage !== null && (
            <div className="version-restore-success" role="status">
              <p>{restoreSuccessMessage}</p>
            </div>
          )}
          {versionStaleNotice !== null && (
            <div className="stale-banner" role="alert">
              <p>{versionStaleNotice}</p>
            </div>
          )}

          {historyOpen && (
            <VersionHistoryPanel
              schoolId={appConfigResult.schoolId}
              academicYearId={appConfigResult.academicYearId}
              refreshToken={historyRefreshToken}
              viewingVersionNumber={viewingVersionNumber}
              onSelectVersion={handleSelectVersion}
            />
          )}

          {viewingVersionNumber !== null && (
            <div className="historical-version-banner" role="region" aria-label="Historical version view">
              <p>
                Viewing historical <strong>Version {viewingVersionNumber}</strong> — read only
              </p>
              <span className="lesson-edit-panel-actions">
                <button type="button" onClick={handleBackToCurrentVersion} disabled={restoreBusy}>
                  Back to current version
                </button>
                {activeVersionNumber !== null && viewingVersionNumber !== activeVersionNumber && !restoreConfirming && (
                  <button
                    type="button"
                    className="action-button"
                    onClick={() => {
                      setRestoreConfirming(true);
                      setRestoreError(null);
                    }}
                    disabled={restoreBusy}
                  >
                    Restore this version
                  </button>
                )}
              </span>
              {restoreConfirming && (
                <div className="reoptimize-confirm">
                  <p>
                    This will not delete newer versions. A new version will be created from Version{" "}
                    {viewingVersionNumber} and made active.
                  </p>
                  <span className="reoptimize-confirm-actions">
                    <button type="button" className="btn-primary" onClick={handleConfirmRestore} disabled={restoreBusy}>
                      {restoreBusy ? "Restoring…" : `Restore Version ${viewingVersionNumber}`}
                    </button>
                    <button type="button" onClick={() => setRestoreConfirming(false)} disabled={restoreBusy}>
                      Cancel
                    </button>
                  </span>
                </div>
              )}
              {restoreError !== null && (
                <div role="alert" className="lesson-edit-panel-error">
                  <p>{restoreError}</p>
                </div>
              )}
            </div>
          )}

          {mode === "class" ? (
            configState.config.class_sections.length === 0 ? (
              <p>No classes are configured for this school/year yet.</p>
            ) : (
              <>
                <ClassSelector
                  classSections={configState.config.class_sections}
                  selectedClassId={selectedClassId}
                  onChange={setSelectedClassId}
                />

                {timetableState.status === "loading" && <p>Loading timetable…</p>}
                {timetableState.status === "no-schedule" && (
                  <div className="generate-action">
                    <p>No schedule has been generated yet for this class.</p>
                    <button type="button" className="btn-primary" onClick={handleGenerateClick} disabled={generating}>
                      {generating ? "Generating…" : "Generate schedule"}
                    </button>
                    {generateError !== null && (
                      <div role="alert" className="generate-error">
                        <p>{generateError}</p>
                        {generateDiagnostics.length > 0 && (
                          <ul className="generate-diagnostics">
                            {generateDiagnostics.map((diagnostic, index) => (
                              <li key={`${diagnostic.code}-${index}`}>{diagnostic.message}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}
                  </div>
                )}
                {timetableState.status === "error" && <p role="alert">{timetableState.message}</p>}
                {timetableState.status === "loaded" && appConfigResult.ok && (
                  <>
                    <p className="timetable-meta">
                      {timetableState.timetable.class_section_name} · Version{" "}
                      {timetableState.timetable.version_number}
                    </p>
                    {viewingVersionNumber === null ? (
                      <>
                        {configurationState.status === "ready" && (
                          <ActiveConfigurationNotice state={configurationState.state} />
                        )}
                        {configurationState.status === "ready" && configurationState.state.timetable_out_of_date && (
                          <button type="button" className="btn-primary" onClick={handleRegenerateClick} disabled={regenerating}>
                            {regenerating ? "Regenerating…" : "Regenerate timetable"}
                          </button>
                        )}
                        {configurationState.status === "ready" && configurationState.state.timetable_out_of_date ? (
                          <>
                            <p className="stale-banner" role="status">
                              Timetable editing is paused until the configuration changes are regenerated.
                            </p>
                            <TimetableGrid timetable={timetableState.timetable} />
                          </>
                        ) : (
                          <ClassTimetableEditor
                            schoolId={appConfigResult.schoolId}
                            academicYearId={appConfigResult.academicYearId}
                            timetable={timetableState.timetable}
                            onMutationSuccess={() => setGenerationRefreshToken((token) => token + 1)}
                          />
                        )}
                      </>
                    ) : (
                      // Historical version: read-only, exactly like
                      // `TimetableGrid` already renders when no
                      // `editing` bundle is supplied -- Move/Lock/
                      // Unlock/Re-optimize/preview are never available
                      // for an immutable past version.
                      <TimetableGrid timetable={timetableState.timetable} />
                    )}
                  </>
                )}
              </>
            )
          ) : mode === "teacher" ? configState.config.teachers.length === 0 ? (
            <p>No teachers are configured for this school/year yet.</p>
          ) : (
            <>
              <TeacherSelector
                teachers={configState.config.teachers}
                selectedTeacherId={selectedTeacherId}
                onChange={setSelectedTeacherId}
              />

              {teacherTimetableState.status === "loading" && <p>Loading timetable…</p>}
              {teacherTimetableState.status === "no-schedule" && <p>No schedule has been generated yet.</p>}
              {teacherTimetableState.status === "error" && <p role="alert">{teacherTimetableState.message}</p>}
              {teacherTimetableState.status === "loaded" && (
                <>
                  <p className="timetable-meta">
                    {teacherTimetableState.timetable.teacher_name} · Version{" "}
                    {teacherTimetableState.timetable.version_number}
                  </p>
                  {viewingVersionNumber === null && configurationState.status === "ready" && (
                    <ActiveConfigurationNotice state={configurationState.state} />
                  )}
                  {viewingVersionNumber === null && configurationState.status === "ready" && configurationState.state.timetable_out_of_date && (
                    <button type="button" className="btn-primary" onClick={handleRegenerateClick} disabled={regenerating}>
                      {regenerating ? "Regenerating…" : "Regenerate timetable"}
                    </button>
                  )}
                  <TeacherTimetableGrid timetable={teacherTimetableState.timetable} />
                </>
              )}
            </>
          ) : (
            <>
              {teacherMatrixState.status === "idle" || teacherMatrixState.status === "loading" ? (
                <p role="status">Loading teacher matrix…</p>
              ) : null}
              {teacherMatrixState.status === "no-schedule" && (
                <p>No schedule has been generated yet.</p>
              )}
              {teacherMatrixState.status === "error" && (
                <p role="alert">{teacherMatrixState.message}</p>
              )}
              {teacherMatrixState.status === "loaded" && (
                <>
                  <p className="timetable-meta">
                    Teacher Matrix · Version {teacherMatrixState.matrix.version_number}
                  </p>
                  <TeacherTimetableMatrix matrix={teacherMatrixState.matrix} />
                </>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}

export default TimetablePage;
