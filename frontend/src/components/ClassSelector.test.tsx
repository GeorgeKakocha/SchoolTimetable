import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ClassSelector from "./ClassSelector";
import type { ClassSectionSummary } from "../api/types";

const CLASS_SECTIONS: ClassSectionSummary[] = [
  { id: "9a", name: "9-A" },
  { id: "8a", name: "8-A" },
  { id: "8b", name: "8-B" },
];

describe("ClassSelector", () => {
  it("renders a labeled combobox", () => {
    render(
      <ClassSelector classSections={CLASS_SECTIONS} selectedClassId="9a" onChange={() => {}} />,
    );
    expect(screen.getByRole("combobox", { name: "Class" })).toBeInTheDocument();
  });

  it("preserves the exact input order of class sections, never alphabetizing", () => {
    render(
      <ClassSelector classSections={CLASS_SECTIONS} selectedClassId="9a" onChange={() => {}} />,
    );
    const options = screen.getAllByRole("option") as HTMLOptionElement[];
    expect(options.map((o) => o.value)).toEqual(["9a", "8a", "8b"]);
    expect(options.map((o) => o.textContent)).toEqual(["9-A", "8-A", "8-B"]);
  });

  it("reflects the selected class ID", () => {
    render(
      <ClassSelector classSections={CLASS_SECTIONS} selectedClassId="8b" onChange={() => {}} />,
    );
    expect(screen.getByRole("combobox", { name: "Class" })).toHaveValue("8b");
  });

  it("emits the chosen class ID on change", () => {
    const onChange = vi.fn();
    render(
      <ClassSelector classSections={CLASS_SECTIONS} selectedClassId="9a" onChange={onChange} />,
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Class" }), { target: { value: "8a" } });

    expect(onChange).toHaveBeenCalledWith("8a");
  });

  it("can be disabled", () => {
    render(
      <ClassSelector
        classSections={CLASS_SECTIONS}
        selectedClassId="9a"
        onChange={() => {}}
        disabled
      />,
    );
    expect(screen.getByRole("combobox", { name: "Class" })).toBeDisabled();
  });
});
