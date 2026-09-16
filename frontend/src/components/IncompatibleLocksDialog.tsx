import { useEffect, useRef } from "react";
import type { IncompatibleLock } from "../api/types";

const REASON_LABELS: Record<string, string> = {
  REQUIREMENT_OR_SLOT_DELETED: "Requirement or slot no longer exists",
  OCCURRENCE_STRUCTURE_INVALID: "Lesson occurrence structure changed",
  OCCURRENCE_UNRESOLVABLE: "Lesson occurrence can no longer be resolved",
  TEACHER_UNAVAILABLE_AT_SLOT: "Teacher is unavailable at this time",
  FIXED_PLACEMENT_CONFLICT: "Conflicts with a fixed placement",
  REQUIRED_BLOCK_PATTERN_INVALID: "Conflicts with the required lesson block pattern",
  RESOURCE_DELETED: "Required resource no longer exists",
  RESOURCE_CAPACITY_EXCEEDED: "Resource capacity would be exceeded",
};

export function reasonLabel(reasonCode: string): string {
  return REASON_LABELS[reasonCode] ?? "Configuration conflict";
}

interface IncompatibleLocksDialogProps {
  locks: IncompatibleLock[];
  submitting: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export default function IncompatibleLocksDialog({ locks, submitting, onCancel, onConfirm }: IncompatibleLocksDialogProps) {
  const headingId = "incompatible-locks-dialog-heading";
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    confirmRef.current?.focus();
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !submitting) {
        onCancel();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onCancel, submitting]);

  return (
    <div className="drawer-overlay" role="presentation">
      <div className="drawer-panel" role="dialog" aria-modal="true" aria-labelledby={headingId}>
        <div className="drawer-header">
          <h2 id={headingId}>Review incompatible locked lessons</h2>
          <button type="button" className="drawer-close" aria-label="Close" onClick={onCancel} disabled={submitting}>
            ×
          </button>
        </div>
        <p>
          Some currently locked lessons cannot be retained with the changed configuration. Continuing will regenerate
          the timetable without the incompatible locks listed below only. Compatible locks remain protected.
        </p>
        <p>The currently active timetable remains unchanged until regeneration succeeds.</p>
        <ul>
          {locks.map((lock, index) => (
            <li key={`${lock.requirement_id}|${lock.day_id}|${lock.anchor_period_id}|${index}`}>
              <strong>
                {lock.requirement_id} · {lock.day_id} · {lock.anchor_period_id}
              </strong>
              <div>{reasonLabel(lock.reason_code)}</div>
              <div>{lock.message}</div>
            </li>
          ))}
        </ul>
        <div className="reoptimize-confirm-actions">
          <button type="button" onClick={onCancel} disabled={submitting}>
            Cancel
          </button>
          <button type="button" className="btn-primary" ref={confirmRef} onClick={onConfirm} disabled={submitting}>
            {submitting ? "Regenerating…" : "Regenerate without these locks"}
          </button>
        </div>
      </div>
    </div>
  );
}
