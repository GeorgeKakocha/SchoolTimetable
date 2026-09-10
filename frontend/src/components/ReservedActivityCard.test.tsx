import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ReservedActivityCard from "./ReservedActivityCard";
import type {
  ReservedActivityClassSectionOption,
  ReservedActivityDay,
  ReservedActivityItem,
  ReservedActivityPeriod,
  ReservedActivitySpecialActivityOption,
  ReservedActivityTeacherOption,
} from "../api/reservedActivities";

const SPECIAL_ACTIVITIES: ReservedActivitySpecialActivityOption[] = [{ id: "club_debate", name: "Debate Club" }];
const CLASS_SECTIONS: ReservedActivityClassSectionOption[] = [
  { id: "class_8a", name: "8A" },
  { id: "class_8b", name: "8B" },
];
const TEACHERS: ReservedActivityTeacherOption[] = [{ id: "teacher_a", name: "Maia Beridze" }];
const DAYS: ReservedActivityDay[] = [
  { id: "mon", name: "Monday", index: 0 },
  { id: "wed", name: "Wednesday", index: 2 },
];
const PERIODS: ReservedActivityPeriod[] = [
  { id: "p1", name: "P1", index: 0, is_instructional: true },
  { id: "p2", name: "P2", index: 1, is_instructional: true },
  { id: "p4", name: "P4", index: 3, is_instructional: true },
];

const BASE_ITEM: ReservedActivityItem = {
  id: "reserved_block_1",
  special_activity_id: "club_debate",
  class_section_ids: ["class_8a", "class_8b"],
  teacher_id: "teacher_a",
  slots: [
    { day_id: "mon", period_id: "p1" },
    { day_id: "mon", period_id: "p2" },
    { day_id: "wed", period_id: "p4" },
  ],
};

function renderCard(overrides: Partial<React.ComponentProps<typeof ReservedActivityCard>> = {}) {
  const props: React.ComponentProps<typeof ReservedActivityCard> = {
    item: BASE_ITEM,
    specialActivities: SPECIAL_ACTIVITIES,
    classSections: CLASS_SECTIONS,
    teachers: TEACHERS,
    days: DAYS,
    periods: PERIODS,
    locked: false,
    controlsDisabled: false,
    deleteConfirmId: null,
    deleteSubmitting: false,
    deleteError: null,
    onEditClick: vi.fn(),
    onDeleteClick: vi.fn(),
    onConfirmDelete: vi.fn(),
    onCancelDelete: vi.fn(),
    ...overrides,
  };
  return { ...render(<ReservedActivityCard {...props} />), props };
}

describe("ReservedActivityCard", () => {
  it("shows the Special Activity name, classes, teacher, and slots grouped by Day in Day order", () => {
    renderCard();

    expect(screen.getByText("Debate Club")).toBeInTheDocument();
    expect(screen.getByText("8A, 8B")).toBeInTheDocument();
    expect(screen.getByText("Maia Beridze")).toBeInTheDocument();

    const slotRows = screen.getAllByText(/^(Monday|Wednesday):$/);
    expect(slotRows.map((el) => el.textContent)).toEqual(["Monday:", "Wednesday:"]);
    expect(screen.getByText("P1, P2")).toBeInTheDocument();
    expect(screen.getByText("P4")).toBeInTheDocument();
  });

  it("shows 'No teacher' when teacher_id is null", () => {
    renderCard({ item: { ...BASE_ITEM, teacher_id: null } });
    expect(screen.getByText("No teacher")).toBeInTheDocument();
  });

  it("falls back to 'Unknown Special Activity' when the reference cannot be resolved", () => {
    renderCard({ item: { ...BASE_ITEM, special_activity_id: "club_missing" } });
    expect(screen.getByText("Unknown Special Activity")).toBeInTheDocument();
  });

  it("falls back to 'Unknown Class' for an unresolvable class reference, per-class", () => {
    renderCard({ item: { ...BASE_ITEM, class_section_ids: ["class_8a", "class_missing"] } });
    expect(screen.getByText("8A, Unknown Class")).toBeInTheDocument();
  });

  it("falls back to 'Unknown Teacher' for an unresolvable teacher reference", () => {
    renderCard({ item: { ...BASE_ITEM, teacher_id: "teacher_missing" } });
    expect(screen.getByText("Unknown Teacher")).toBeInTheDocument();
  });

  it("falls back to 'Unknown time slot' for an unresolvable day/period reference", () => {
    renderCard({ item: { ...BASE_ITEM, slots: [{ day_id: "mon", period_id: "p_missing" }] } });
    expect(screen.getByText("Unknown time slot")).toBeInTheDocument();
  });

  it("clicking Edit on a fully resolvable record calls onEditClick with the item", () => {
    const onEditClick = vi.fn();
    renderCard({ onEditClick });

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    expect(onEditClick).toHaveBeenCalledWith(BASE_ITEM);
  });

  it("clicking Edit on a malformed record shows a safe notice instead of opening the editor", () => {
    const onEditClick = vi.fn();
    renderCard({ onEditClick, item: { ...BASE_ITEM, special_activity_id: "club_missing" } });

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    expect(onEditClick).not.toHaveBeenCalled();
    expect(
      screen.getByText(
        "This Reserved Activity cannot be edited because some referenced configuration data is unavailable.",
      ),
    ).toBeInTheDocument();
  });

  it("Delete remains available on a malformed record", () => {
    const onDeleteClick = vi.fn();
    renderCard({ onDeleteClick, item: { ...BASE_ITEM, special_activity_id: "club_missing" } });

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(onDeleteClick).toHaveBeenCalledWith("reserved_block_1");
  });

  it("shows inline delete confirmation with Confirm/Cancel, no window.confirm", () => {
    const onConfirmDelete = vi.fn();
    const onCancelDelete = vi.fn();
    renderCard({ deleteConfirmId: "reserved_block_1", onConfirmDelete, onCancelDelete });

    expect(screen.getByText("Delete this reserved activity?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(onConfirmDelete).toHaveBeenCalledWith("reserved_block_1");

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancelDelete).toHaveBeenCalled();
  });

  it("disables Edit/Delete and shows no actionable editor when locked", () => {
    renderCard({ locked: true });

    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("never renders raw natural IDs as primary text", () => {
    renderCard();
    const bodyText = document.body.textContent ?? "";
    expect(bodyText).not.toContain("reserved_block_1");
    expect(bodyText).not.toContain("club_debate");
  });
});
