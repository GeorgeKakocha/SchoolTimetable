import type { AvailabilityCellState, TeacherAvailabilityDay, TeacherAvailabilityPeriod } from "../../api/types";

/**
 * Teacher Availability Slice B: purely presentational -- never fetches,
 * never owns server/draft state, never owns Save/Reset. Receives the
 * already-instructional-filtered `periods` and a `getCellState`
 * accessor; the caller (`TeacherAvailabilityPage`) is the only source
 * of truth for what each cell currently shows.
 *
 * Desktop orientation is locked: Period **rows** x Day **columns**,
 * matching the existing `TimetableGrid`'s own orientation exactly
 * (`<th scope="col">Period</th>` + day columns there; here the axes
 * are the same shape, just with an availability cell instead of a
 * lesson entry). Renders BOTH the desktop table and the narrow-width
 * per-Day stacked markup unconditionally -- a plain CSS media query
 * (`index.css`) toggles which one is `display:none` at the 640px
 * breakpoint; no JavaScript viewport listener. Both branches call the
 * exact same `getCellState`/`onCellActivate` props, so there is only
 * ever one underlying source of truth regardless of which markup is
 * visible. `data-testid` hooks below exist only so tests can
 * unambiguously scope to one branch (jsdom does not evaluate `@media`
 * rules, so both branches are simultaneously present in the test DOM)
 * -- they have no runtime/production meaning.
 *
 * Every cell is a single real `<button>` cycling
 * AVAILABLE -> PREFER_NOT -> UNAVAILABLE -> AVAILABLE. Deliberately
 * NEVER `aria-pressed`/`aria-checked` -- both are inherently binary/
 * mixed-only and cannot correctly represent three independent
 * semantic values; the button's `aria-label` instead states the Day,
 * Period, current status, and the next status one activation will
 * produce, so a screen reader always announces the true state (the
 * accessible name update is heard because focus never leaves the
 * button across an activation).
 */

const STATE_LABELS: Record<AvailabilityCellState, string> = {
  AVAILABLE: "Available",
  PREFER_NOT: "Prefer not",
  UNAVAILABLE: "Unavailable",
};

const STATE_SYMBOLS: Record<AvailabilityCellState, string> = {
  AVAILABLE: "✓",
  PREFER_NOT: "–",
  UNAVAILABLE: "✗",
};

const NEXT_STATE: Record<AvailabilityCellState, AvailabilityCellState> = {
  AVAILABLE: "PREFER_NOT",
  PREFER_NOT: "UNAVAILABLE",
  UNAVAILABLE: "AVAILABLE",
};

function stateClassSuffix(state: AvailabilityCellState): string {
  return state === "AVAILABLE" ? "available" : state === "PREFER_NOT" ? "prefer-not" : "unavailable";
}

function accessibleLabel(dayName: string, periodName: string, state: AvailabilityCellState): string {
  const next = STATE_LABELS[NEXT_STATE[state]];
  return `${dayName}, ${periodName}. Current status: ${STATE_LABELS[state]}. Activate to change to ${next}.`;
}

interface AvailabilityGridProps {
  days: TeacherAvailabilityDay[];
  periods: TeacherAvailabilityPeriod[];
  getCellState: (dayId: string, periodId: string) => AvailabilityCellState;
  disabled: boolean;
  onCellActivate: (dayId: string, periodId: string) => void;
}

function Cell({
  day,
  period,
  state,
  disabled,
  onCellActivate,
}: {
  day: TeacherAvailabilityDay;
  period: TeacherAvailabilityPeriod;
  state: AvailabilityCellState;
  disabled: boolean;
  onCellActivate: (dayId: string, periodId: string) => void;
}) {
  return (
    <button
      type="button"
      className={`availability-cell availability-cell-${stateClassSuffix(state)}`}
      disabled={disabled}
      onClick={() => onCellActivate(day.id, period.id)}
      aria-label={accessibleLabel(day.name, period.name, state)}
    >
      {STATE_SYMBOLS[state]} {STATE_LABELS[state]}
    </button>
  );
}

function AvailabilityGrid({ days, periods, getCellState, disabled, onCellActivate }: AvailabilityGridProps) {
  return (
    <>
      <div className="availability-desktop-matrix table-scroll" data-testid="availability-desktop-matrix">
        <table className="setup-table availability-table">
          <colgroup>
            <col className="col-availability-period" />
            {days.map((day) => (
              <col className="col-availability-day" key={day.id} />
            ))}
          </colgroup>
          <thead>
            <tr>
              <th scope="col">Period</th>
              {days.map((day) => (
                <th scope="col" key={day.id}>
                  {day.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {periods.map((period) => (
              <tr key={period.id}>
                <th scope="row">{period.name}</th>
                {days.map((day) => (
                  <td key={day.id}>
                    <Cell
                      day={day}
                      period={period}
                      state={getCellState(day.id, period.id)}
                      disabled={disabled}
                      onCellActivate={onCellActivate}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="availability-mobile-days" data-testid="availability-mobile-days">
        {days.map((day) => (
          <div className="availability-mobile-day" key={day.id}>
            <h3 className="availability-mobile-day-heading">{day.name}</h3>
            <div className="availability-mobile-day-rows">
              {periods.map((period) => (
                <div className="availability-mobile-row" key={period.id}>
                  <span className="availability-mobile-period-name">{period.name}</span>
                  <Cell
                    day={day}
                    period={period}
                    state={getCellState(day.id, period.id)}
                    disabled={disabled}
                    onCellActivate={onCellActivate}
                  />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

export default AvailabilityGrid;
