import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ReservedActivityEditor from "./ReservedActivityEditor";
import type {
  ReservedActivityClassSectionOption,
  ReservedActivityDay,
  ReservedActivityPeriod,
  ReservedActivityResourceOption,
  ReservedActivitySpecialActivityOption,
  ReservedActivityTeacherOption,
} from "../api/reservedActivities";

const SPECIAL_ACTIVITIES: ReservedActivitySpecialActivityOption[] = [
  { id: "club_debate", name: "Debate Club" },
  { id: "club_art", name: "Art Club" },
];
const CLASS_SECTIONS: ReservedActivityClassSectionOption[] = [
  { id: "class_8a", name: "8A" },
  { id: "class_8b", name: "8B" },
];
const TEACHERS: ReservedActivityTeacherOption[] = [{ id: "teacher_a", name: "Maia Beridze" }];
const RESOURCES: ReservedActivityResourceOption[] = [
  { id: "gym", name: "Gym", capacity: 1 },
  { id: "lab", name: "Science Lab", capacity: 1 },
];
const DAYS: ReservedActivityDay[] = [
  { id: "mon", name: "Monday", index: 0 },
  { id: "wed", name: "Wednesday", index: 2 },
];
const INSTRUCTIONAL_PERIODS: ReservedActivityPeriod[] = [
  { id: "p1", name: "P1", index: 0, is_instructional: true },
  { id: "p2", name: "P2", index: 1, is_instructional: true },
];

function baseProps(overrides: Partial<React.ComponentProps<typeof ReservedActivityEditor>> = {}) {
  return {
    mode: "create" as const,
    specialActivities: SPECIAL_ACTIVITIES,
    classSections: CLASS_SECTIONS,
    teachers: TEACHERS,
    resources: RESOURCES,
    days: DAYS,
    instructionalPeriods: INSTRUCTIONAL_PERIODS,
    submitting: false,
    errors: null,
    onCancel: vi.fn(),
    onSubmit: vi.fn(),
    ...overrides,
  };
}

describe("ReservedActivityEditor", () => {
  it("shows 'Add reserved activity' heading in create mode and does not preselect a Special Activity", () => {
    render(<ReservedActivityEditor {...baseProps()} />);

    expect(screen.getByRole("heading", { name: "Add reserved activity" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Special Activity" })).toHaveValue("");
  });

  it("shows 'Edit reserved activity' heading in edit mode", () => {
    render(
      <ReservedActivityEditor
        {...baseProps({
          mode: "edit",
          initialValues: {
            specialActivityId: "club_debate",
            classSectionIds: ["class_8a"],
            teacherId: null,
            slots: [{ day_id: "mon", period_id: "p1" }],
            resourceId: null,
          },
        })}
      />,
    );

    expect(screen.getByRole("heading", { name: "Edit reserved activity" })).toBeInTheDocument();
  });

  it("Save is disabled until a Special Activity, a Class, and a slot are all selected", () => {
    render(<ReservedActivityEditor {...baseProps()} />);

    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_debate" } });
    expect(save).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
    expect(save).toBeDisabled();

    fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
    expect(save).not.toBeDisabled();
  });

  it("Teacher defaults to 'No teacher' and submits teacherId: null", () => {
    const onSubmit = vi.fn();
    render(<ReservedActivityEditor {...baseProps({ onSubmit })} />);

    expect(screen.getByRole("combobox", { name: "Teacher" })).toHaveValue("");

    fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_debate" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
    fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSubmit).toHaveBeenCalledWith({
      specialActivityId: "club_debate",
      classSectionIds: ["class_8a"],
      teacherId: null,
      slots: [{ day_id: "mon", period_id: "p1" }],
      resourceId: null,
    });
  });

  it("submits classSectionIds in catalog order regardless of click order", () => {
    const onSubmit = vi.fn();
    render(<ReservedActivityEditor {...baseProps({ onSubmit })} />);

    fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_debate" } });
    // Click 8B before 8A -- catalog order is [8A, 8B].
    fireEvent.click(screen.getByRole("checkbox", { name: "8B" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
    fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ classSectionIds: ["class_8a", "class_8b"] }),
    );
  });

  it("submits slots in Day-then-Period order regardless of click order", () => {
    const onSubmit = vi.fn();
    render(<ReservedActivityEditor {...baseProps({ onSubmit })} />);

    fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_debate" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
    // Click Wednesday P2 before Monday P1.
    fireEvent.click(document.getElementById("reserved-slot-desktop-wed-p2")!);
    fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        slots: [
          { day_id: "mon", period_id: "p1" },
          { day_id: "wed", period_id: "p2" },
        ],
      }),
    );
  });

  it("in edit mode, Save is disabled until something actually changes (canonical comparison)", () => {
    render(
      <ReservedActivityEditor
        {...baseProps({
          mode: "edit",
          initialValues: {
            specialActivityId: "club_debate",
            classSectionIds: ["class_8a", "class_8b"],
            teacherId: null,
            slots: [
              { day_id: "mon", period_id: "p1" },
              { day_id: "wed", period_id: "p2" },
            ],
            resourceId: null,
          },
        })}
      />,
    );

    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    // Toggling a class off and back on returns to the exact same
    // canonical set -- must not spuriously mark the draft dirty.
    fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
    expect(save).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
    expect(save).not.toBeDisabled();
  });

  it("Cancel calls onCancel without calling onSubmit", () => {
    const onCancel = vi.fn();
    const onSubmit = vi.fn();
    render(<ReservedActivityEditor {...baseProps({ onCancel, onSubmit })} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onCancel).toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("renders the errors summary when errors are supplied", () => {
    render(<ReservedActivityEditor {...baseProps({ errors: ["Something is wrong.", "Another issue."] })} />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Something is wrong.");
    expect(alert).toHaveTextContent("Another issue.");
  });

  it("disables all fields and shows Saving… while submitting", () => {
    render(<ReservedActivityEditor {...baseProps({ submitting: true })} />);

    expect(screen.getByRole("combobox", { name: "Special Activity" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Saving…" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  });

  it("Resource select shows 'No resource' first, then every Resource by name, never a raw ID", () => {
    render(<ReservedActivityEditor {...baseProps()} />);

    const resourceSelect = screen.getByRole("combobox", { name: "Resource" }) as HTMLSelectElement;
    const optionLabels = Array.from(resourceSelect.options).map((option) => option.textContent);
    expect(optionLabels[0]).toBe("No resource");
    expect(optionLabels).toContain("Gym");
    expect(optionLabels).toContain("Science Lab");
    expect(resourceSelect.value).toBe("");
    expect(optionLabels).not.toContain("gym");
    expect(optionLabels).not.toContain("lab");
  });

  it("still renders the editor with only 'No resource' when the Resource catalog is empty", () => {
    render(<ReservedActivityEditor {...baseProps({ resources: [] })} />);

    const resourceSelect = screen.getByRole("combobox", { name: "Resource" }) as HTMLSelectElement;
    expect(resourceSelect.options.length).toBe(1);
    expect(resourceSelect.options[0]?.textContent).toBe("No resource");
    expect(resourceSelect).toBeEnabled();
  });

  it("Resource defaults to 'No resource' and submits resourceId: null", () => {
    const onSubmit = vi.fn();
    render(<ReservedActivityEditor {...baseProps({ onSubmit })} />);

    expect(screen.getByRole("combobox", { name: "Resource" })).toHaveValue("");

    fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_debate" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
    fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ resourceId: null }));
  });

  it("submits the selected Resource's exact id", () => {
    const onSubmit = vi.fn();
    render(<ReservedActivityEditor {...baseProps({ onSubmit })} />);

    fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_debate" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
    fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
    fireEvent.change(screen.getByRole("combobox", { name: "Resource" }), { target: { value: "gym" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ resourceId: "gym" }));
  });

  it("preselects the current Resource in edit mode", () => {
    render(
      <ReservedActivityEditor
        {...baseProps({
          mode: "edit",
          initialValues: {
            specialActivityId: "club_debate",
            classSectionIds: ["class_8a"],
            teacherId: null,
            slots: [{ day_id: "mon", period_id: "p1" }],
            resourceId: "lab",
          },
        })}
      />,
    );

    expect(screen.getByRole("combobox", { name: "Resource" })).toHaveValue("lab");
  });

  it("changing from one Resource to No resource marks the draft dirty and submits resourceId: null", () => {
    const onSubmit = vi.fn();
    render(
      <ReservedActivityEditor
        {...baseProps({
          mode: "edit",
          onSubmit,
          initialValues: {
            specialActivityId: "club_debate",
            classSectionIds: ["class_8a"],
            teacherId: null,
            slots: [{ day_id: "mon", period_id: "p1" }],
            resourceId: "gym",
          },
        })}
      />,
    );

    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByRole("combobox", { name: "Resource" }), { target: { value: "" } });
    expect(save).not.toBeDisabled();

    fireEvent.click(save);
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ resourceId: null }));
  });

  it("disables the Resource select while submitting", () => {
    render(<ReservedActivityEditor {...baseProps({ submitting: true })} />);
    expect(screen.getByRole("combobox", { name: "Resource" })).toBeDisabled();
  });
});
