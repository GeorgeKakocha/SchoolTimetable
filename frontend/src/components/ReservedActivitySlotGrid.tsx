import type { ReservedActivityDay, ReservedActivityPeriod } from "../api/reservedActivities";

/**
 * Reserved Activities Slice B: purely presentational -- never fetches,
 * never owns draft/API state. Receives the already
 * instructional-Period-filtered `periods` (item 25 -- non-instructional
 * periods are never rendered here) and an `isSelected` accessor; the
 * caller (`ReservedActivityEditor`) is the only source of truth for
 * which slots are currently selected.
 *
 * This is deliberately NOT a reuse of `AvailabilityGrid` -- only its
 * proven layout/responsive *pattern* is reused (desktop Period rows x
 * Day columns, a per-Day stacked mobile layout, both always present in
 * the DOM with a single `@media (max-width:640px)` CSS toggle, zero JS
 * viewport listener). `AvailabilityGrid`'s cell is a 3-state cycling
 * status button; a Reserved Activity slot is plain SET MEMBERSHIP, so
 * every cell here is a real `<input type="checkbox">` instead -- never
 * `aria-pressed`, never a custom checkbox role.
 *
 * Desktop and mobile checkboxes for the same (day, period) are two
 * distinct DOM nodes (both always mounted; CSS `display:none` removes
 * the inactive one from the accessibility tree) and therefore need two
 * distinct, unique `id`s (`reserved-slot-desktop-...`/
 * `reserved-slot-mobile-...`); both are driven by, and write back to,
 * the exact same controlled `isSelected`/`onToggle` pair, never native
 * form submission.
 */

function slotId(prefix: "desktop" | "mobile", dayId: string, periodId: string): string {
  return `reserved-slot-${prefix}-${dayId}-${periodId}`;
}

interface ReservedActivitySlotGridProps {
  days: ReservedActivityDay[];
  instructionalPeriods: ReservedActivityPeriod[];
  isSelected: (dayId: string, periodId: string) => boolean;
  onToggle: (dayId: string, periodId: string) => void;
  disabled: boolean;
}

function ReservedActivitySlotGrid({
  days,
  instructionalPeriods,
  isSelected,
  onToggle,
  disabled,
}: ReservedActivitySlotGridProps) {
  return (
    <fieldset className="reserved-slot-fieldset" disabled={disabled}>
      <legend>Time slots</legend>

      <div className="reserved-slot-desktop-matrix table-scroll" data-testid="reserved-slot-desktop-matrix">
        <table className="setup-table reserved-slot-table">
          <colgroup>
            <col className="col-reserved-slot-period" />
            {days.map((day) => (
              <col className="col-reserved-slot-day" key={day.id} />
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
            {instructionalPeriods.map((period) => (
              <tr key={period.id}>
                <th scope="row">{period.name}</th>
                {days.map((day) => {
                  const id = slotId("desktop", day.id, period.id);
                  return (
                    <td key={day.id}>
                      <label className="sr-only" htmlFor={id}>
                        {day.name}, {period.name}
                      </label>
                      <input
                        type="checkbox"
                        id={id}
                        className="reserved-slot-checkbox"
                        checked={isSelected(day.id, period.id)}
                        onChange={() => onToggle(day.id, period.id)}
                        disabled={disabled}
                      />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="reserved-slot-mobile-days" data-testid="reserved-slot-mobile-days">
        {days.map((day) => (
          <div className="reserved-slot-mobile-day" key={day.id}>
            <h3 className="reserved-slot-mobile-day-heading">{day.name}</h3>
            <div className="reserved-slot-mobile-day-rows">
              {instructionalPeriods.map((period) => {
                const id = slotId("mobile", day.id, period.id);
                return (
                  <div className="reserved-slot-mobile-row" key={period.id}>
                    <input
                      type="checkbox"
                      id={id}
                      className="reserved-slot-checkbox"
                      checked={isSelected(day.id, period.id)}
                      onChange={() => onToggle(day.id, period.id)}
                      disabled={disabled}
                    />
                    <label htmlFor={id}>
                      {day.name}, {period.name}
                    </label>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </fieldset>
  );
}

export default ReservedActivitySlotGrid;
