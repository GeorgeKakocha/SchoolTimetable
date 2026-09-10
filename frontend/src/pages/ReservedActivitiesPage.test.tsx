import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ReservedActivitiesPage from "./ReservedActivitiesPage";
import { ApiError } from "../api/client";
import {
  createReservedActivity,
  deleteReservedActivity,
  getReservedActivities,
  updateReservedActivity,
} from "../api/reservedActivities";
import type { ReservedActivitiesProjectionResponse } from "../api/reservedActivities";
import { loadAppConfig } from "../config/appConfig";

vi.mock("../api/reservedActivities", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/reservedActivities")>();
  return {
    ...actual,
    getReservedActivities: vi.fn(),
    createReservedActivity: vi.fn(),
    updateReservedActivity: vi.fn(),
    deleteReservedActivity: vi.fn(),
  };
});

vi.mock("../config/appConfig", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../config/appConfig")>();
  return { ...actual, loadAppConfig: vi.fn() };
});

const mockedGetReservedActivities = vi.mocked(getReservedActivities);
const mockedCreateReservedActivity = vi.mocked(createReservedActivity);
const mockedUpdateReservedActivity = vi.mocked(updateReservedActivity);
const mockedDeleteReservedActivity = vi.mocked(deleteReservedActivity);
const mockedLoadAppConfig = vi.mocked(loadAppConfig);

function renderPage() {
  return render(
    <MemoryRouter>
      <ReservedActivitiesPage />
    </MemoryRouter>,
  );
}

const BASE_PROJECTION: ReservedActivitiesProjectionResponse = {
  configuration_locked: false,
  special_activities: [{ id: "club_debate", name: "Debate Club" }],
  teachers: [{ id: "teacher_a", name: "Maia Beridze" }],
  class_sections: [{ id: "class_8a", name: "8A" }, { id: "class_8b", name: "8B" }],
  days: [{ id: "mon", name: "Monday", index: 0 }],
  periods: [
    { id: "p1", name: "P1", index: 0, is_instructional: true },
    { id: "break", name: "Break", index: 1, is_instructional: false },
  ],
  reserved_activities: [],
  resources: [{ id: "gym", name: "Gym", capacity: 1 }],
};

const ONE_RESERVED_ACTIVITY: ReservedActivitiesProjectionResponse = {
  ...BASE_PROJECTION,
  reserved_activities: [
    {
      id: "reserved_block_1",
      special_activity_id: "club_debate",
      class_section_ids: ["class_8a"],
      teacher_id: null,
      slots: [{ day_id: "mon", period_id: "p1" }],
      resource_id: null,
    },
  ],
};

const TWO_RESERVED_ACTIVITIES: ReservedActivitiesProjectionResponse = {
  ...BASE_PROJECTION,
  special_activities: [
    { id: "club_debate", name: "Debate Club" },
    { id: "club_art", name: "Art Club" },
  ],
  reserved_activities: [
    {
      id: "reserved_block_1",
      special_activity_id: "club_debate",
      class_section_ids: ["class_8a"],
      teacher_id: null,
      slots: [{ day_id: "mon", period_id: "p1" }],
      resource_id: null,
    },
    {
      id: "reserved_block_2",
      special_activity_id: "club_art",
      class_section_ids: ["class_8b"],
      teacher_id: null,
      slots: [{ day_id: "mon", period_id: "p1" }],
      resource_id: null,
    },
  ],
};

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("ReservedActivitiesPage", () => {
  it("shows a loading state, then the page title and help text", async () => {
    mockedGetReservedActivities.mockResolvedValue(BASE_PROJECTION);
    renderPage();

    expect(screen.getByText("Loading reserved activities…")).toBeInTheDocument();
    await screen.findByRole("heading", { name: "Reserved Activities", level: 1 });
    expect(screen.getByText(/fixed at the exact classes, teacher, and time slots/)).toBeInTheDocument();
  });

  it("shows a fatal error with Retry on initial load failure", async () => {
    mockedGetReservedActivities.mockRejectedValueOnce(new ApiError(500, "Request failed."));
    mockedGetReservedActivities.mockResolvedValueOnce(BASE_PROJECTION);
    renderPage();

    await screen.findByText("Request failed.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await screen.findByRole("button", { name: "+ Add reserved activity" });
  });

  describe("prerequisite states", () => {
    it("shows a prerequisite surface with a School Setup link when there are zero Special Activities", async () => {
      mockedGetReservedActivities.mockResolvedValue({ ...BASE_PROJECTION, special_activities: [] });
      renderPage();

      await screen.findByText("No Special Activities yet.");
      expect(screen.getByText("Create a Special Activity before adding a Reserved Activity.")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "+ Add reserved activity" })).not.toBeInTheDocument();

      const link = screen.getByRole("link", { name: "Go to School Setup" });
      expect(link).toHaveAttribute("href", "/configuration/setup");
    });

    it("shows a prerequisite surface with a School Setup link when there are zero Classes", async () => {
      mockedGetReservedActivities.mockResolvedValue({ ...BASE_PROJECTION, class_sections: [] });
      renderPage();

      await screen.findByText("No Classes yet.");
      expect(screen.getByText("Create a Class before adding a Reserved Activity.")).toBeInTheDocument();
    });

    it("shows a prerequisite surface with no navigation destination when there are zero instructional periods", async () => {
      mockedGetReservedActivities.mockResolvedValue({
        ...BASE_PROJECTION,
        periods: [{ id: "break", name: "Break", index: 0, is_instructional: false }],
      });
      renderPage();

      await screen.findByText("No instructional scheduling slots yet.");
      expect(screen.queryByRole("link", { name: "Go to School Setup" })).not.toBeInTheDocument();
    });

    it("combines multiple missing prerequisites into one surface, not competing empty states", async () => {
      mockedGetReservedActivities.mockResolvedValue({ ...BASE_PROJECTION, special_activities: [], class_sections: [] });
      renderPage();

      await screen.findByText("No Special Activities yet.");
      expect(screen.getByText("No Classes yet.")).toBeInTheDocument();
      expect(screen.getAllByRole("link", { name: "Go to School Setup" })).toHaveLength(2);
    });

    it("existing reserved activity cards remain visible even when prerequisites are missing", async () => {
      mockedGetReservedActivities.mockResolvedValue({
        ...ONE_RESERVED_ACTIVITY,
        periods: [{ id: "break", name: "Break", index: 0, is_instructional: false }],
      });
      renderPage();

      await screen.findByText("No instructional scheduling slots yet.");
      expect(screen.getByText("Debate Club")).toBeInTheDocument();
    });

    it("locked state takes precedence over missing prerequisites", async () => {
      mockedGetReservedActivities.mockResolvedValue({
        ...BASE_PROJECTION,
        special_activities: [],
        configuration_locked: true,
      });
      renderPage();

      await screen.findByText(/Scheduling configuration is locked/);
      expect(screen.queryByText("No Special Activities yet.")).not.toBeInTheDocument();
    });
  });

  describe("add flow", () => {
    it("Add opens a blank editor with no Special Activity preselected", async () => {
      mockedGetReservedActivities.mockResolvedValue(BASE_PROJECTION);
      renderPage();
      await screen.findByRole("button", { name: "+ Add reserved activity" });

      fireEvent.click(screen.getByRole("button", { name: "+ Add reserved activity" }));

      expect(screen.getByRole("heading", { name: "Add reserved activity" })).toBeInTheDocument();
      expect(screen.getByRole("combobox", { name: "Special Activity" })).toHaveValue("");
    });

    it("submits the exact create request body, closes the editor, and refetches on success", async () => {
      mockedGetReservedActivities.mockResolvedValueOnce(BASE_PROJECTION);
      mockedCreateReservedActivity.mockResolvedValue({
        id: "reserved_block_1",
        special_activity_id: "club_debate",
        class_section_ids: ["class_8a"],
        teacher_id: null,
        slots: [{ day_id: "mon", period_id: "p1" }],
        resource_id: null,
      });
      mockedGetReservedActivities.mockResolvedValueOnce(ONE_RESERVED_ACTIVITY);

      renderPage();
      await screen.findByRole("button", { name: "+ Add reserved activity" });
      fireEvent.click(screen.getByRole("button", { name: "+ Add reserved activity" }));

      fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_debate" } });
      fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
      fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(mockedCreateReservedActivity).toHaveBeenCalledWith("s1", "y1", {
          special_activity_id: "club_debate",
          class_section_ids: ["class_8a"],
          teacher_id: null,
          slots: [{ day_id: "mon", period_id: "p1" }],
          resource_id: null,
        });
      });
      await screen.findByText("Debate Club");
      expect(screen.queryByRole("heading", { name: "Add reserved activity" })).not.toBeInTheDocument();
    });

    it("does not render a non-instructional period in the slot grid", async () => {
      mockedGetReservedActivities.mockResolvedValue(BASE_PROJECTION);
      renderPage();
      await screen.findByRole("button", { name: "+ Add reserved activity" });
      fireEvent.click(screen.getByRole("button", { name: "+ Add reserved activity" }));

      expect(document.getElementById("reserved-slot-desktop-mon-break")).toBeNull();
    });
  });

  describe("edit flow", () => {
    it("Edit opens the editor populated with the existing record, and PUTs the full aggregate", async () => {
      mockedGetReservedActivities.mockResolvedValueOnce(ONE_RESERVED_ACTIVITY);
      mockedUpdateReservedActivity.mockResolvedValue({
        id: "reserved_block_1",
        special_activity_id: "club_debate",
        class_section_ids: ["class_8a", "class_8b"],
        teacher_id: null,
        slots: [{ day_id: "mon", period_id: "p1" }],
        resource_id: null,
      });
      mockedGetReservedActivities.mockResolvedValueOnce({
        ...ONE_RESERVED_ACTIVITY,
        reserved_activities: [
          { ...ONE_RESERVED_ACTIVITY.reserved_activities[0]!, class_section_ids: ["class_8a", "class_8b"] },
        ],
      });

      renderPage();
      await screen.findByText("Debate Club");

      fireEvent.click(screen.getByRole("button", { name: "Edit" }));
      expect(screen.getByRole("heading", { name: "Edit reserved activity" })).toBeInTheDocument();
      expect(screen.getByRole("combobox", { name: "Special Activity" })).toHaveValue("club_debate");
      expect(screen.getByRole("checkbox", { name: "8A" })).toBeChecked();

      fireEvent.click(screen.getByRole("checkbox", { name: "8B" }));
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(mockedUpdateReservedActivity).toHaveBeenCalledWith("s1", "y1", "reserved_block_1", {
          special_activity_id: "club_debate",
          class_section_ids: ["class_8a", "class_8b"],
          teacher_id: null,
          slots: [{ day_id: "mon", period_id: "p1" }],
          resource_id: null,
        });
      });
    });
  });

  it("one editor at a time: Add and other row actions are disabled while the editor is open", async () => {
    mockedGetReservedActivities.mockResolvedValue(ONE_RESERVED_ACTIVITY);
    renderPage();
    await screen.findByText("Debate Club");

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("one editor at a time: opening Edit for one record disables Add, the OTHER record's Edit, and BOTH records' Delete", async () => {
    mockedGetReservedActivities.mockResolvedValue(TWO_RESERVED_ACTIVITIES);
    renderPage();
    await screen.findByText("Debate Club");
    await screen.findByText("Art Club");

    const editButtons = screen.getAllByRole("button", { name: "Edit" });
    const deleteButtons = screen.getAllByRole("button", { name: "Delete" });
    expect(editButtons).toHaveLength(2);
    expect(deleteButtons).toHaveLength(2);

    // Open the editor for record A (Debate Club, the first card).
    fireEvent.click(editButtons[0]!);
    expect(screen.getByRole("heading", { name: "Edit reserved activity" })).toBeInTheDocument();

    // Add remains visible but disabled while the editor is open.
    expect(screen.getByRole("button", { name: "+ Add reserved activity" })).toBeDisabled();

    // Every row's Edit is now disabled, including record B's (the one NOT being edited) --
    // there is no "Edit" button left enabled anywhere on the page.
    for (const button of screen.getAllByRole("button", { name: "Edit" })) {
      expect(button).toBeDisabled();
    }
    // Every row's Delete is disabled too, for both record A and record B.
    for (const button of screen.getAllByRole("button", { name: "Delete" })) {
      expect(button).toBeDisabled();
    }

    // Only the open editor's own Save/Cancel remain actionable mutation decisions.
    expect(screen.getByRole("button", { name: "Cancel" })).not.toBeDisabled();
    // Save starts disabled here only because the draft is unchanged from
    // the loaded record (edit-mode dirty gate) -- it is not globally
    // locked out; adding a class (keeping the set non-empty) re-enables it.
    fireEvent.click(screen.getByRole("checkbox", { name: "8B" }));
    expect(screen.getByRole("button", { name: "Save" })).not.toBeDisabled();
  });

  describe("write error handling", () => {
    it("maps RESERVED_BLOCK_TEACHER_UNAVAILABLE and preserves the editor draft", async () => {
      mockedGetReservedActivities.mockResolvedValue(BASE_PROJECTION);
      mockedCreateReservedActivity.mockRejectedValue(
        new ApiError(422, "invalid", "INVALID_RESERVED_ACTIVITY", {
          errors: [
            {
              code: "RESERVED_BLOCK_TEACHER_UNAVAILABLE",
              message: "teacher unavailable",
              context: { teacher_id: "teacher_a", day_id: "mon", period_id: "p1" },
            },
          ],
        }),
      );

      renderPage();
      await screen.findByRole("button", { name: "+ Add reserved activity" });
      fireEvent.click(screen.getByRole("button", { name: "+ Add reserved activity" }));
      fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_debate" } });
      fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
      fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await screen.findByText("Maia Beridze is unavailable on Monday P1.");
      // Draft preserved: the editor is still open with the same selection.
      expect(screen.getByRole("combobox", { name: "Special Activity" })).toHaveValue("club_debate");
    });

    it("resolves a class-slot collision to the conflicting reservation's Special Activity name", async () => {
      const projectionWithConflict: ReservedActivitiesProjectionResponse = {
        ...BASE_PROJECTION,
        special_activities: [{ id: "club_debate", name: "Debate Club" }, { id: "club_art", name: "Art Club" }],
        reserved_activities: [
          {
            id: "reserved_block_other",
            special_activity_id: "club_debate",
            class_section_ids: ["class_8a"],
            teacher_id: null,
            slots: [{ day_id: "mon", period_id: "p1" }],
            resource_id: null,
          },
        ],
      };
      mockedGetReservedActivities.mockResolvedValue(projectionWithConflict);
      mockedCreateReservedActivity.mockRejectedValue(
        new ApiError(422, "invalid", "INVALID_RESERVED_ACTIVITY", {
          errors: [
            {
              code: "RESERVED_BLOCK_CLASS_SLOT_COLLISION",
              message: "collision",
              context: {
                conflicting_reserved_block_id: "reserved_block_other",
                class_section_id: "class_8a",
                day_id: "mon",
                period_id: "p1",
              },
            },
          ],
        }),
      );

      renderPage();
      await screen.findByText("Debate Club");
      fireEvent.click(screen.getByRole("button", { name: "+ Add reserved activity" }));
      fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_art" } });
      fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
      fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await screen.findByText("Conflicts with the existing Debate Club reservation: 8A, Monday P1.");
    });

    it("falls back to a generic collision message when the conflicting block cannot be resolved", async () => {
      mockedGetReservedActivities.mockResolvedValue(BASE_PROJECTION);
      mockedCreateReservedActivity.mockRejectedValue(
        new ApiError(422, "invalid", "INVALID_RESERVED_ACTIVITY", {
          errors: [
            {
              code: "RESERVED_BLOCK_CLASS_SLOT_COLLISION",
              message: "collision",
              context: {
                conflicting_reserved_block_id: "reserved_block_gone",
                class_section_id: "class_8a",
                day_id: "mon",
                period_id: "p1",
              },
            },
          ],
        }),
      );

      renderPage();
      await screen.findByRole("button", { name: "+ Add reserved activity" });
      fireEvent.click(screen.getByRole("button", { name: "+ Add reserved activity" }));
      fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_debate" } });
      fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
      fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await screen.findByText("Conflicts with another existing Reserved Activity.");
      expect(document.body.textContent ?? "").not.toContain("reserved_block_gone");
    });

    it("resolves a RESERVED_RESOURCE_CAPACITY_EXCEEDED diagnostic to a friendly, resource-named message", async () => {
      mockedGetReservedActivities.mockResolvedValue(BASE_PROJECTION);
      mockedCreateReservedActivity.mockRejectedValue(
        new ApiError(422, "invalid", "INVALID_RESERVED_ACTIVITY", {
          errors: [
            {
              code: "RESERVED_RESOURCE_CAPACITY_EXCEEDED",
              message: "capacity exceeded",
              context: { resource_id: "gym", day_id: "mon", period_id: "p1", capacity: 1, reserved_usage: 2 },
            },
          ],
        }),
      );

      renderPage();
      await screen.findByRole("button", { name: "+ Add reserved activity" });
      fireEvent.click(screen.getByRole("button", { name: "+ Add reserved activity" }));
      fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_debate" } });
      fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
      fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
      fireEvent.change(screen.getByRole("combobox", { name: "Resource" }), { target: { value: "gym" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await screen.findByText("Gym is already fully booked at Monday P1 (capacity 1).");
    });

    it("on UNKNOWN_REFERENCE, closes the editor, discards the draft, refetches, and shows a transient message", async () => {
      mockedGetReservedActivities.mockResolvedValueOnce(BASE_PROJECTION);
      mockedCreateReservedActivity.mockRejectedValue(
        new ApiError(422, "Unknown class_section", "UNKNOWN_REFERENCE", {
          reference_kind: "class_section",
          reference_id: "class_missing",
        }),
      );
      mockedGetReservedActivities.mockResolvedValueOnce(BASE_PROJECTION);

      renderPage();
      await screen.findByRole("button", { name: "+ Add reserved activity" });
      fireEvent.click(screen.getByRole("button", { name: "+ Add reserved activity" }));
      fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_debate" } });
      fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
      fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await screen.findByText("The configuration changed. Review the latest data and try again.");
      expect(screen.queryByRole("heading", { name: "Add reserved activity" })).not.toBeInTheDocument();
    });

    it("on NON_SPECIAL_ACTIVITY_TARGET, closes the editor and never shows raw kind vocabulary", async () => {
      mockedGetReservedActivities.mockResolvedValueOnce(BASE_PROJECTION);
      mockedCreateReservedActivity.mockRejectedValue(
        new ApiError(422, "Selected activity is not a special activity", "NON_SPECIAL_ACTIVITY_TARGET", {
          activity_id: "activity_math",
        }),
      );
      mockedGetReservedActivities.mockResolvedValueOnce(BASE_PROJECTION);

      renderPage();
      await screen.findByRole("button", { name: "+ Add reserved activity" });
      fireEvent.click(screen.getByRole("button", { name: "+ Add reserved activity" }));
      fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_debate" } });
      fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
      fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      await screen.findByText("The configuration changed. Review the latest data and try again.");
      const bodyText = document.body.textContent ?? "";
      expect(bodyText).not.toContain("CLUB");
      expect(bodyText).not.toContain("ORDINARY");
    });
  });

  it("on a successful write whose authoritative refetch fails, shows the stale banner, keeps the editor closed, and disables Add", async () => {
    mockedGetReservedActivities.mockResolvedValueOnce(BASE_PROJECTION);
    mockedCreateReservedActivity.mockResolvedValue({
      id: "reserved_block_1",
      special_activity_id: "club_debate",
      class_section_ids: ["class_8a"],
      teacher_id: null,
      slots: [{ day_id: "mon", period_id: "p1" }],
      resource_id: null,
    });
    mockedGetReservedActivities.mockRejectedValueOnce(new ApiError(500, "Request failed."));
    mockedGetReservedActivities.mockResolvedValueOnce(ONE_RESERVED_ACTIVITY);

    renderPage();
    await screen.findByRole("button", { name: "+ Add reserved activity" });
    fireEvent.click(screen.getByRole("button", { name: "+ Add reserved activity" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_debate" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
    fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText(/could not be refreshed/);
    expect(screen.queryByRole("heading", { name: "Add reserved activity" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Debate Club");
  });

  it("on a lock race during create, closes the editor and refetches into the locked state", async () => {
    mockedGetReservedActivities.mockResolvedValueOnce(BASE_PROJECTION);
    mockedCreateReservedActivity.mockRejectedValue(
      new ApiError(409, "locked", "SCHEDULING_CONFIGURATION_LOCKED"),
    );
    mockedGetReservedActivities.mockResolvedValueOnce({ ...BASE_PROJECTION, configuration_locked: true });

    renderPage();
    await screen.findByRole("button", { name: "+ Add reserved activity" });
    fireEvent.click(screen.getByRole("button", { name: "+ Add reserved activity" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Special Activity" }), { target: { value: "club_debate" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "8A" }));
    fireEvent.click(document.getElementById("reserved-slot-desktop-mon-p1")!);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText(/A schedule was generated since this page loaded/);
    await waitFor(() => {
      expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    });
    expect(screen.queryByRole("heading", { name: "Add reserved activity" })).not.toBeInTheDocument();
  });

  describe("delete flow", () => {
    it("shows inline confirmation; Cancel leaves the record intact", async () => {
      mockedGetReservedActivities.mockResolvedValue(ONE_RESERVED_ACTIVITY);
      renderPage();
      await screen.findByText("Debate Club");

      fireEvent.click(screen.getByRole("button", { name: "Delete" }));
      expect(screen.getByText("Delete this reserved activity?")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

      expect(mockedDeleteReservedActivity).not.toHaveBeenCalled();
      expect(screen.getByText("Debate Club")).toBeInTheDocument();
    });

    it("on success, refetches the authoritative projection", async () => {
      mockedGetReservedActivities.mockResolvedValueOnce(ONE_RESERVED_ACTIVITY);
      mockedDeleteReservedActivity.mockResolvedValue({ deleted_id: "reserved_block_1" });
      mockedGetReservedActivities.mockResolvedValueOnce(BASE_PROJECTION);

      renderPage();
      await screen.findByText("Debate Club");

      fireEvent.click(screen.getByRole("button", { name: "Delete" }));
      fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

      await waitFor(() => {
        expect(mockedDeleteReservedActivity).toHaveBeenCalledWith("s1", "y1", "reserved_block_1");
      });
      await screen.findByText("No reserved activities yet.");
    });

    it("a failed delete preserves the current view and shows an inline error", async () => {
      mockedGetReservedActivities.mockResolvedValue(ONE_RESERVED_ACTIVITY);
      mockedDeleteReservedActivity.mockRejectedValue(new ApiError(500, "Request failed."));

      renderPage();
      await screen.findByText("Debate Club");

      fireEvent.click(screen.getByRole("button", { name: "Delete" }));
      fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

      await screen.findByText("Request failed.");
      expect(screen.getByText("Debate Club")).toBeInTheDocument();
    });
  });

  it("locked page: cards readable, Add unavailable, Edit/Delete disabled", async () => {
    mockedGetReservedActivities.mockResolvedValue({ ...ONE_RESERVED_ACTIVITY, configuration_locked: true });
    renderPage();

    await screen.findByText("Debate Club");
    expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ Add reserved activity" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("shows both the desktop and mobile slot layout structures in the editor DOM", async () => {
    mockedGetReservedActivities.mockResolvedValue(BASE_PROJECTION);
    renderPage();
    await screen.findByRole("button", { name: "+ Add reserved activity" });
    fireEvent.click(screen.getByRole("button", { name: "+ Add reserved activity" }));

    expect(screen.getByTestId("reserved-slot-desktop-matrix")).toBeInTheDocument();
    expect(screen.getByTestId("reserved-slot-mobile-days")).toBeInTheDocument();
  });
});
