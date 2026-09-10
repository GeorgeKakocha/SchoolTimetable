/**
 * Teacher Availability Slice B: a compact, always-visible (never
 * tooltip-only) legend explaining what the three states actually mean
 * -- not just their color -- so the SOFT (`Prefer not`) vs HARD
 * (`Unavailable`) distinction is never left implicit. Purely static/
 * presentational.
 */
function AvailabilityLegend() {
  return (
    <div className="availability-legend" role="note" aria-label="Availability state legend">
      <div className="availability-legend-item">
        <span className="availability-legend-symbol availability-cell-available">✓ Available</span>
        <span className="availability-legend-text">Normal scheduling</span>
      </div>
      <div className="availability-legend-item">
        <span className="availability-legend-symbol availability-cell-prefer-not">– Prefer not</span>
        <span className="availability-legend-text">Avoid if possible; may still be scheduled</span>
      </div>
      <div className="availability-legend-item">
        <span className="availability-legend-symbol availability-cell-unavailable">✗ Unavailable</span>
        <span className="availability-legend-text">Do not schedule</span>
      </div>
    </div>
  );
}

export default AvailabilityLegend;
