import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ClassesPanel from "./ClassesPanel";
import { ApiError } from "../../api/client";
import { createClass, deleteClass, getClasses, updateClass } from "../../api/classes";
import { loadAppConfig } from "../../config/appConfig";
import type { ClassesProjectionResponse } from "../../api/types";

vi.mock("../../api/classes", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/classes")>();
  return {
    ...actual,
    getClasses: vi.fn(),
    createClass: vi.fn(),
    updateClass: vi.fn(),
    deleteClass: vi.fn(),
  };
});

vi.mock("../../config/appConfig", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../config/appConfig")>();
  return {
    ...actual,
    loadAppConfig: vi.fn(),
  };
});

const mockedGetClasses = vi.mocked(getClasses);
const mockedCreateClass = vi.mocked(createClass);
const mockedUpdateClass = vi.mocked(updateClass);
const mockedDeleteClass = vi.mocked(deleteClass);
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

const TWO_CLASSES: ClassesProjectionResponse = {
  configuration_locked: false,
  classes: [
    { id: "class_8a", name: "8-A" },
    { id: "class_8b", name: "8-B" },
  ],
};

const EMPTY_PROJECTION: ClassesProjectionResponse = { configuration_locked: false, classes: [] };

const LOCKED_PROJECTION: ClassesProjectionResponse = {
  configuration_locked: true,
  classes: [{ id: "class_8a", name: "8-A" }],
};

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("ClassesPanel", () => {
  it("shows a loading state, then the class list in server order", async () => {
    const { promise, resolve } = deferred<ClassesProjectionResponse>();
    mockedGetClasses.mockReturnValue(promise);

    render(<ClassesPanel />);
    expect(screen.getByText("Loading classes…")).toBeInTheDocument();

    resolve(TWO_CLASSES);
    await screen.findByText("8-A");
    const rows = screen.getAllByRole("row");
    expect(rows[1]?.textContent).toContain("8-A");
    expect(rows[2]?.textContent).toContain("8-B");
  });

  it("shows the empty state with no dominant form when there are no classes", async () => {
    mockedGetClasses.mockResolvedValue(EMPTY_PROJECTION);

    render(<ClassesPanel />);

    await screen.findByText("No classes yet.");
    expect(screen.queryByLabelText("Class name")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "+ Add class" })).toHaveLength(1);
  });

  it("reveals the create form only after clicking Add class", async () => {
    mockedGetClasses.mockResolvedValue(TWO_CLASSES);
    render(<ClassesPanel />);
    await screen.findByText("8-A");

    expect(screen.queryByLabelText("Class name")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ Add class" }));
    expect(screen.getByLabelText("Class name")).toBeInTheDocument();
  });

  it("prevents Save while the class name is blank", async () => {
    mockedGetClasses.mockResolvedValue(TWO_CLASSES);
    render(<ClassesPanel />);
    await screen.findByText("8-A");

    fireEvent.click(screen.getByRole("button", { name: "+ Add class" }));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Class name"), { target: { value: "8-C" } });
    expect(screen.getByRole("button", { name: "Save" })).not.toBeDisabled();
  });

  it("sends the exact create request body, then collapses and refetches on success", async () => {
    mockedGetClasses.mockResolvedValueOnce(TWO_CLASSES);
    mockedCreateClass.mockResolvedValue({ id: "class_8c", name: "8-C" });
    mockedGetClasses.mockResolvedValueOnce({ configuration_locked: false, classes: [...TWO_CLASSES.classes, { id: "class_8c", name: "8-C" }] });

    render(<ClassesPanel />);
    await screen.findByText("8-A");

    fireEvent.click(screen.getByRole("button", { name: "+ Add class" }));
    fireEvent.change(screen.getByLabelText("Class name"), { target: { value: "8-C" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mockedCreateClass).toHaveBeenCalledWith("s1", "y1", { name: "8-C" });
    });
    await screen.findByText("8-C");
    expect(screen.queryByLabelText("Class name")).not.toBeInTheDocument();
  });

  it("switches a row into inline edit mode and sends the exact update request", async () => {
    mockedGetClasses.mockResolvedValueOnce(TWO_CLASSES);
    mockedUpdateClass.mockResolvedValue({ id: "class_8a", name: "8-Alpha" });
    mockedGetClasses.mockResolvedValueOnce({ configuration_locked: false, classes: [{ id: "class_8a", name: "8-Alpha" }, TWO_CLASSES.classes[1]!] });

    render(<ClassesPanel />);
    await screen.findByText("8-A");

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);
    const nameInputs = screen.getAllByLabelText("Class name");
    fireEvent.change(nameInputs[nameInputs.length - 1]!, { target: { value: "8-Alpha" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mockedUpdateClass).toHaveBeenCalledWith("s1", "y1", "class_8a", { name: "8-Alpha" });
    });
    await screen.findByText("8-Alpha");
  });

  it("edit Cancel discards local edit state", async () => {
    mockedGetClasses.mockResolvedValue(TWO_CLASSES);
    render(<ClassesPanel />);
    await screen.findByText("8-A");

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);
    const nameInputs = screen.getAllByLabelText("Class name");
    fireEvent.change(nameInputs[nameInputs.length - 1]!, { target: { value: "Changed" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(mockedUpdateClass).not.toHaveBeenCalled();
    expect(screen.getByText("8-A")).toBeInTheDocument();
  });

  it("maps DUPLICATE_CLASS to a clear inline message", async () => {
    mockedGetClasses.mockResolvedValue(TWO_CLASSES);
    mockedCreateClass.mockRejectedValue(new ApiError(409, "A class named 8-A already exists", "DUPLICATE_CLASS"));

    render(<ClassesPanel />);
    await screen.findByText("8-A");

    fireEvent.click(screen.getByRole("button", { name: "+ Add class" }));
    fireEvent.change(screen.getByLabelText("Class name"), { target: { value: "8-A" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("A class with this name already exists.");
  });

  it("delete confirm/cancel: cancel leaves the class intact", async () => {
    mockedGetClasses.mockResolvedValue(TWO_CLASSES);
    render(<ClassesPanel />);
    await screen.findByText("8-A");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    expect(screen.getByText("Delete 8-A?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(mockedDeleteClass).not.toHaveBeenCalled();
    expect(screen.queryByText("Delete 8-A?")).not.toBeInTheDocument();
  });

  it("maps CLASS_IN_USE referenced_by to human-readable labels", async () => {
    mockedGetClasses.mockResolvedValue(TWO_CLASSES);
    mockedDeleteClass.mockRejectedValue(
      new ApiError(409, "Class is referenced elsewhere", "CLASS_IN_USE", {
        referenced_by: ["TEACHING_REQUIREMENT", "SUBGROUP", "MERGED_CLASSES"],
      }),
    );

    render(<ClassesPanel />);
    await screen.findByText("8-A");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await screen.findByText(
      "This class can't be deleted because it is used by: Teaching assignments, Subgroups, Merged classes.",
    );
  });

  it("disables Add/Edit/Delete when configuration is locked", async () => {
    mockedGetClasses.mockResolvedValue(LOCKED_PROJECTION);

    render(<ClassesPanel />);
    await screen.findByText("8-A");

    expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Add class" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("on a lock race during create, refetches into the locked state", async () => {
    mockedGetClasses.mockResolvedValueOnce(TWO_CLASSES);
    mockedCreateClass.mockRejectedValue(new ApiError(409, "locked", "SCHEDULING_CONFIGURATION_LOCKED"));
    mockedGetClasses.mockResolvedValueOnce(LOCKED_PROJECTION);

    render(<ClassesPanel />);
    await screen.findByText("8-A");

    fireEvent.click(screen.getByRole("button", { name: "+ Add class" }));
    fireEvent.change(screen.getByLabelText("Class name"), { target: { value: "8-C" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText(/A schedule was generated since this page loaded/);
    await waitFor(() => {
      expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    });
    expect(screen.queryByLabelText("Class name")).not.toBeInTheDocument();
  });

  it("never renders canonical group fields (id, role, membership, ordinal)", async () => {
    mockedGetClasses.mockResolvedValue(TWO_CLASSES);
    render(<ClassesPanel />);
    await screen.findByText("8-A");

    const bodyText = document.body.textContent ?? "";
    expect(bodyText).not.toContain("class_8a");
    expect(bodyText).not.toContain("WHOLE_CLASS");
    expect(bodyText).not.toContain("ordinal");
  });

  it("does not double-submit while a create is in flight", async () => {
    mockedGetClasses.mockResolvedValue(TWO_CLASSES);
    const { promise, resolve } = deferred<{ id: string; name: string }>();
    mockedCreateClass.mockReturnValue(promise);

    render(<ClassesPanel />);
    await screen.findByText("8-A");

    fireEvent.click(screen.getByRole("button", { name: "+ Add class" }));
    fireEvent.change(screen.getByLabelText("Class name"), { target: { value: "8-C" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(screen.getByRole("button", { name: "Saving…" }));

    expect(mockedCreateClass).toHaveBeenCalledTimes(1);
    resolve({ id: "class_8c", name: "8-C" });
  });
});
