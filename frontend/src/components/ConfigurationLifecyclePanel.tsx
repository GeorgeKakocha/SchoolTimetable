import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import {
  beginConfigurationDraft,
  discardConfigurationDraft,
  getConfigurationState,
} from "../api/configurationRevision";
import type { ConfigurationRevisionStateResponse } from "../api/types";

type LifecycleState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; value: ConfigurationRevisionStateResponse };

type PendingAction = "begin" | "discard" | null;

function describeError(error: unknown): string {
  return error instanceof ApiError ? error.detail : "Something went wrong. Please try again.";
}

interface ConfigurationLifecyclePanelProps {
  schoolId: string;
  academicYearId: string;
  onProjectionRefresh: () => void;
}

export default function ConfigurationLifecyclePanel({
  schoolId,
  academicYearId,
  onProjectionRefresh,
}: ConfigurationLifecyclePanelProps) {
  const [lifecycleState, setLifecycleState] = useState<LifecycleState>({ status: "loading" });
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [discardConfirming, setDiscardConfirming] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLifecycleState({ status: "loading" });
    setDiscardConfirming(false);

    getConfigurationState(schoolId, academicYearId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          setLifecycleState({ status: "ready", value });
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setLifecycleState({ status: "error", message: describeError(error) });
        }
      });

    return () => controller.abort();
  }, [schoolId, academicYearId, refreshToken]);

  function refreshAuthoritativeState(message?: string) {
    if (message !== undefined) {
      setActionError(message);
    }
    setRefreshToken((token) => token + 1);
  }

  async function handleBeginDraft() {
    if (pendingAction !== null) {
      return;
    }
    setPendingAction("begin");
    setActionError(null);
    try {
      const value = await beginConfigurationDraft(schoolId, academicYearId);
      setLifecycleState({ status: "ready", value });
      onProjectionRefresh();
    } catch (error) {
      refreshAuthoritativeState(describeError(error));
    } finally {
      setPendingAction(null);
    }
  }

  async function handleDiscardDraft() {
    if (pendingAction !== null) {
      return;
    }
    setPendingAction("discard");
    setActionError(null);
    try {
      const value = await discardConfigurationDraft(schoolId, academicYearId);
      setDiscardConfirming(false);
      setLifecycleState({ status: "ready", value });
      onProjectionRefresh();
    } catch (error) {
      setDiscardConfirming(false);
      refreshAuthoritativeState(
        error instanceof ApiError && error.code === "INITIAL_DRAFT_CANNOT_BE_DISCARDED"
          ? "This initial configuration is required before the first timetable is generated."
          : describeError(error),
      );
    } finally {
      setPendingAction(null);
    }
  }

  if (lifecycleState.status === "loading") {
    return <p className="configuration-lifecycle-loading">Loading configuration state…</p>;
  }
  if (lifecycleState.status === "error") {
    return (
      <div className="stale-banner" role="alert">
        <p>{lifecycleState.message}</p>
        <button type="button" onClick={() => setRefreshToken((token) => token + 1)}>
          Retry
        </button>
      </div>
    );
  }

  const state = lifecycleState.value;
  const hasDraft = state.draft_revision_number !== null;
  const isProtectedInitialDraft = hasDraft && state.published_revision_number === null;

  return (
    <section className="configuration-lifecycle" aria-labelledby="configuration-lifecycle-heading">
      <h2 id="configuration-lifecycle-heading" className="sr-only">
        Configuration editing state
      </h2>

      {actionError !== null && (
        <div className="stale-banner" role="alert">
          <p>{actionError}</p>
          <button type="button" onClick={() => setRefreshToken((token) => token + 1)}>
            Refresh state
          </button>
        </div>
      )}

      {!hasDraft && (
        <div className="lock-banner">
          <p>Configuration is published and locked because the current timetable is active.</p>
          <p>Open an editable draft to make changes. The current timetable will remain unchanged.</p>
          <button type="button" className="btn-primary" onClick={handleBeginDraft} disabled={pendingAction !== null}>
            {pendingAction === "begin" ? "Opening draft…" : "Edit configuration"}
          </button>
        </div>
      )}

      {hasDraft && isProtectedInitialDraft && (
        <div className="lock-banner">
          <p>This is the initial configuration draft. Complete configuration before generating the first timetable.</p>
          <p>Changes are editable here; no timetable exists yet.</p>
        </div>
      )}

      {hasDraft && !isProtectedInitialDraft && (
        <div className={state.timetable_out_of_date ? "stale-banner" : "lock-banner"}>
          {state.timetable_out_of_date ? (
            <>
              <p>Configuration changes are saved in an editable draft.</p>
              <p>The current timetable remains active. Regeneration will be required to apply these changes.</p>
            </>
          ) : (
            <>
              <p>An editable configuration draft is open.</p>
              <p>The current published timetable remains active until changes are made and regenerated.</p>
            </>
          )}
          {!discardConfirming ? (
            <button
              type="button"
              className="action-button action-button-delete"
              onClick={() => {
                setActionError(null);
                setDiscardConfirming(true);
              }}
              disabled={pendingAction !== null}
            >
              Discard draft
            </button>
          ) : (
            <div className="reoptimize-confirm">
              <p>
                Discarding will lose unpublished configuration changes. The published configuration and current active
                timetable will remain unchanged.
              </p>
              <span className="reoptimize-confirm-actions">
                <button type="button" className="btn-primary" onClick={handleDiscardDraft} disabled={pendingAction !== null}>
                  {pendingAction === "discard" ? "Discarding…" : "Confirm discard"}
                </button>
                <button type="button" onClick={() => setDiscardConfirming(false)} disabled={pendingAction !== null}>
                  Cancel
                </button>
              </span>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
