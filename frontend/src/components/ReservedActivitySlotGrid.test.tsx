import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ReservedActivitySlotGrid from "./ReservedActivitySlotGrid";
import type { ReservedActivityDay, ReservedActivityPeriod } from "../api/reservedActivities";

const DAYS: ReservedActivityDay[] = [
  { id: "mon", name: "Monday", index: 0 },
  { id: "tue", name: "Tuesday", index: 1 },
];

const INSTRUCTIONAL_PERIODS: ReservedActivityPeriod[] = [
  { id: "p1", name: "Period 1", index: 0, is_instructional: true },
  { id: "p2", name: "Period 2", index: 1, is_instructional: true },
];

describe("ReservedActivitySlotGrid", () => {
  it("renders one desktop checkbox and one mobile checkbox per (day, period), each with a unique id", () => {
    render(
      <ReservedActivitySlotGrid
        days={DAYS}
        instructionalPeriods={INSTRUCTIONAL_PERIODS}
        isSelected={() => false}
        onToggle={vi.fn()}
        disabled={false}
      />,
    );

    const checkboxes = document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
    // 2 days x 2 periods x 2 layouts (desktop + mobile) = 8 checkboxes total in the DOM.
    expect(checkboxes).toHaveLength(8);

    const ids = Array.from(checkboxes).map((checkbox) => checkbox.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toContain("reserved-slot-desktop-mon-p1");
    expect(ids).toContain("reserved-slot-mobile-mon-p1");
  });

  it("gives every checkbox an accessible name combining Day and Period", () => {
    render(
      <ReservedActivitySlotGrid
        days={DAYS}
        instructionalPeriods={INSTRUCTIONAL_PERIODS}
        isSelected={() => false}
        onToggle={vi.fn()}
        disabled={false}
      />,
    );

    const matches = screen.getAllByLabelText("Monday, Period 1");
    expect(matches).toHaveLength(2);
  });

  it("reflects isSelected via the native checked attribute, not color alone", () => {
    render(
      <ReservedActivitySlotGrid
        days={DAYS}
        instructionalPeriods={INSTRUCTIONAL_PERIODS}
        isSelected={(dayId, periodId) => dayId === "mon" && periodId === "p1"}
        onToggle={vi.fn()}
        disabled={false}
      />,
    );

    const desktopChecked = document.getElementById("reserved-slot-desktop-mon-p1") as HTMLInputElement;
    const desktopUnchecked = document.getElementById("reserved-slot-desktop-mon-p2") as HTMLInputElement;
    expect(desktopChecked.checked).toBe(true);
    expect(desktopUnchecked.checked).toBe(false);
  });

  it("calls onToggle with the exact (dayId, periodId) when a checkbox is toggled", () => {
    const onToggle = vi.fn();
    render(
      <ReservedActivitySlotGrid
        days={DAYS}
        instructionalPeriods={INSTRUCTIONAL_PERIODS}
        isSelected={() => false}
        onToggle={onToggle}
        disabled={false}
      />,
    );

    const checkbox = document.getElementById("reserved-slot-desktop-tue-p2") as HTMLInputElement;
    checkbox.click();

    expect(onToggle).toHaveBeenCalledWith("tue", "p2");
  });

  it("disables every checkbox when disabled is true", () => {
    render(
      <ReservedActivitySlotGrid
        days={DAYS}
        instructionalPeriods={INSTRUCTIONAL_PERIODS}
        isSelected={() => false}
        onToggle={vi.fn()}
        disabled
      />,
    );

    const checkboxes = document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
    for (const checkbox of Array.from(checkboxes)) {
      expect(checkbox).toBeDisabled();
    }
  });

  it("renders no checkbox for a period that is not in instructionalPeriods (caller already filtered non-instructional)", () => {
    render(
      <ReservedActivitySlotGrid
        days={DAYS}
        instructionalPeriods={[INSTRUCTIONAL_PERIODS[0]!]}
        isSelected={() => false}
        onToggle={vi.fn()}
        disabled={false}
      />,
    );

    expect(document.getElementById("reserved-slot-desktop-mon-p2")).toBeNull();
    expect(document.getElementById("reserved-slot-mobile-mon-p2")).toBeNull();
  });

  it("renders both the desktop matrix and mobile per-Day structure in the DOM simultaneously", () => {
    render(
      <ReservedActivitySlotGrid
        days={DAYS}
        instructionalPeriods={INSTRUCTIONAL_PERIODS}
        isSelected={() => false}
        onToggle={vi.fn()}
        disabled={false}
      />,
    );

    expect(screen.getByTestId("reserved-slot-desktop-matrix")).toBeInTheDocument();
    expect(screen.getByTestId("reserved-slot-mobile-days")).toBeInTheDocument();
  });
});
