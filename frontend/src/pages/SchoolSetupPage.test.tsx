import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SchoolSetupPage from "./SchoolSetupPage";
import { getTeachers } from "../api/teachers";
import { getClasses } from "../api/classes";
import { getSubjects } from "../api/subjects";
import { loadAppConfig } from "../config/appConfig";
import type { ClassesProjectionResponse, SubjectsProjectionResponse, TeachersProjectionResponse } from "../api/types";

vi.mock("../api/teachers", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/teachers")>();
  return { ...actual, getTeachers: vi.fn() };
});
vi.mock("../api/classes", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/classes")>();
  return { ...actual, getClasses: vi.fn() };
});
vi.mock("../api/subjects", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/subjects")>();
  return { ...actual, getSubjects: vi.fn() };
});
vi.mock("../config/appConfig", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../config/appConfig")>();
  return { ...actual, loadAppConfig: vi.fn() };
});

const mockedGetTeachers = vi.mocked(getTeachers);
const mockedGetClasses = vi.mocked(getClasses);
const mockedGetSubjects = vi.mocked(getSubjects);
const mockedLoadAppConfig = vi.mocked(loadAppConfig);

const TEACHERS_PROJECTION: TeachersProjectionResponse = {
  configuration_locked: false,
  teachers: [{ id: "teacher_1", first_name: "Ada", last_name: "Lovelace", name: "Ada Lovelace" }],
};
const CLASSES_PROJECTION: ClassesProjectionResponse = { configuration_locked: false, classes: [{ id: "class_8a", name: "8-A" }] };
const SUBJECTS_PROJECTION: SubjectsProjectionResponse = {
  configuration_locked: false,
  subjects: [{ id: "activity_math", name: "Mathematics" }],
};

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
  mockedGetTeachers.mockResolvedValue(TEACHERS_PROJECTION);
  mockedGetClasses.mockResolvedValue(CLASSES_PROJECTION);
  mockedGetSubjects.mockResolvedValue(SUBJECTS_PROJECTION);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SchoolSetupPage", () => {
  it("renders the page heading and subtitle", async () => {
    render(<SchoolSetupPage />);

    expect(screen.getByRole("heading", { name: "School Setup", level: 1 })).toBeInTheDocument();
    expect(
      screen.getByText("Manage the teachers, classes, and subjects used to build the timetable."),
    ).toBeInTheDocument();
  });

  it("defaults to the Teachers tab and loads only the Teachers projection", async () => {
    render(<SchoolSetupPage />);

    await screen.findByText("Ada Lovelace");
    expect(mockedGetTeachers).toHaveBeenCalledTimes(1);
    expect(mockedGetClasses).not.toHaveBeenCalled();
    expect(mockedGetSubjects).not.toHaveBeenCalled();
  });

  it("switching to Classes loads the Classes projection only at that point, not on initial mount", async () => {
    render(<SchoolSetupPage />);
    await screen.findByText("Ada Lovelace");
    expect(mockedGetClasses).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("tab", { name: "Classes" }));

    await screen.findByText("8-A");
    expect(mockedGetClasses).toHaveBeenCalledTimes(1);
  });

  it("switching to Subjects loads the Subjects projection", async () => {
    render(<SchoolSetupPage />);
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getByRole("tab", { name: "Subjects" }));

    await screen.findByText("Mathematics");
    expect(mockedGetSubjects).toHaveBeenCalledTimes(1);
  });

  it("the inactive panel's content is not present in the document", async () => {
    render(<SchoolSetupPage />);
    await screen.findByText("Ada Lovelace");
    expect(screen.queryByText("8-A")).not.toBeInTheDocument();
    expect(screen.queryByText("Mathematics")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Classes" }));
    await screen.findByText("8-A");
    expect(screen.queryByText("Ada Lovelace")).not.toBeInTheDocument();
  });

  it("sets correct ARIA state on tabs and the tabpanel", async () => {
    render(<SchoolSetupPage />);
    await screen.findByText("Ada Lovelace");

    const teachersTab = screen.getByRole("tab", { name: "Teachers" });
    const classesTab = screen.getByRole("tab", { name: "Classes" });
    const subjectsTab = screen.getByRole("tab", { name: "Subjects" });

    expect(teachersTab).toHaveAttribute("aria-selected", "true");
    expect(classesTab).toHaveAttribute("aria-selected", "false");
    expect(subjectsTab).toHaveAttribute("aria-selected", "false");
    expect(teachersTab).toHaveAttribute("tabindex", "0");
    expect(classesTab).toHaveAttribute("tabindex", "-1");

    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAttribute("aria-labelledby", teachersTab.id);
    expect(teachersTab).toHaveAttribute("aria-controls", panel.id);

    fireEvent.click(classesTab);
    await screen.findByText("8-A");
    expect(classesTab).toHaveAttribute("aria-selected", "true");
    expect(teachersTab).toHaveAttribute("aria-selected", "false");
  });

  it("ArrowRight/ArrowLeft move focus and activate the adjacent tab, wrapping at the ends", async () => {
    render(<SchoolSetupPage />);
    await screen.findByText("Ada Lovelace");

    const teachersTab = screen.getByRole("tab", { name: "Teachers" });
    const classesTab = screen.getByRole("tab", { name: "Classes" });
    const subjectsTab = screen.getByRole("tab", { name: "Subjects" });

    teachersTab.focus();
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    await screen.findByText("8-A");
    expect(classesTab).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(classesTab);

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    await screen.findByText("Mathematics");
    expect(subjectsTab).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    await screen.findByText("Ada Lovelace");
    expect(teachersTab).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    await screen.findByText("Mathematics");
    expect(subjectsTab).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(subjectsTab);
  });

  it("Home/End jump to the first/last tab", async () => {
    render(<SchoolSetupPage />);
    await screen.findByText("Ada Lovelace");

    const teachersTab = screen.getByRole("tab", { name: "Teachers" });
    const subjectsTab = screen.getByRole("tab", { name: "Subjects" });

    teachersTab.focus();
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "End" });
    await screen.findByText("Mathematics");
    expect(subjectsTab).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(subjectsTab);

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "Home" });
    await screen.findByText("Ada Lovelace");
    expect(teachersTab).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(teachersTab);
  });

  it("remains stable (no crash, no stale data) across repeated tab switches", async () => {
    render(<SchoolSetupPage />);
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getByRole("tab", { name: "Classes" }));
    await screen.findByText("8-A");
    fireEvent.click(screen.getByRole("tab", { name: "Subjects" }));
    await screen.findByText("Mathematics");
    fireEvent.click(screen.getByRole("tab", { name: "Teachers" }));
    await screen.findByText("Ada Lovelace");

    await waitFor(() => {
      expect(mockedGetTeachers).toHaveBeenCalledTimes(2);
      expect(mockedGetClasses).toHaveBeenCalledTimes(1);
      expect(mockedGetSubjects).toHaveBeenCalledTimes(1);
    });
  });
});
