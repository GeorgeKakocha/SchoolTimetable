import { useEffect, useState } from "react";
import { getScheduleVersionHistory } from "../api/scheduleVersions";
import { ApiError } from "../api/client";
import type { ScheduleVersionSummary } from "../api/types";

/**
 * Schedule version history + restore slice: a self-contained,
 * read-only version list -- fetches `GET .../schedule/versions` itself
 * (mirroring `ClassTimetableEditor`'s "owns its own API calls" style)
 * and reports only which version was clicked; `TimetablePage` owns
 * everything about what "viewing a historical version" actually does
 * (switching the class/teacher fetch, the read-only banner, restore).
 *
 * Newest-first order is never re-derived here -- it's the backend's own
 * `list_versions` guarantee, preserved verbatim.
 */

interface VersionHistoryPanelProps {
  schoolId: string;
  academicYearId: string;
  /** Bump to force a re-fetch (e.g. after a successful restore, or any
   * other mutation that may have changed the active version). */
  refreshToken: number;
  /** `null` means "viewing the current active version". */
  viewingVersionNumber: number | null;
  onSelectVersion: (versionNumber: number) => void;
}

type HistoryState =
  | { status: "loading" }
  | { status: "loaded"; versions: ScheduleVersionSummary[] }
  | { status: "error"; message: string };

function formatCreatedAt(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function VersionHistoryPanel({
  schoolId,
  academicYearId,
  refreshToken,
  viewingVersionNumber,
  onSelectVersion,
}: VersionHistoryPanelProps) {
  const [state, setState] = useState<HistoryState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    getScheduleVersionHistory(schoolId, academicYearId, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) {
          return;
        }
        setState({ status: "loaded", versions: response.versions });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        const message = error instanceof ApiError ? error.detail : "Could not load version history.";
        setState({ status: "error", message });
      });
    return () => {
      controller.abort();
    };
  }, [schoolId, academicYearId, refreshToken]);

  return (
    <div className="version-history-panel" role="region" aria-label="Version history">
      {state.status === "loading" && <p>Loading version history…</p>}
      {state.status === "error" && <p role="alert">{state.message}</p>}
      {state.status === "loaded" && (
        <ul className="version-history-list">
          {state.versions.map((version) => {
            const isCurrentlyViewed =
              viewingVersionNumber === null ? version.is_active : version.version_number === viewingVersionNumber;
            return (
              <li key={version.version_number}>
                <button
                  type="button"
                  className="version-history-row"
                  aria-current={isCurrentlyViewed ? "true" : undefined}
                  onClick={() => onSelectVersion(version.version_number)}
                >
                  <span className="version-history-row-heading">
                    Version {version.version_number}
                    {version.is_active && <span className="version-history-active-badge">ACTIVE</span>}
                  </span>
                  <span className="version-history-row-meta">
                    {formatCreatedAt(version.created_at)} · {version.solver_status} · Penalty{" "}
                    {version.total_soft_penalty}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default VersionHistoryPanel;
