import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import {
  beginConfigurationDraft,
  discardConfigurationDraft,
  getConfigurationState,
} from "../api/configurationRevision";
import type { ConfigurationRevisionStateResponse } from "../api/types";
import ConfigurationLifecyclePanel from "./ConfigurationLifecyclePanel";

vi.mock("../api/configurationRevision", () => ({
  beginConfigurationDraft: vi.fn(),
  discardConfigurationDraft: vi.fn(),
  getConfigurationState: vi.fn(),
}));

const mockedGetState = vi.mocked(getConfigurationState);
const mockedBegin = vi.mocked(beginConfigurationDraft);
const mockedDiscard = vi.mocked(discardConfigurationDraft);

const PUBLISHED: ConfigurationRevisionStateResponse = {
  published_revision_number: 1,
  draft_revision_number: null,
  configuration_locked: true,
  timetable_out_of_date: false,
};
const DRAFT: ConfigurationRevisionStateResponse = {
  published_revision_number: 1,
  draft_revision_number: 2,
  configuration_locked: false,
  timetable_out_of_date: false,
};
const OUT_OF_DATE: ConfigurationRevisionStateResponse = { ...DRAFT, timetable_out_of_date: true };
const INITIAL_DRAFT: ConfigurationRevisionStateResponse = {
  published_revision_number: null,
  draft_revision_number: 1,
  configuration_locked: false,
  timetable_out_of_date: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedGetState.mockResolvedValue(PUBLISHED);
  mockedBegin.mockResolvedValue(DRAFT);
  mockedDiscard.mockResolvedValue(PUBLISHED);
});

function renderPanel(onProjectionRefresh = vi.fn()) {
  render(<ConfigurationLifecyclePanel schoolId="s1" academicYearId="y1" onProjectionRefresh={onProjectionRefresh} />);
  return onProjectionRefresh;
}

describe("ConfigurationLifecyclePanel", () => {
  it("shows the published locked state and opens one draft", async () => {
    const refresh = renderPanel();
    await screen.findByText(/Configuration is published and locked/);

    fireEvent.click(screen.getByRole("button", { name: "Edit configuration" }));
    expect(screen.getByRole("button", { name: "Opening draft…" })).toBeDisabled();

    await waitFor(() => expect(mockedBegin).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(/editable configuration draft is open/)).toBeInTheDocument());
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("shows an unchanged draft without a regenerate action", async () => {
    mockedGetState.mockResolvedValueOnce(DRAFT);
    renderPanel();
    await screen.findByText(/editable configuration draft is open/);
    expect(screen.getByText(/current published timetable remains active/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Regenerate/i })).not.toBeInTheDocument();
  });

  it("shows an out-of-date draft without a regenerate action", async () => {
    mockedGetState.mockResolvedValueOnce(OUT_OF_DATE);
    renderPanel();
    await screen.findByText(/Regeneration will be required/);
    expect(screen.getByText(/current timetable remains active/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Regenerate/i })).not.toBeInTheDocument();
  });

  it("requires confirmation and sends exactly one discard request", async () => {
    mockedGetState.mockResolvedValue(DRAFT);
    const refresh = renderPanel();
    await screen.findByRole("button", { name: "Discard draft" });

    fireEvent.click(screen.getByRole("button", { name: "Discard draft" }));
    expect(screen.getByText(/unpublished configuration changes/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(mockedDiscard).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Discard draft" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm discard" }));
    await waitFor(() => expect(mockedDiscard).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "Edit configuration" })).toBeInTheDocument());
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("does not offer discard for the protected initial draft", async () => {
    mockedGetState.mockResolvedValue(INITIAL_DRAFT);
    renderPanel();
    await screen.findByText(/initial configuration draft/);
    expect(screen.queryByRole("button", { name: "Discard draft" })).not.toBeInTheDocument();
  });

  it("renders safe state and mutation errors", async () => {
    mockedGetState.mockRejectedValueOnce(new ApiError(500, "Could not load state"));
    renderPanel();
    await screen.findByText("Could not load state");

    mockedGetState.mockResolvedValue(PUBLISHED);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("button", { name: "Edit configuration" });
    mockedBegin.mockRejectedValueOnce(new ApiError(409, "State changed", "SCHEDULING_CONFIGURATION_LOCKED"));
    fireEvent.click(screen.getByRole("button", { name: "Edit configuration" }));
    await screen.findByText("State changed");
    expect(mockedGetState).toHaveBeenCalledTimes(3);
  });

  it("explains protected-draft rejection if stale UI reaches the backend", async () => {
    mockedGetState.mockResolvedValue(DRAFT);
    mockedDiscard.mockRejectedValueOnce(
      new ApiError(409, "initial draft", "INITIAL_DRAFT_CANNOT_BE_DISCARDED"),
    );
    renderPanel();
    await screen.findByRole("button", { name: "Discard draft" });
    fireEvent.click(screen.getByRole("button", { name: "Discard draft" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm discard" }));
    await screen.findByText(/required before the first timetable/);
  });
});
