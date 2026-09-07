import { useEffect, useState } from "react";
import ClassSelector from "./components/ClassSelector";
import TimetableGrid from "./components/TimetableGrid";
import { ApiError, getClassTimetable, getSchedulingConfigIndex } from "./api/client";
import type { ClassTimetableResponse, SchedulingConfigIndexResponse } from "./api/types";
import { AppConfigError, loadAppConfig } from "./config/appConfig";

/**
 * Phase 3B.3 (`docs/DECISIONS.md` #32): the first real browser class
 * timetable. Orchestrates, on mount: load the (pilot-fixed) app config
 * -> load the scheduling config index (for the class selector) ->
 * select the first backend-provided class -> load its live timetable.
 * No Generate button, no school/year selectors, no teacher timetable,
 * no history/editing/auth -- all explicitly out of Phase 3B.3 scope.
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

function App() {
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
  // selected class changes; the previous request is aborted first (via
  // this effect's own cleanup), so a stale/aborted response can never
  // overwrite a newer selection -- the timetable is cleared to
  // "loading" immediately rather than left showing the old class.
  useEffect(() => {
    if (!appConfigResult.ok || configState.status !== "ready" || selectedClassId === "") {
      return;
    }
    const controller = new AbortController();
    setTimetableState({ status: "loading" });

    getClassTimetable(appConfigResult.schoolId, appConfigResult.academicYearId, selectedClassId, controller.signal)
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
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        if (error instanceof ApiError && error.detail === "Active schedule not found") {
          setTimetableState({ status: "no-schedule" });
          return;
        }
        setTimetableState({ status: "error", message: describeApiError(error) });
      });

    return () => {
      controller.abort();
    };
  }, [appConfigResult, configState.status, selectedClassId]);

  return (
    <main className="app-shell">
      <h1>School Timetable</h1>

      {!appConfigResult.ok && <p role="alert">{appConfigResult.message}</p>}

      {appConfigResult.ok && configState.status === "loading" && <p>Loading configuration…</p>}
      {appConfigResult.ok && configState.status === "error" && <p role="alert">{configState.message}</p>}

      {appConfigResult.ok && configState.status === "ready" && (
        <>
          <p className="app-subtitle">
            {configState.config.school.name} — {configState.config.academic_year.label}
          </p>

          {configState.config.class_sections.length === 0 ? (
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
                <p>No schedule has been generated yet for this class.</p>
              )}
              {timetableState.status === "error" && <p role="alert">{timetableState.message}</p>}
              {timetableState.status === "loaded" && (
                <>
                  <p className="timetable-meta">
                    {timetableState.timetable.class_section_name} · Version{" "}
                    {timetableState.timetable.version_number}
                  </p>
                  <TimetableGrid timetable={timetableState.timetable} />
                </>
              )}
            </>
          )}
        </>
      )}
    </main>
  );
}

export default App;
