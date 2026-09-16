import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import IncompatibleLocksDialog from "./IncompatibleLocksDialog";
import type { IncompatibleLock } from "../api/types";

const LOCKS: IncompatibleLock[] = [
  {
    requirement_id: "math_8a",
    day_id: "mon",
    anchor_period_id: "p1",
    reason_code: "FIXED_PLACEMENT_CONFLICT",
    message: "This locked lesson conflicts with a fixed placement.",
  },
  {
    requirement_id: "history_8b",
    day_id: "tue",
    anchor_period_id: "p3",
    reason_code: "FUTURE_REASON",
    message: "A future structural rule rejected this lock.",
  },
];

describe("IncompatibleLocksDialog", () => {
  it("lists every lock, safe reason labels, and backend messages", () => {
    render(<IncompatibleLocksDialog locks={LOCKS} submitting={false} onCancel={vi.fn()} onConfirm={vi.fn()} />);

    expect(screen.getByRole("dialog", { name: "Review incompatible locked lessons" })).toBeInTheDocument();
    expect(screen.getByText("math_8a · mon · p1")).toBeInTheDocument();
    expect(screen.getByText("history_8b · tue · p3")).toBeInTheDocument();
    expect(screen.getByText("Conflicts with a fixed placement")).toBeInTheDocument();
    expect(screen.getByText("Configuration conflict")).toBeInTheDocument();
    expect(screen.getByText(LOCKS[0]!.message)).toBeInTheDocument();
    expect(screen.getByText(LOCKS[1]!.message)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Regenerate without these locks" })).toBeInTheDocument();
  });

  it("supports cancel and explicit confirmation, while disabling both during retry", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    const { rerender } = render(
      <IncompatibleLocksDialog locks={LOCKS} submitting={false} onCancel={onCancel} onConfirm={onConfirm} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Regenerate without these locks" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledTimes(1);

    rerender(<IncompatibleLocksDialog locks={LOCKS} submitting onCancel={onCancel} onConfirm={onConfirm} />);
    expect(screen.getByRole("button", { name: "Regenerating…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });
});
