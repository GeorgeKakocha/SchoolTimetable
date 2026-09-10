import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SpecialActivitiesPanel from "./SpecialActivitiesPanel";
import { ApiError } from "../../api/client";
import {
  createSpecialActivity,
  deleteSpecialActivity,
  getSpecialActivities,
  updateSpecialActivity,
} from "../../api/specialActivities";
import { loadAppConfig } from "../../config/appConfig";
import type { SpecialActivitiesProjectionResponse } from "../../api/specialActivities";

vi.mock("../../api/specialActivities", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/specialActivities")>();
  return {
    ...actual,
    getSpecialActivities: vi.fn(),
    createSpecialActivity: vi.fn(),
    updateSpecialActivity: vi.fn(),
    deleteSpecialActivity: vi.fn(),
  };
});

vi.mock("../../config/appConfig", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../config/appConfig")>();
  return {
    ...actual,
    loadAppConfig: vi.fn(),
  };
});

const mockedGetSpecialActivities = vi.mocked(getSpecialActivities);
const mockedCreateSpecialActivity = vi.mocked(createSpecialActivity);
const mockedUpdateSpecialActivity = vi.mocked(updateSpecialActivity);
const mockedDeleteSpecialActivity = vi.mocked(deleteSpecialActivity);
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

const TWO_ACTIVITIES: SpecialActivitiesProjectionResponse = {
  configuration_locked: false,
  special_activities: [
    { id: "club_debate", name: "Debate Club" },
    { id: "club_art", name: "Art Club" },
  ],
};

const EMPTY_PROJECTION: SpecialActivitiesProjectionResponse = { configuration_locked: false, special_activities: [] };

const LOCKED_PROJECTION: SpecialActivitiesProjectionResponse = {
  configuration_locked: true,
  special_activities: [{ id: "club_debate", name: "Debate Club" }],
};

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SpecialActivitiesPanel", () => {
  it("shows a loading state, then the special activity list in server order", async () => {
    const { promise, resolve } = deferred<SpecialActivitiesProjectionResponse>();
    mockedGetSpecialActivities.mockReturnValue(promise);

    render(<SpecialActivitiesPanel />);
    expect(screen.getByText("Loading special activities…")).toBeInTheDocument();

    resolve(TWO_ACTIVITIES);
    await screen.findByText("Debate Club");
    const rows = screen.getAllByRole("row");
    expect(rows[1]?.textContent).toContain("Debate Club");
    expect(rows[2]?.textContent).toContain("Art Club");
  });

  it("shows the help text separated from form content", async () => {
    mockedGetSpecialActivities.mockResolvedValue(TWO_ACTIVITIES);
    render(<SpecialActivitiesPanel />);
    await screen.findByText("Debate Club");

    expect(
      screen.getByText("Create school activities that can later be placed at fixed times in Reserved Activities."),
    ).toBeInTheDocument();
  });

  it("shows the empty state with no dominant form when there are no special activities", async () => {
    mockedGetSpecialActivities.mockResolvedValue(EMPTY_PROJECTION);

    render(<SpecialActivitiesPanel />);

    await screen.findByText("No Special Activities yet.");
    expect(screen.queryByLabelText("Special activity name")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "+ Add special activity" })).toHaveLength(1);
  });

  it("reveals the create form only after clicking Add special activity", async () => {
    mockedGetSpecialActivities.mockResolvedValue(TWO_ACTIVITIES);
    render(<SpecialActivitiesPanel />);
    await screen.findByText("Debate Club");

    expect(screen.queryByLabelText("Special activity name")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ Add special activity" }));
    expect(screen.getByLabelText("Special activity name")).toBeInTheDocument();
  });

  it("sends the exact create request body, then collapses and refetches on success", async () => {
    mockedGetSpecialActivities.mockResolvedValueOnce(TWO_ACTIVITIES);
    mockedCreateSpecialActivity.mockResolvedValue({ id: "club_chess", name: "Chess Club" });
    mockedGetSpecialActivities.mockResolvedValueOnce({
      configuration_locked: false,
      special_activities: [...TWO_ACTIVITIES.special_activities, { id: "club_chess", name: "Chess Club" }],
    });

    render(<SpecialActivitiesPanel />);
    await screen.findByText("Debate Club");

    fireEvent.click(screen.getByRole("button", { name: "+ Add special activity" }));
    fireEvent.change(screen.getByLabelText("Special activity name"), { target: { value: "Chess Club" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mockedCreateSpecialActivity).toHaveBeenCalledWith("s1", "y1", { name: "Chess Club" });
    });
    await screen.findByText("Chess Club");
    expect(screen.queryByLabelText("Special activity name")).not.toBeInTheDocument();
  });

  it("switches a row into inline edit mode and sends the exact update request", async () => {
    mockedGetSpecialActivities.mockResolvedValueOnce(TWO_ACTIVITIES);
    mockedUpdateSpecialActivity.mockResolvedValue({ id: "club_debate", name: "Debate" });
    mockedGetSpecialActivities.mockResolvedValueOnce({
      configuration_locked: false,
      special_activities: [{ id: "club_debate", name: "Debate" }, TWO_ACTIVITIES.special_activities[1]!],
    });

    render(<SpecialActivitiesPanel />);
    await screen.findByText("Debate Club");

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);
    const nameInputs = screen.getAllByLabelText("Special activity name");
    fireEvent.change(nameInputs[nameInputs.length - 1]!, { target: { value: "Debate" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mockedUpdateSpecialActivity).toHaveBeenCalledWith("s1", "y1", "club_debate", { name: "Debate" });
    });
    await screen.findByText("Debate");
  });

  it("edit Cancel discards local edit state", async () => {
    mockedGetSpecialActivities.mockResolvedValue(TWO_ACTIVITIES);
    render(<SpecialActivitiesPanel />);
    await screen.findByText("Debate Club");

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);
    const nameInputs = screen.getAllByLabelText("Special activity name");
    fireEvent.change(nameInputs[nameInputs.length - 1]!, { target: { value: "Changed" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(mockedUpdateSpecialActivity).not.toHaveBeenCalled();
    expect(screen.getByText("Debate Club")).toBeInTheDocument();
  });

  it("maps DUPLICATE_SPECIAL_ACTIVITY to a clear inline message", async () => {
    mockedGetSpecialActivities.mockResolvedValue(TWO_ACTIVITIES);
    mockedCreateSpecialActivity.mockRejectedValue(
      new ApiError(409, "A special activity named Debate Club already exists", "DUPLICATE_SPECIAL_ACTIVITY"),
    );

    render(<SpecialActivitiesPanel />);
    await screen.findByText("Debate Club");

    fireEvent.click(screen.getByRole("button", { name: "+ Add special activity" }));
    fireEvent.change(screen.getByLabelText("Special activity name"), { target: { value: "Debate Club" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("A special activity with this name already exists.");
  });

  it("delete confirm/cancel: cancel leaves the special activity intact", async () => {
    mockedGetSpecialActivities.mockResolvedValue(TWO_ACTIVITIES);
    render(<SpecialActivitiesPanel />);
    await screen.findByText("Debate Club");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    expect(screen.getByText("Delete Debate Club?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(mockedDeleteSpecialActivity).not.toHaveBeenCalled();
    expect(screen.queryByText("Delete Debate Club?")).not.toBeInTheDocument();
  });

  it("maps SPECIAL_ACTIVITY_IN_USE to a human message without leaking RESERVED_BLOCK", async () => {
    mockedGetSpecialActivities.mockResolvedValue(TWO_ACTIVITIES);
    mockedDeleteSpecialActivity.mockRejectedValue(
      new ApiError(409, "Special activity is referenced elsewhere", "SPECIAL_ACTIVITY_IN_USE", {
        referenced_by: ["RESERVED_BLOCK"],
      }),
    );

    render(<SpecialActivitiesPanel />);
    await screen.findByText("Debate Club");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await screen.findByText(
      "This special activity can't be deleted because it is used by: Reserved activities.",
    );
    expect(document.body.textContent ?? "").not.toContain("RESERVED_BLOCK");
  });

  it("disables Add/Edit/Delete when configuration is locked", async () => {
    mockedGetSpecialActivities.mockResolvedValue(LOCKED_PROJECTION);

    render(<SpecialActivitiesPanel />);
    await screen.findByText("Debate Club");

    expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Add special activity" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("on a lock race during create, refetches into the locked state", async () => {
    mockedGetSpecialActivities.mockResolvedValueOnce(TWO_ACTIVITIES);
    mockedCreateSpecialActivity.mockRejectedValue(new ApiError(409, "locked", "SCHEDULING_CONFIGURATION_LOCKED"));
    mockedGetSpecialActivities.mockResolvedValueOnce(LOCKED_PROJECTION);

    render(<SpecialActivitiesPanel />);
    await screen.findByText("Debate Club");

    fireEvent.click(screen.getByRole("button", { name: "+ Add special activity" }));
    fireEvent.change(screen.getByLabelText("Special activity name"), { target: { value: "Chess Club" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText(/A schedule was generated since this page loaded/);
    await waitFor(() => {
      expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    });
    expect(screen.queryByLabelText("Special activity name")).not.toBeInTheDocument();
  });

  it("on a successful write whose authoritative refetch fails, shows the stale banner with Retry", async () => {
    mockedGetSpecialActivities.mockResolvedValueOnce(TWO_ACTIVITIES);
    mockedCreateSpecialActivity.mockResolvedValue({ id: "club_chess", name: "Chess Club" });
    mockedGetSpecialActivities.mockRejectedValueOnce(new ApiError(500, "Request failed."));
    mockedGetSpecialActivities.mockResolvedValueOnce({
      configuration_locked: false,
      special_activities: [...TWO_ACTIVITIES.special_activities, { id: "club_chess", name: "Chess Club" }],
    });

    render(<SpecialActivitiesPanel />);
    await screen.findByText("Debate Club");

    fireEvent.click(screen.getByRole("button", { name: "+ Add special activity" }));
    fireEvent.change(screen.getByLabelText("Special activity name"), { target: { value: "Chess Club" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText(/could not be refreshed/);
    expect(screen.getByRole("button", { name: "+ Add special activity" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Chess Club");
  });

  it("never renders a kind field or any Club concept", async () => {
    mockedGetSpecialActivities.mockResolvedValue(TWO_ACTIVITIES);
    render(<SpecialActivitiesPanel />);
    await screen.findByText("Debate Club");

    const bodyText = document.body.textContent ?? "";
    expect(bodyText).not.toContain("ORDINARY");
    expect(bodyText).not.toContain("CLUB");
    expect(bodyText).not.toContain("kind");
    expect(bodyText).not.toContain("club_debate");
  });

  it("does not double-submit while a create is in flight", async () => {
    mockedGetSpecialActivities.mockResolvedValue(TWO_ACTIVITIES);
    const { promise, resolve } = deferred<{ id: string; name: string }>();
    mockedCreateSpecialActivity.mockReturnValue(promise);

    render(<SpecialActivitiesPanel />);
    await screen.findByText("Debate Club");

    fireEvent.click(screen.getByRole("button", { name: "+ Add special activity" }));
    fireEvent.change(screen.getByLabelText("Special activity name"), { target: { value: "Chess Club" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(screen.getByRole("button", { name: "Saving…" }));

    expect(mockedCreateSpecialActivity).toHaveBeenCalledTimes(1);
    resolve({ id: "club_chess", name: "Chess Club" });
  });
});
