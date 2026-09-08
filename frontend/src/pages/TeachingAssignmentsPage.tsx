import { useCallback, useEffect, useState } from "react";
import { getTeachingAssignments } from "../api/teachingAssignments";
import { ApiError } from "../api/client";
import type { TeacherWorkload, TeachingAssignment, TeachingAssignmentsProjectionResponse } from "../api/types";
import { AppConfigError, loadAppConfig } from "../config/appConfig";

/**
 * Phase 3C.3a: `/configuration/teaching-assignments`, read-only. Loads
 * the dedicated Phase 3C.2b page projection
 * (`GET .../teaching-assignments`) directly -- never reconstructs
 * assignment/workload/target semantics from `/config` itself, matching
 * the locked design gate. Create/edit/delete affordances belong to
 * Phase 3C.3b; this slice renders no mutation controls at all, not even
 * disabled ones (nothing to preview yet).
 *
 * Visual-correction follow-up (based on a real manual-browser pass):
 * workload moved from a narrow single-column table to a wider
 * two-column row grid; the assignments table's columns are explicitly
 * width-balanced (`col-*` classes) so "Class / Group" no longer
 * dominates; the "Type" header/column is now "Configuration"; advanced
 * rows show small individual reason chips instead of one
 * comma-joined sentence; `SUBGROUP`/`MERGED_CLASSES` badges are styled
 * quieter than the "Advanced" status badge (target semantics, not a
 * warning). No data/contract change accompanies any of this.
 */

type PageState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; projection: TeachingAssignmentsProjectionResponse };

type AppConfigResult =
  | { ok: true; schoolId: string; academicYearId: string }
  | { ok: false; message: string };

/** Every `advanced_reasons` code `teaching_assignment_rules.plain_reasons`
 * (backend) can currently produce, mapped to a friendly label -- purely
 * presentational, never a change to backend semantics. An unrecognized
 * future code falls back to its raw form rather than breaking render. */
const ADVANCED_REASON_LABELS: Record<string, string> = {
  participant_group_role: "Non-whole-class target",
  split_group_id: "Split group",
  block_policy: "Block pattern",
  distribution_policy: "Distribution rule",
  time_preferences: "Preferred time",
  resource_requirement: "Resource requirement",
  fixed_placement: "Fixed placement",
};

function friendlyReason(code: string): string {
  return ADVANCED_REASON_LABELS[code] ?? code;
}

const GROUP_ROLE_LABELS: Record<string, string> = {
  SUBGROUP: "Subgroup",
  MERGED_CLASSES: "Merged classes",
};

function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  return "Something went wrong. Please try again.";
}

function TeachingAssignmentsPage() {
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

  const [pageState, setPageState] = useState<PageState>({ status: "loading" });
  const [retryToken, setRetryToken] = useState(0);

  useEffect(() => {
    if (!appConfigResult.ok) {
      return;
    }
    const controller = new AbortController();
    setPageState({ status: "loading" });

    getTeachingAssignments(appConfigResult.schoolId, appConfigResult.academicYearId, controller.signal)
      .then((projection) => {
        if (controller.signal.aborted) {
          return;
        }
        setPageState({ status: "ready", projection });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setPageState({ status: "error", message: describeApiError(error) });
      });

    return () => {
      controller.abort();
    };
  }, [appConfigResult, retryToken]);

  const handleRetry = useCallback(() => {
    setRetryToken((token) => token + 1);
  }, []);

  return (
    <div className="assignments-page">
      <h1>Teaching Assignments</h1>
      <p className="page-subtitle">
        Which teacher teaches which class, in which activity, for how many periods a week.
      </p>

      {!appConfigResult.ok && <p role="alert">{appConfigResult.message}</p>}

      {appConfigResult.ok && pageState.status === "loading" && <p>Loading teaching assignments…</p>}

      {appConfigResult.ok && pageState.status === "error" && (
        <div role="alert" className="page-error">
          <p>{pageState.message}</p>
          <button type="button" onClick={handleRetry}>
            Retry
          </button>
        </div>
      )}

      {appConfigResult.ok && pageState.status === "ready" && (
        <TeachingAssignmentsContent projection={pageState.projection} />
      )}
    </div>
  );
}

function TeachingAssignmentsContent({ projection }: { projection: TeachingAssignmentsProjectionResponse }) {
  return (
    <>
      {projection.configuration_locked && (
        <div className="lock-banner">
          Assignments are read-only because a schedule has already been generated for this configuration.
        </div>
      )}

      <section className="page-section" aria-labelledby="workload-heading">
        <h2 id="workload-heading">Teacher workload</h2>
        <p className="section-caption">Weekly periods assigned per teacher.</p>
        <WorkloadOverview workloads={projection.teacher_workloads} />
      </section>

      <section className="page-section" aria-labelledby="assignments-heading">
        <h2 id="assignments-heading">Assignments</h2>
        {projection.assignments.length === 0 ? (
          <p>No teaching assignments configured yet.</p>
        ) : (
          <AssignmentsTable assignments={projection.assignments} />
        )}
      </section>
    </>
  );
}

/** A compact, responsive two-column row grid (not a table, not cards) --
 * uses the available horizontal width instead of leaving a narrow
 * left-aligned block; collapses to one column at narrow widths via a
 * plain media query. Every teacher renders, including zero-period
 * ones; no invented target/remaining/capacity presentation. */
function WorkloadOverview({ workloads }: { workloads: TeacherWorkload[] }) {
  return (
    <div className="workload-grid">
      {workloads.map((workload) => (
        <div className="workload-row" key={workload.teacher_id}>
          <span className="workload-row-name">{workload.teacher_name}</span>
          <span className="workload-row-periods">{workload.total_weekly_periods}</span>
        </div>
      ))}
    </div>
  );
}

function AssignmentsTable({ assignments }: { assignments: TeachingAssignment[] }) {
  return (
    <div className="table-scroll">
      <table className="assignments-table">
        <colgroup>
          <col className="col-teacher" />
          <col className="col-group" />
          <col className="col-activity" />
          <col className="col-periods" />
          <col className="col-configuration" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">Teacher</th>
            <th scope="col">Class / Group</th>
            <th scope="col">Activity</th>
            <th scope="col" className="numeric-cell">
              Weekly periods
            </th>
            <th scope="col">Configuration</th>
          </tr>
        </thead>
        <tbody>
          {assignments.map((assignment) => (
            <tr key={assignment.id}>
              <th scope="row">{assignment.teacher_name}</th>
              <td>
                <GroupCell assignment={assignment} />
              </td>
              <td>{assignment.activity_name}</td>
              <td className="numeric-cell">{assignment.weekly_periods}</td>
              <td>
                <StatusCell assignment={assignment} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GroupCell({ assignment }: { assignment: TeachingAssignment }) {
  const soleClassSection = assignment.class_sections.length === 1 ? assignment.class_sections[0] : undefined;
  if (assignment.participant_group_role === "WHOLE_CLASS" && soleClassSection !== undefined) {
    return <span>{soleClassSection.name}</span>;
  }
  const roleLabel = GROUP_ROLE_LABELS[assignment.participant_group_role] ?? assignment.participant_group_role;
  return (
    <>
      <span>{assignment.participant_group_name}</span>
      <span className="group-role-badge">{roleLabel}</span>
    </>
  );
}

function StatusCell({ assignment }: { assignment: TeachingAssignment }) {
  if (assignment.editable) {
    return <span className="status-standard">Standard</span>;
  }
  return (
    <>
      <span className="status-advanced">Advanced</span>
      <div className="reason-chips">
        {assignment.advanced_reasons.map((reason) => (
          <span className="reason-chip" key={reason}>
            {friendlyReason(reason)}
          </span>
        ))}
      </div>
    </>
  );
}

export default TeachingAssignmentsPage;
