import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TeachersPanel from "./TeachersPanel";
import { ApiError } from "../../api/client";
import { createTeacher, deleteTeacher, getTeachers, updateTeacher } from "../../api/teachers";
import { loadAppConfig } from "../../config/appConfig";
import type { TeachersProjectionResponse } from "../../api/types";

vi.mock("../../api/teachers", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/teachers")>();
  return {
    ...actual,
    getTeachers: vi.fn(),
    createTeacher: vi.fn(),
    updateTeacher: vi.fn(),
    deleteTeacher: vi.fn(),
  };
});

vi.mock("../../config/appConfig", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../config/appConfig")>();
  return {
    ...actual,
    loadAppConfig: vi.fn(),
  };
});

const mockedGetTeachers = vi.mocked(getTeachers);
const mockedCreateTeacher = vi.mocked(createTeacher);
const mockedUpdateTeacher = vi.mocked(updateTeacher);
const mockedDeleteTeacher = vi.mocked(deleteTeacher);
const mockedLoadAppConfig = vi.mocked(loadAppConfig);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const TWO_TEACHERS: TeachersProjectionResponse = {
  configuration_locked: false,
  teachers: [
    { id: "teacher_1", first_name: "Ada", last_name: "Lovelace", name: "Ada Lovelace" },
    { id: "teacher_2", first_name: "Alan", last_name: "Turing", name: "Alan Turing" },
  ],
};

const EMPTY_PROJECTION: TeachersProjectionResponse = { configuration_locked: false, teachers: [] };

const LOCKED_PROJECTION: TeachersProjectionResponse = {
  configuration_locked: true,
  teachers: [{ id: "teacher_1", first_name: "Ada", last_name: "Lovelace", name: "Ada Lovelace" }],
};

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("TeachersPanel", () => {
  it("shows a loading state, then the teacher list", async () => {
    const { promise, resolve } = deferred<TeachersProjectionResponse>();
    mockedGetTeachers.mockReturnValue(promise);

    render(<TeachersPanel />);
    expect(screen.getByText("Loading teachers…")).toBeInTheDocument();

    resolve(TWO_TEACHERS);
    await screen.findByText("Ada Lovelace");
    expect(screen.getByText("Alan Turing")).toBeInTheDocument();
  });

  it("shows the empty state with no dominant form when there are no teachers", async () => {
    mockedGetTeachers.mockResolvedValue(EMPTY_PROJECTION);

    render(<TeachersPanel />);

    await screen.findByText("No teachers yet.");
    expect(screen.queryByLabelText("First name")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "+ Add teacher" })).toHaveLength(1);
  });

  it("reveals the create form only after clicking Add teacher, and it is absent initially", async () => {
    mockedGetTeachers.mockResolvedValue(TWO_TEACHERS);
    render(<TeachersPanel />);
    await screen.findByText("Ada Lovelace");

    expect(screen.queryByLabelText("First name")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "+ Add teacher" }));

    expect(screen.getByLabelText("First name")).toBeInTheDocument();
    expect(screen.getByLabelText("Last name")).toBeInTheDocument();
  });

  it("prevents Save while first/last name are blank", async () => {
    mockedGetTeachers.mockResolvedValue(TWO_TEACHERS);
    render(<TeachersPanel />);
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getByRole("button", { name: "+ Add teacher" }));
    const saveButton = screen.getByRole("button", { name: "Save" });
    expect(saveButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("First name"), { target: { value: "Grace" } });
    expect(saveButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Last name"), { target: { value: "Hopper" } });
    expect(saveButton).not.toBeDisabled();
  });

  it("sends the exact create request body, then collapses the form and refetches on success", async () => {
    mockedGetTeachers.mockResolvedValueOnce(TWO_TEACHERS);
    mockedCreateTeacher.mockResolvedValue({ id: "teacher_3", first_name: "Grace", last_name: "Hopper", name: "Grace Hopper" });
    mockedGetTeachers.mockResolvedValueOnce({
      configuration_locked: false,
      teachers: [...TWO_TEACHERS.teachers, { id: "teacher_3", first_name: "Grace", last_name: "Hopper", name: "Grace Hopper" }],
    });

    render(<TeachersPanel />);
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getByRole("button", { name: "+ Add teacher" }));
    fireEvent.change(screen.getByLabelText("First name"), { target: { value: "Grace" } });
    fireEvent.change(screen.getByLabelText("Last name"), { target: { value: "Hopper" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mockedCreateTeacher).toHaveBeenCalledWith("s1", "y1", { first_name: "Grace", last_name: "Hopper" });
    });

    await screen.findByText("Grace Hopper");
    expect(screen.queryByLabelText("First name")).not.toBeInTheDocument();
  });

  it("switches a row into inline edit mode and sends the exact update request", async () => {
    mockedGetTeachers.mockResolvedValueOnce(TWO_TEACHERS);
    mockedUpdateTeacher.mockResolvedValue({ id: "teacher_1", first_name: "Ada", last_name: "King", name: "Ada King" });
    mockedGetTeachers.mockResolvedValueOnce({
      configuration_locked: false,
      teachers: [{ id: "teacher_1", first_name: "Ada", last_name: "King", name: "Ada King" }, TWO_TEACHERS.teachers[1]!],
    });

    render(<TeachersPanel />);
    await screen.findByText("Ada Lovelace");

    const editButtons = screen.getAllByRole("button", { name: "Edit" });
    fireEvent.click(editButtons[0]!);

    const lastNameInputs = screen.getAllByLabelText("Last name");
    const editLastNameInput = lastNameInputs[lastNameInputs.length - 1]!;
    fireEvent.change(editLastNameInput, { target: { value: "King" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mockedUpdateTeacher).toHaveBeenCalledWith("s1", "y1", "teacher_1", { first_name: "Ada", last_name: "King" });
    });
    await screen.findByText("Ada King");
  });

  it("edit Cancel discards local edit state and returns to server-rendered values", async () => {
    mockedGetTeachers.mockResolvedValue(TWO_TEACHERS);
    render(<TeachersPanel />);
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);
    const lastNameInputs = screen.getAllByLabelText("Last name");
    fireEvent.change(lastNameInputs[lastNameInputs.length - 1]!, { target: { value: "Changed" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(mockedUpdateTeacher).not.toHaveBeenCalled();
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
  });

  it("delete confirm/cancel: cancel leaves the teacher intact", async () => {
    mockedGetTeachers.mockResolvedValue(TWO_TEACHERS);
    render(<TeachersPanel />);
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    expect(screen.getByText("Delete Ada Lovelace?")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(mockedDeleteTeacher).not.toHaveBeenCalled();
    expect(screen.queryByText("Delete Ada Lovelace?")).not.toBeInTheDocument();
  });

  it("delete success refetches the authoritative projection", async () => {
    mockedGetTeachers.mockResolvedValueOnce(TWO_TEACHERS);
    mockedDeleteTeacher.mockResolvedValue({ deleted_id: "teacher_1" });
    mockedGetTeachers.mockResolvedValueOnce({ configuration_locked: false, teachers: [TWO_TEACHERS.teachers[1]!] });

    render(<TeachersPanel />);
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await waitFor(() => {
      expect(mockedDeleteTeacher).toHaveBeenCalledWith("s1", "y1", "teacher_1");
    });
    await waitFor(() => {
      expect(screen.queryByText("Ada Lovelace")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Alan Turing")).toBeInTheDocument();
  });

  it("maps TEACHER_IN_USE to a human-readable message", async () => {
    mockedGetTeachers.mockResolvedValue(TWO_TEACHERS);
    mockedDeleteTeacher.mockRejectedValue(
      new ApiError(409, "Teacher is referenced elsewhere", "TEACHER_IN_USE", {
        referenced_by: ["TEACHING_REQUIREMENT", "TEACHER_AVAILABILITY"],
      }),
    );

    render(<TeachersPanel />);
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await screen.findByText(
      "This teacher can't be deleted because it is used by: Teaching assignments, Teacher availability.",
    );
  });

  it("disables Add/Edit/Delete when configuration is locked, without hiding the list", async () => {
    mockedGetTeachers.mockResolvedValue(LOCKED_PROJECTION);

    render(<TeachersPanel />);
    await screen.findByText("Ada Lovelace");

    expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Add teacher" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("on a lock-race during create, refetches and shows the locked state instead of leaving a stale form open", async () => {
    mockedGetTeachers.mockResolvedValueOnce(TWO_TEACHERS);
    mockedCreateTeacher.mockRejectedValue(
      new ApiError(409, "locked", "SCHEDULING_CONFIGURATION_LOCKED"),
    );
    mockedGetTeachers.mockResolvedValueOnce(LOCKED_PROJECTION);

    render(<TeachersPanel />);
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getByRole("button", { name: "+ Add teacher" }));
    fireEvent.change(screen.getByLabelText("First name"), { target: { value: "Grace" } });
    fireEvent.change(screen.getByLabelText("Last name"), { target: { value: "Hopper" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText(/A schedule was generated since this page loaded/);
    await waitFor(() => {
      expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    });
    expect(screen.queryByLabelText("First name")).not.toBeInTheDocument();
  });

  it("allows two teachers with the same name (no client-side duplicate rejection)", async () => {
    mockedGetTeachers.mockResolvedValueOnce(TWO_TEACHERS);
    mockedCreateTeacher.mockResolvedValue({ id: "teacher_3", first_name: "Ada", last_name: "Lovelace", name: "Ada Lovelace" });
    mockedGetTeachers.mockResolvedValueOnce({
      configuration_locked: false,
      teachers: [...TWO_TEACHERS.teachers, { id: "teacher_3", first_name: "Ada", last_name: "Lovelace", name: "Ada Lovelace" }],
    });

    render(<TeachersPanel />);
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getByRole("button", { name: "+ Add teacher" }));
    fireEvent.change(screen.getByLabelText("First name"), { target: { value: "Ada" } });
    fireEvent.change(screen.getByLabelText("Last name"), { target: { value: "Lovelace" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mockedCreateTeacher).toHaveBeenCalledWith("s1", "y1", { first_name: "Ada", last_name: "Lovelace" });
    });
    await waitFor(() => {
      expect(screen.getAllByText("Ada Lovelace")).toHaveLength(2);
    });
  });

  it("does not double-submit while a create is in flight", async () => {
    mockedGetTeachers.mockResolvedValue(TWO_TEACHERS);
    const { promise, resolve } = deferred<{ id: string; first_name: string; last_name: string; name: string }>();
    mockedCreateTeacher.mockReturnValue(promise);

    render(<TeachersPanel />);
    await screen.findByText("Ada Lovelace");

    fireEvent.click(screen.getByRole("button", { name: "+ Add teacher" }));
    fireEvent.change(screen.getByLabelText("First name"), { target: { value: "Grace" } });
    fireEvent.change(screen.getByLabelText("Last name"), { target: { value: "Hopper" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(screen.getByRole("button", { name: "Saving…" }));

    expect(mockedCreateTeacher).toHaveBeenCalledTimes(1);
    resolve({ id: "teacher_3", first_name: "Grace", last_name: "Hopper", name: "Grace Hopper" });
  });
});
