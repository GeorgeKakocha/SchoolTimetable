import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RoomsResourcesPanel from "./RoomsResourcesPanel";
import { ApiError } from "../../api/client";
import { createResource, deleteResource, getResources, updateResource } from "../../api/resources";
import { loadAppConfig } from "../../config/appConfig";
import type { ResourcesProjectionResponse } from "../../api/resources";

vi.mock("../../api/resources", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/resources")>();
  return {
    ...actual,
    getResources: vi.fn(),
    createResource: vi.fn(),
    updateResource: vi.fn(),
    deleteResource: vi.fn(),
  };
});

vi.mock("../../config/appConfig", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../config/appConfig")>();
  return {
    ...actual,
    loadAppConfig: vi.fn(),
  };
});

const mockedGetResources = vi.mocked(getResources);
const mockedCreateResource = vi.mocked(createResource);
const mockedUpdateResource = vi.mocked(updateResource);
const mockedDeleteResource = vi.mocked(deleteResource);
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

const TWO_RESOURCES: ResourcesProjectionResponse = {
  configuration_locked: false,
  resources: [
    { id: "resource_gym", name: "Gym", capacity: 1 },
    { id: "resource_lab", name: "Science Lab", capacity: 2 },
  ],
};

const EMPTY_PROJECTION: ResourcesProjectionResponse = { configuration_locked: false, resources: [] };

const LOCKED_PROJECTION: ResourcesProjectionResponse = {
  configuration_locked: true,
  resources: [{ id: "resource_gym", name: "Gym", capacity: 1 }],
};

beforeEach(() => {
  mockedLoadAppConfig.mockReturnValue({ schoolId: "s1", academicYearId: "y1" });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("RoomsResourcesPanel", () => {
  it("shows a loading state, then the resource list in server order with name and capacity", async () => {
    const { promise, resolve } = deferred<ResourcesProjectionResponse>();
    mockedGetResources.mockReturnValue(promise);

    render(<RoomsResourcesPanel />);
    expect(screen.getByText("Loading rooms and resources…")).toBeInTheDocument();

    resolve(TWO_RESOURCES);
    await screen.findByText("Gym");
    const rows = screen.getAllByRole("row");
    expect(rows[1]?.textContent).toContain("Gym");
    expect(rows[1]?.textContent).toContain("1");
    expect(rows[2]?.textContent).toContain("Science Lab");
    expect(rows[2]?.textContent).toContain("2");
  });

  it("never renders a raw ID", async () => {
    mockedGetResources.mockResolvedValue(TWO_RESOURCES);
    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    const bodyText = document.body.textContent ?? "";
    expect(bodyText).not.toContain("resource_gym");
    expect(bodyText).not.toContain("resource_lab");
  });

  it("shows the empty state with no dominant form when there are no resources", async () => {
    mockedGetResources.mockResolvedValue(EMPTY_PROJECTION);

    render(<RoomsResourcesPanel />);

    await screen.findByText("No Rooms & Resources yet.");
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "+ Add room or resource" })).toHaveLength(1);
  });

  it("reveals the create form (with capacity defaulting to 1) only after clicking Add", async () => {
    mockedGetResources.mockResolvedValue(TWO_RESOURCES);
    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ Add room or resource" }));
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByLabelText("Capacity")).toHaveValue(1);
  });

  it("shows capacity help text separated from the editable fields", async () => {
    mockedGetResources.mockResolvedValue(TWO_RESOURCES);
    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getByRole("button", { name: "+ Add room or resource" }));
    expect(screen.getByText(/maximum number of simultaneous uses/i)).toBeInTheDocument();
  });

  it("cannot submit with an invalid (zero) capacity", async () => {
    mockedGetResources.mockResolvedValue(TWO_RESOURCES);
    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getByRole("button", { name: "+ Add room or resource" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Music Room" } });
    fireEvent.change(screen.getByLabelText("Capacity"), { target: { value: "0" } });

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mockedCreateResource).not.toHaveBeenCalled();
  });

  it("sends the exact create request body (with default capacity), then collapses and refetches on success", async () => {
    mockedGetResources.mockResolvedValueOnce(TWO_RESOURCES);
    mockedCreateResource.mockResolvedValue({ id: "resource_music", name: "Music Room", capacity: 1 });
    mockedGetResources.mockResolvedValueOnce({
      configuration_locked: false,
      resources: [...TWO_RESOURCES.resources, { id: "resource_music", name: "Music Room", capacity: 1 }],
    });

    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getByRole("button", { name: "+ Add room or resource" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Music Room" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mockedCreateResource).toHaveBeenCalledWith("s1", "y1", { name: "Music Room", capacity: 1 });
    });
    await screen.findByText("Music Room");
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
  });

  it("shows a readable message when backend validation rejects the request", async () => {
    mockedGetResources.mockResolvedValue(TWO_RESOURCES);
    mockedCreateResource.mockRejectedValue(
      new ApiError(422, "invalid resource", "INVALID_RESOURCE", {
        errors: [{ code: "INVALID_RESOURCE_CAPACITY", message: "capacity must be at least 1" }],
      }),
    );

    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getByRole("button", { name: "+ Add room or resource" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Music Room" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("capacity must be at least 1");
  });

  it("maps DUPLICATE_RESOURCE to a clear inline message", async () => {
    mockedGetResources.mockResolvedValue(TWO_RESOURCES);
    mockedCreateResource.mockRejectedValue(
      new ApiError(409, "a resource named 'Gym' already exists", "DUPLICATE_RESOURCE"),
    );

    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getByRole("button", { name: "+ Add room or resource" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Gym" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("A room or resource with this name already exists.");
  });

  it("switches a row into inline edit mode and sends the exact update request for name", async () => {
    mockedGetResources.mockResolvedValueOnce(TWO_RESOURCES);
    mockedUpdateResource.mockResolvedValue({ id: "resource_gym", name: "Gymnasium", capacity: 1 });
    mockedGetResources.mockResolvedValueOnce({
      configuration_locked: false,
      resources: [{ id: "resource_gym", name: "Gymnasium", capacity: 1 }, TWO_RESOURCES.resources[1]!],
    });

    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);
    const nameInputs = screen.getAllByLabelText("Name");
    fireEvent.change(nameInputs[nameInputs.length - 1]!, { target: { value: "Gymnasium" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mockedUpdateResource).toHaveBeenCalledWith("s1", "y1", "resource_gym", { name: "Gymnasium", capacity: 1 });
    });
    await screen.findByText("Gymnasium");
  });

  it("edits capacity and sends the exact update request", async () => {
    mockedGetResources.mockResolvedValueOnce(TWO_RESOURCES);
    mockedUpdateResource.mockResolvedValue({ id: "resource_gym", name: "Gym", capacity: 2 });
    mockedGetResources.mockResolvedValueOnce({
      configuration_locked: false,
      resources: [{ id: "resource_gym", name: "Gym", capacity: 2 }, TWO_RESOURCES.resources[1]!],
    });

    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);
    const capacityInputs = screen.getAllByLabelText("Capacity");
    fireEvent.change(capacityInputs[capacityInputs.length - 1]!, { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mockedUpdateResource).toHaveBeenCalledWith("s1", "y1", "resource_gym", { name: "Gym", capacity: 2 });
    });
  });

  it("edit Cancel discards local edit state", async () => {
    mockedGetResources.mockResolvedValue(TWO_RESOURCES);
    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);
    const nameInputs = screen.getAllByLabelText("Name");
    fireEvent.change(nameInputs[nameInputs.length - 1]!, { target: { value: "Changed" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(mockedUpdateResource).not.toHaveBeenCalled();
    expect(screen.getByText("Gym")).toBeInTheDocument();
  });

  it("delete confirm/cancel: cancel leaves the resource intact", async () => {
    mockedGetResources.mockResolvedValue(TWO_RESOURCES);
    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    expect(screen.getByText("Delete Gym?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(mockedDeleteResource).not.toHaveBeenCalled();
    expect(screen.queryByText("Delete Gym?")).not.toBeInTheDocument();
  });

  it("delete success removes the row after refetch", async () => {
    mockedGetResources.mockResolvedValueOnce(TWO_RESOURCES);
    mockedDeleteResource.mockResolvedValue({ deleted_id: "resource_gym" });
    mockedGetResources.mockResolvedValueOnce({
      configuration_locked: false,
      resources: [TWO_RESOURCES.resources[1]!],
    });

    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await waitFor(() => {
      expect(screen.queryByText("Gym")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Science Lab")).toBeInTheDocument();
  });

  it("maps RESOURCE_IN_USE via Teaching Assignments to a friendly message without leaking backend vocabulary", async () => {
    mockedGetResources.mockResolvedValue(TWO_RESOURCES);
    mockedDeleteResource.mockRejectedValue(
      new ApiError(409, "Resource is referenced elsewhere", "RESOURCE_IN_USE", {
        referenced_by: ["TEACHING_REQUIREMENT"],
      }),
    );

    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await screen.findByText("This resource can't be deleted because it is used by: Teaching Assignments.");
    expect(document.body.textContent ?? "").not.toContain("TEACHING_REQUIREMENT");
  });

  it("maps RESOURCE_IN_USE via Reserved Activities to a friendly message without leaking backend vocabulary", async () => {
    mockedGetResources.mockResolvedValue(TWO_RESOURCES);
    mockedDeleteResource.mockRejectedValue(
      new ApiError(409, "Resource is referenced elsewhere", "RESOURCE_IN_USE", {
        referenced_by: ["RESERVED_BLOCK"],
      }),
    );

    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await screen.findByText("This resource can't be deleted because it is used by: Reserved Activities.");
    expect(document.body.textContent ?? "").not.toContain("RESERVED_BLOCK");
  });

  it("maps RESOURCE_IN_USE via both Teaching Assignments and Reserved Activities", async () => {
    mockedGetResources.mockResolvedValue(TWO_RESOURCES);
    mockedDeleteResource.mockRejectedValue(
      new ApiError(409, "Resource is referenced elsewhere", "RESOURCE_IN_USE", {
        referenced_by: ["TEACHING_REQUIREMENT", "RESERVED_BLOCK"],
      }),
    );

    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await screen.findByText(
      "This resource can't be deleted because it is used by: Teaching Assignments, Reserved Activities.",
    );
  });

  it("disables Add/Edit/Delete when configuration is locked, but records remain visible", async () => {
    mockedGetResources.mockResolvedValue(LOCKED_PROJECTION);

    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Add room or resource" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("on a lock race during create, refetches into the locked state", async () => {
    mockedGetResources.mockResolvedValueOnce(TWO_RESOURCES);
    mockedCreateResource.mockRejectedValue(new ApiError(409, "locked", "SCHEDULING_CONFIGURATION_LOCKED"));
    mockedGetResources.mockResolvedValueOnce(LOCKED_PROJECTION);

    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getByRole("button", { name: "+ Add room or resource" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Music Room" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText(/A schedule was generated since this page loaded/);
    await waitFor(() => {
      expect(screen.getByText(/Scheduling configuration is locked/)).toBeInTheDocument();
    });
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
  });

  it("on a successful write whose authoritative refetch fails, shows the stale banner with Retry and prevents further mutation", async () => {
    mockedGetResources.mockResolvedValueOnce(TWO_RESOURCES);
    mockedCreateResource.mockResolvedValue({ id: "resource_music", name: "Music Room", capacity: 1 });
    mockedGetResources.mockRejectedValueOnce(new ApiError(500, "Request failed."));
    mockedGetResources.mockResolvedValueOnce({
      configuration_locked: false,
      resources: [...TWO_RESOURCES.resources, { id: "resource_music", name: "Music Room", capacity: 1 }],
    });

    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getByRole("button", { name: "+ Add room or resource" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Music Room" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText(/could not be refreshed/);
    expect(screen.getByRole("button", { name: "+ Add room or resource" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Music Room");
  });

  it("does not double-submit while a create is in flight", async () => {
    mockedGetResources.mockResolvedValue(TWO_RESOURCES);
    const { promise, resolve } = deferred<{ id: string; name: string; capacity: number }>();
    mockedCreateResource.mockReturnValue(promise);

    render(<RoomsResourcesPanel />);
    await screen.findByText("Gym");

    fireEvent.click(screen.getByRole("button", { name: "+ Add room or resource" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Music Room" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(screen.getByRole("button", { name: "Saving…" }));

    expect(mockedCreateResource).toHaveBeenCalledTimes(1);
    resolve({ id: "resource_music", name: "Music Room", capacity: 1 });
  });
});
