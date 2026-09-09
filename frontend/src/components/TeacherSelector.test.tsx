import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TeacherSelector from "./TeacherSelector";
import type { TeacherSummary } from "../api/types";

const TEACHERS: TeacherSummary[] = [
  { id: "t_science", name: "Teacher Science" },
  { id: "t_art", name: "Teacher Art" },
  { id: "t_math", name: "Teacher Math" },
];

describe("TeacherSelector", () => {
  it("renders a labeled combobox", () => {
    render(
      <TeacherSelector teachers={TEACHERS} selectedTeacherId="t_science" onChange={() => {}} />,
    );
    expect(screen.getByRole("combobox", { name: "Teacher" })).toBeInTheDocument();
  });

  it("preserves the exact input order of teachers, never alphabetizing", () => {
    render(
      <TeacherSelector teachers={TEACHERS} selectedTeacherId="t_science" onChange={() => {}} />,
    );
    const options = screen.getAllByRole("option") as HTMLOptionElement[];
    expect(options.map((o) => o.value)).toEqual(["t_science", "t_art", "t_math"]);
    expect(options.map((o) => o.textContent)).toEqual(["Teacher Science", "Teacher Art", "Teacher Math"]);
  });

  it("reflects the selected teacher ID", () => {
    render(
      <TeacherSelector teachers={TEACHERS} selectedTeacherId="t_math" onChange={() => {}} />,
    );
    expect(screen.getByRole("combobox", { name: "Teacher" })).toHaveValue("t_math");
  });

  it("emits the chosen teacher ID on change", () => {
    const onChange = vi.fn();
    render(
      <TeacherSelector teachers={TEACHERS} selectedTeacherId="t_science" onChange={onChange} />,
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Teacher" }), { target: { value: "t_art" } });

    expect(onChange).toHaveBeenCalledWith("t_art");
  });

  it("can be disabled", () => {
    render(
      <TeacherSelector
        teachers={TEACHERS}
        selectedTeacherId="t_science"
        onChange={() => {}}
        disabled
      />,
    );
    expect(screen.getByRole("combobox", { name: "Teacher" })).toBeDisabled();
  });
});
