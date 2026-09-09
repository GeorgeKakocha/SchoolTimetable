import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SubjectsPanel from "./SubjectsPanel";
import { ApiError } from "../../api/client";
import { createSubject, deleteSubject, getSubjects, updateSubject } from "../../api/subjects";
import { loadAppConfig } from "../../config/appConfig";
import type { SubjectsProjectionResponse } from "../../api/types";

vi.mock("../../api/subjects", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/subjects")>();
  return {
    ...actual,
    getSubjects: vi.fn(),
    createSubject: vi.fn(),
    updateSubject: vi.fn(),
    deleteSubject: vi.fn(),
  };
});

vi.mock("../../config/appConfig", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../config/appConfig")>();
  return {
    ...actual,
    loadAppConfig: vi.fn(),
  };
});

const mockedGetSubjects = vi.mocked(getSubjects);
const mockedCreateSubject = vi.mocked(createSubject);
const mockedUpdateSubject = vi.mocked(updateSubject);
const mockedDeleteSubject = vi.mocked(deleteSubject);
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

const TWO_SUBJECTS: SubjectsProjectionResponse = {
  configuration_locked: false,
  subjects: [
    { id: "activity_math", name: "Mathematics" },
    { id: "activity_history", name: "History" },
  ],
};

const EMPTY_PROJECTION: SubjectsProjectionResponse = { configuration_locked: false, subjects: [] };

const LOCKED_PROJECTION: SubjectsProjectionResponse = {
  configuration_locked: true,
  subjects: [{ id: "activity_math", name: "Mathematics" }],
};

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SubjectsPanel", () => {
  it("shows a loading state, then the subject list in server order", async () => {
    const { promise, resolve } = deferred<SubjectsProjectionResponse>();
    mockedGetSubjects.mockReturnValue(promise);

    render(<SubjectsPanel />);
    expect(screen.getByText("Loading subjects…")).toBeInTheDocument();

    resolve(TWO_SUBJECTS);
    await screen.findByText("Mathematics");
    const rows = screen.getAllByRole("row");
    expect(rows[1]?.textContent).toContain("Mathematics");
    expect(rows[2]?.textContent).toContain("History");
  });

  it("shows the empty state with no dominant form when there are no subjects", async () => {
    mockedGetSubjects.mockResolvedValue(EMPTY_PROJECTION);

    render(<SubjectsPanel />);

    await screen.findByText("No subjects yet.");
    expect(screen.queryByLabelText("Subject name")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "+ Add subject" })).toHaveLength(1);
  });

  it("reveals the create form only after clicking Add subject", async () => {
    mockedGetSubjects.mockResolvedValue(TWO_SUBJECTS);
    render(<SubjectsPanel />);
    await screen.findByText("Mathematics");

    expect(screen.queryByLabelText("Subject name")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ Add subject" }));
    expect(screen.getByLabelText("Subject name")).toBeInTheDocument();
  });

  it("prevents Save while the subject name is blank", async () => {
    mockedGetSubjects.mockResolvedValue(TWO_SUBJECTS);
    render(<SubjectsPanel />);
    await screen.findByText("Mathematics");

    fireEvent.click(screen.getByRole("button", { name: "+ Add subject" }));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Subject name"), { target: { value: "Physics" } });
    expect(screen.getByRole("button", { name: "Save" })).not.toBeDisabled();
  });

  it("sends the exact create request body, then collapses and refetches on success", async () => {
    mockedGetSubjects.mockResolvedValueOnce(TWO_SUBJECTS);
    mockedCreateSubject.mockResolvedValue({ id: "activity_physics", name: "Physics" });
    mockedGetSubjects.mockResolvedValueOnce({
      configuration_locked: false,
      subjects: [...TWO_SUBJECTS.subjects, { id: "activity_physics", name: "Physics" }],
    });

    render(<SubjectsPanel />);
    await screen.findByText("Mathematics");

    fireEvent.click(screen.getByRole("button", { name: "+ Add subject" }));
    fireEvent.change(screen.getByLabelText("Subject name"), { target: { value: "Physics" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mockedCreateSubject).toHaveBeenCalledWith("s1", "y1", { name: "Physics" });
    });
    await screen.findByText("Physics");
    expect(screen.queryByLabelText("Subject name")).not.toBeInTheDocument();
  });

  it("switches a row into inline edit mode and sends the exact update request", async () => {
    mockedGetSubjects.mockResolvedValueOnce(TWO_SUBJECTS);
    mockedUpdateSubject.mockResolvedValue({ id: "activity_math", name: "Math" });
    mockedGetSubjects.mockResolvedValueOnce({
      configuration_locked: false,
      subjects: [{ id: "activity_math", name: "Math" }, TWO_SUBJECTS.subjects[1]!],
    });

    render(<SubjectsPanel />);
    await screen.findByText("Mathematics");

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);
    const nameInputs = screen.getAllByLabelText("Subject name");
    fireEvent.change(nameInputs[nameInputs.length - 1]!, { target: { value: "Math" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mockedUpdateSubject).toHaveBeenCalledWith("s1", "y1", "activity_math", { name: "Math" });
    });
    await screen.findByText("Math");
  });

  it("edit Cancel discards local edit state", async () => {
    mockedGetSubjects.mockResolvedValue(TWO_SUBJECTS);
    render(<SubjectsPanel />);
    await screen.findByText("Mathematics");

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);
    const nameInputs = screen.getAllByLabelText("Subject name");
    fireEvent.change(nameInputs[nameInputs.length - 1]!, { target: { value: "Changed" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(mockedUpdateSubject).not.toHaveBeenCalled();
    expect(screen.getByText("Mathematics")).toBeInTheDocument();
  });

  it("maps DUPLICATE_SUBJECT to a clear inline message", async () => {
    mockedGetSubjects.mockResolvedValue(TWO_SUBJECTS);
    mockedCreateSubject.mockRejectedValue(
      new ApiError(409, "A subject named Mathematics already exists", "DUPLICATE_SUBJECT"),
    );

    render(<SubjectsPanel />);
    await screen.findByText("Mathematics");

    fireEvent.click(screen.getByRole("button", { name: "+ Add subject" }));
    fireEvent.change(screen.getByLabelText("Subject name"), { target: { value: "Mathematics" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("A subject with this name already exists.");
  });

  it("delete confirm/cancel: cancel leaves the subject intact", async () => {
    mockedGetSubjects.mockResolvedValue(TWO_SUBJECTS);
    render(<SubjectsPanel />);
    await screen.findByText("Mathematics");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    expect(screen.getByText("Delete Mathematics?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(mockedDeleteSubject).not.toHaveBeenCalled();
    expect(screen.queryByText("Delete Mathematics?")).not.toBeInTheDocument();
  });

  it("maps SUBJECT_IN_USE referenced_by to human-readable labels", async () => {
    mockedGetSubjects.mockResolvedValue(TWO_SUBJECTS);
    mockedDeleteSubject.mockRejectedValue(
      new ApiError(409, "Subject is referenced elsewhere", "SUBJECT_IN_USE", {
        referenced_by: ["TEACHING_REQUIREMENT", "RESERVED_BLOCK"],
      }),
    );

    render(<SubjectsPanel />);
    await screen.findByText("Mathematics");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await screen.findByText(
      "This subject can't be deleted because it is used by: Teaching assignments, Reserved activities.",
    );
  });

  it("disables Add/Edit/Delete when configuration is locked", async () => {
    mockedGetSubjects.mockResolvedValue(LOCKED_PROJECTION);

    render(<SubjectsPanel />);
    await screen.findByText("Mathematics");

    expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Add subject" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("on a lock race during create, refetches into the locked state", async () => {
    mockedGetSubjects.mockResolvedValueOnce(TWO_SUBJECTS);
    mockedCreateSubject.mockRejectedValue(new ApiError(409, "locked", "SCHEDULING_CONFIGURATION_LOCKED"));
    mockedGetSubjects.mockResolvedValueOnce(LOCKED_PROJECTION);

    render(<SubjectsPanel />);
    await screen.findByText("Mathematics");

    fireEvent.click(screen.getByRole("button", { name: "+ Add subject" }));
    fireEvent.change(screen.getByLabelText("Subject name"), { target: { value: "Physics" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText(/A schedule was generated since this page loaded/);
    await waitFor(() => {
      expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    });
    expect(screen.queryByLabelText("Subject name")).not.toBeInTheDocument();
  });

  it("never renders a kind field or any Club concept", async () => {
    mockedGetSubjects.mockResolvedValue(TWO_SUBJECTS);
    render(<SubjectsPanel />);
    await screen.findByText("Mathematics");

    const bodyText = document.body.textContent ?? "";
    expect(bodyText).not.toContain("ORDINARY");
    expect(bodyText).not.toContain("CLUB");
    expect(bodyText).not.toContain("kind");
    expect(bodyText).not.toContain("activity_math");
  });

  it("does not double-submit while a create is in flight", async () => {
    mockedGetSubjects.mockResolvedValue(TWO_SUBJECTS);
    const { promise, resolve } = deferred<{ id: string; name: string }>();
    mockedCreateSubject.mockReturnValue(promise);

    render(<SubjectsPanel />);
    await screen.findByText("Mathematics");

    fireEvent.click(screen.getByRole("button", { name: "+ Add subject" }));
    fireEvent.change(screen.getByLabelText("Subject name"), { target: { value: "Physics" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(screen.getByRole("button", { name: "Saving…" }));

    expect(mockedCreateSubject).toHaveBeenCalledTimes(1);
    resolve({ id: "activity_physics", name: "Physics" });
  });
});
