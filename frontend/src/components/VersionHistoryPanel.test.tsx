import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import VersionHistoryPanel from "./VersionHistoryPanel";
import { ApiError } from "../api/client";
import { getScheduleVersionHistory } from "../api/scheduleVersions";
import type { ScheduleVersionHistoryResponse } from "../api/types";

vi.mock("../api/scheduleVersions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/scheduleVersions")>();
  return {
    ...actual,
    getScheduleVersionHistory: vi.fn(),
  };
});

const mockedGetScheduleVersionHistory = vi.mocked(getScheduleVersionHistory);

const HISTORY: ScheduleVersionHistoryResponse = {
  versions: [
    {
      version_number: 3, created_at: "2026-01-03T00:00:00Z", solver_status: "FEASIBLE",
      total_soft_penalty: 5, is_active: true, parent_version_number: 2,
    },
    {
      version_number: 2, created_at: "2026-01-02T00:00:00Z", solver_status: "OPTIMAL",
      total_soft_penalty: 0, is_active: false, parent_version_number: 1,
    },
    {
      version_number: 1, created_at: "2026-01-01T00:00:00Z", solver_status: "OPTIMAL",
      total_soft_penalty: 0, is_active: false, parent_version_number: null,
    },
  ],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("VersionHistoryPanel", () => {
  it("shows a loading state while the history request is in flight", () => {
    const { promise } = deferred<ScheduleVersionHistoryResponse>();
    mockedGetScheduleVersionHistory.mockReturnValue(promise);

    render(
      <VersionHistoryPanel
        schoolId="s1"
        academicYearId="y1"
        refreshToken={0}
        viewingVersionNumber={null}
        onSelectVersion={() => {}}
      />,
    );

    expect(screen.getByText("Loading version history…")).toBeInTheDocument();
  });

  it("lists every version newest-first", async () => {
    mockedGetScheduleVersionHistory.mockResolvedValue(HISTORY);

    render(
      <VersionHistoryPanel
        schoolId="s1"
        academicYearId="y1"
        refreshToken={0}
        viewingVersionNumber={null}
        onSelectVersion={() => {}}
      />,
    );

    await screen.findByText("Version 3");
    const rows = screen.getAllByRole("button");
    const headings = rows.map((row) => row.querySelector(".version-history-row-heading")?.textContent);
    expect(headings).toEqual(["Version 3ACTIVE", "Version 2", "Version 1"]);
  });

  it("shows exactly one ACTIVE badge, on the active version", async () => {
    mockedGetScheduleVersionHistory.mockResolvedValue(HISTORY);

    render(
      <VersionHistoryPanel
        schoolId="s1"
        academicYearId="y1"
        refreshToken={0}
        viewingVersionNumber={null}
        onSelectVersion={() => {}}
      />,
    );

    await screen.findByText("Version 3");
    expect(screen.getAllByText("ACTIVE")).toHaveLength(1);
    const rows = screen.getAllByRole("button");
    expect(within(rows[0]!).queryByText("ACTIVE")).toBeInTheDocument();
    expect(within(rows[1]!).queryByText("ACTIVE")).not.toBeInTheDocument();
  });

  it("marks the version currently being viewed with aria-current", async () => {
    mockedGetScheduleVersionHistory.mockResolvedValue(HISTORY);

    render(
      <VersionHistoryPanel
        schoolId="s1"
        academicYearId="y1"
        refreshToken={0}
        viewingVersionNumber={2}
        onSelectVersion={() => {}}
      />,
    );

    await screen.findByText("Version 3");
    const rows = screen.getAllByRole("button");
    expect(rows[1]).toHaveAttribute("aria-current", "true");
    expect(rows[0]).not.toHaveAttribute("aria-current");
  });

  it("marks the active version as currently viewed when viewingVersionNumber is null", async () => {
    mockedGetScheduleVersionHistory.mockResolvedValue(HISTORY);

    render(
      <VersionHistoryPanel
        schoolId="s1"
        academicYearId="y1"
        refreshToken={0}
        viewingVersionNumber={null}
        onSelectVersion={() => {}}
      />,
    );

    await screen.findByText("Version 3");
    const rows = screen.getAllByRole("button");
    expect(rows[0]).toHaveAttribute("aria-current", "true"); // version 3 is active
  });

  it("reports the clicked version's number", async () => {
    mockedGetScheduleVersionHistory.mockResolvedValue(HISTORY);
    const onSelectVersion = vi.fn();

    render(
      <VersionHistoryPanel
        schoolId="s1"
        academicYearId="y1"
        refreshToken={0}
        viewingVersionNumber={null}
        onSelectVersion={onSelectVersion}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /Version 2/ }));

    expect(onSelectVersion).toHaveBeenCalledWith(2);
  });

  it("shows a safe error message on failure", async () => {
    mockedGetScheduleVersionHistory.mockRejectedValue(new ApiError(404, "Active schedule not found"));

    render(
      <VersionHistoryPanel
        schoolId="s1"
        academicYearId="y1"
        refreshToken={0}
        viewingVersionNumber={null}
        onSelectVersion={() => {}}
      />,
    );

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Active schedule not found"));
  });

  it("re-fetches when refreshToken changes", async () => {
    mockedGetScheduleVersionHistory.mockResolvedValue(HISTORY);

    const { rerender } = render(
      <VersionHistoryPanel
        schoolId="s1"
        academicYearId="y1"
        refreshToken={0}
        viewingVersionNumber={null}
        onSelectVersion={() => {}}
      />,
    );
    await screen.findByText("Version 3");
    expect(mockedGetScheduleVersionHistory).toHaveBeenCalledTimes(1);

    rerender(
      <VersionHistoryPanel
        schoolId="s1"
        academicYearId="y1"
        refreshToken={1}
        viewingVersionNumber={null}
        onSelectVersion={() => {}}
      />,
    );

    await waitFor(() => expect(mockedGetScheduleVersionHistory).toHaveBeenCalledTimes(2));
  });
});
