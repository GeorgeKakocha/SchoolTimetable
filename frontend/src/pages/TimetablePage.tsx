import { useEffect, useState } from "react";
import ClassSelector from "../components/ClassSelector";
import ClassTimetableEditor from "../components/ClassTimetableEditor";
import TeacherSelector from "../components/TeacherSelector";
import TeacherTimetableGrid from "../components/TeacherTimetableGrid";
import TimetableGrid from "../components/TimetableGrid";
import VersionHistoryPanel from "../components/VersionHistoryPanel";
import {
  ApiError,
  generateSchedule,
  getClassTimetable,
  getSchedulingConfigIndex,
  getTeacherTimetable,
} from "../api/client";
import {
  getClassTimetableForVersion,
  getTeacherTimetableForVersion,
  restoreScheduleVersion,
} from "../api/scheduleVersions";
import type {
  ClassTimetableResponse,
  SchedulingConfigIndexResponse,
  TeacherTimetableResponse,
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

type TimetableMode = "class" | "teacher";

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
  const [selectedClassId, setSelectedClassId] = useState<string>("");
  const [timetableState, setTimetableState] = useState<TimetableState>({ status: "idle" });

  const [mode, setMode] = useState<TimetableMode>("class");
  const [selectedTeacherId, setSelectedTeacherId] = useState<string>("");
  const [teacherTimetableState, setTeacherTimetableState] = useState<TeacherTimetableState>({ status: "idle" });

  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [generateDiagnostics, setGenerateDiagnostics] = useState<ValidationDiagnostic[]>([]);
  // Bumped on a successful (or stale-browser "already exists") generate
  // so the existing timetable-fetch effect below re-runs -- the sole
  // mechanism by which a generated schedule becomes visible; never a
  // second, hand-built display path.
  const [generationRefreshToken, setGenerationRefreshToken] = useState(0);

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
    } catch (error) {
      if (error instanceof ApiError && error.code === "SCHEDULE_ALREADY_EXISTS") {
        // Stale browser state, not a generation failure -- someone/
        // something else already generated one. Show the real current
        // state via the same authoritative re-fetch a real success
        // uses, never a scary error and never a second POST.
        setGenerationRefreshToken((token) => token + 1);
      } else {
        const { message, diagnostics } = describeGenerationError(error);
        setGenerateError(message);
        setGenerateDiagnostics(diagnostics);
      }
    } finally {
      setGenerating(false);
    }
  }

  function handleSelectVersion(versionNumber: number) {
    setViewingVersionNumber(versionNumber);
    setRestoreConfirming(false);
    setRestoreError(null);
  }

  function handleBackToCurrentVersion() {
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
                      <ClassTimetableEditor
                        schoolId={appConfigResult.schoolId}
                        academicYearId={appConfigResult.academicYearId}
                        timetable={timetableState.timetable}
                        onMutationSuccess={() => setGenerationRefreshToken((token) => token + 1)}
                      />
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
          ) : configState.config.teachers.length === 0 ? (
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
                  <TeacherTimetableGrid timetable={teacherTimetableState.timetable} />
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
