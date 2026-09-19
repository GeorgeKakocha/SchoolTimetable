import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { provisionSchool, type ProvisionSchoolResponse } from "../api/schoolProvisioning";
import {
  ACTIVE_SCHOOL_YEAR_STORAGE_KEY,
  ActiveSchoolYearProvider,
  useActiveSchoolYearContext,
} from "../context/ActiveSchoolYearContext";
import ProvisionPage from "./ProvisionPage";

vi.mock("../api/schoolProvisioning", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/schoolProvisioning")>();
  return { ...actual, provisionSchool: vi.fn() };
});

const mockedProvisionSchool = vi.mocked(provisionSchool);
const STALE_ENV = { VITE_SCHOOL_ID: "editing-review-school", VITE_ACADEMIC_YEAR_ID: "old-year" };
const RESPONSE: ProvisionSchoolResponse = {
  school: { id: "server-school", name: "Real School" },
  academic_year: { id: "server-year", label: "2026/2027" },
  configuration_state: {
    published_revision_number: null,
    draft_revision_number: 1,
    configuration_locked: false,
    timetable_out_of_date: false,
  },
};

function ConfigurationProbe({ request }: { request?: (schoolId: string, yearId: string) => void }) {
  const { activeContext } = useActiveSchoolYearContext();
  useEffect(() => {
    if (activeContext !== null) {
      request?.(activeContext.schoolId, activeContext.academicYearId);
    }
  }, [activeContext, request]);
  return <h1>Configuration for {activeContext?.schoolId}</h1>;
}

function renderFlow(options: {
  env?: typeof STALE_ENV | Record<string, never>;
  initialPath?: string;
  request?: (schoolId: string, yearId: string) => void;
} = {}) {
  return render(
    <ActiveSchoolYearProvider env={options.env ?? {}}>
      <MemoryRouter initialEntries={[options.initialPath ?? "/provision"]}>
        <Routes>
          <Route path="/provision" element={<ProvisionPage />} />
          <Route
            path="/configuration/setup"
            element={<ConfigurationProbe {...(options.request === undefined ? {} : { request: options.request })} />}
          />
        </Routes>
      </MemoryRouter>
    </ActiveSchoolYearProvider>,
  );
}

function fillValidForm() {
  fireEvent.change(screen.getByLabelText("School name"), { target: { value: "  Real School  " } });
  fireEvent.change(screen.getByLabelText("School ID"), { target: { value: "form-school" } });
  fireEvent.change(screen.getByLabelText("Academic year label"), { target: { value: "  2026/2027  " } });
  fireEvent.change(screen.getByLabelText("Academic year ID"), { target: { value: "form-year" } });
}

beforeEach(() => {
  window.localStorage.clear();
  mockedProvisionSchool.mockReset();
  mockedProvisionSchool.mockResolvedValue(RESPONSE);
});

describe("ProvisionPage", () => {
  it("renders without an active context", () => {
    renderFlow();
    expect(screen.getByRole("heading", { name: "Set up a school" })).toBeInTheDocument();
    expect(screen.queryByText("A school is currently selected.")).not.toBeInTheDocument();
  });

  it("remains reachable with a Vite fallback context and offers recovery", () => {
    renderFlow({ env: STALE_ENV });
    expect(screen.getByRole("heading", { name: "Set up a school" })).toBeInTheDocument();
    expect(screen.getByText("A school is currently selected.")).toBeInTheDocument();
  });

  it("does not submit blank or invalid fields", () => {
    renderFlow();
    const submit = screen.getByRole("button", { name: "Create school" });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(mockedProvisionSchool).not.toHaveBeenCalled();
  });

  it("submits the exact accepted POST body with trimmed display values", async () => {
    renderFlow();
    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: "Create school" }));
    await waitFor(() => expect(mockedProvisionSchool).toHaveBeenCalledWith({
      school_id: "form-school",
      school_name: "Real School",
      initial_academic_year: { academic_year_id: "form-year", label: "2026/2027" },
    }));
  });

  it("prevents duplicate submission while the request is pending", () => {
    let resolveRequest!: (value: ProvisionSchoolResponse) => void;
    mockedProvisionSchool.mockReturnValue(new Promise((resolve) => { resolveRequest = resolve; }));
    renderFlow();
    fillValidForm();
    const submit = screen.getByRole("button", { name: "Create school" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    expect(mockedProvisionSchool).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Creating school…" })).toBeDisabled();
    resolveRequest(RESPONSE);
  });

  it("shows backend validation, preserves values, and stays without context", async () => {
    mockedProvisionSchool.mockRejectedValue(new ApiError(
      422,
      "Request failed.",
      undefined,
      { detail: [{ msg: "School ID is invalid" }] },
    ));
    renderFlow();
    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: "Create school" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("School ID is invalid");
    expect(screen.getByLabelText("School name")).toHaveValue("  Real School  ");
    expect(screen.getByRole("heading", { name: "Set up a school" })).toBeInTheDocument();
    expect(window.localStorage.getItem(ACTIVE_SCHOOL_YEAR_STORAGE_KEY)).toBeNull();
  });

  it("preserves a structured conflict message and does not navigate", async () => {
    mockedProvisionSchool.mockRejectedValue(new ApiError(
      409,
      "school public ID already exists with incompatible data",
      "SCHOOL_ID_ALREADY_EXISTS",
    ));
    renderFlow();
    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: "Create school" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists with incompatible data");
    expect(screen.getByLabelText("School ID")).toHaveValue("form-school");
    expect(screen.getByRole("heading", { name: "Set up a school" })).toBeInTheDocument();
    expect(window.localStorage.getItem(ACTIVE_SCHOOL_YEAR_STORAGE_KEY)).toBeNull();
  });

  it("uses server-returned IDs, overrides stale Vite IDs, and navigates without reload", async () => {
    const configurationRequest = vi.fn();
    renderFlow({ env: STALE_ENV, request: configurationRequest });
    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: "Create school" }));

    await screen.findByRole("heading", { name: "Configuration for server-school" });
    await waitFor(() => expect(configurationRequest).toHaveBeenCalledWith("server-school", "server-year"));
    expect(configurationRequest).not.toHaveBeenCalledWith("editing-review-school", "old-year");
    expect(JSON.parse(window.localStorage.getItem(ACTIVE_SCHOOL_YEAR_STORAGE_KEY) ?? "null")).toEqual({
      version: 1, schoolId: "server-school", academicYearId: "server-year",
    });
  });

  it("restores newly provisioned IDs after a remount instead of the Vite fallback", async () => {
    const first = renderFlow({ env: STALE_ENV });
    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: "Create school" }));
    await screen.findByRole("heading", { name: "Configuration for server-school" });
    first.unmount();

    const request = vi.fn();
    renderFlow({ env: STALE_ENV, initialPath: "/configuration/setup", request });
    expect(await screen.findByRole("heading", { name: "Configuration for server-school" })).toBeInTheDocument();
    await waitFor(() => expect(request).toHaveBeenCalledWith("server-school", "server-year"));
  });

  it("clears a stale runtime selection manually and stays on the usable form", () => {
    window.localStorage.setItem(ACTIVE_SCHOOL_YEAR_STORAGE_KEY, JSON.stringify({
      version: 1, schoolId: "stale-school", academicYearId: "stale-year",
    }));
    renderFlow({ env: STALE_ENV });
    expect(screen.getByText("A school is currently selected.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear current selection" }));
    expect(screen.queryByText("A school is currently selected.")).not.toBeInTheDocument();
    expect(window.localStorage.getItem(ACTIVE_SCHOOL_YEAR_STORAGE_KEY)).toBeNull();
    expect(screen.getByRole("button", { name: "Create school" })).toBeInTheDocument();
  });

  it("shows a safe network failure and keeps the form usable", async () => {
    mockedProvisionSchool.mockRejectedValue(new Error("network down"));
    renderFlow();
    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: "Create school" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Check your connection and try again");
    expect(screen.getByRole("button", { name: "Create school" })).toBeEnabled();
  });
});
