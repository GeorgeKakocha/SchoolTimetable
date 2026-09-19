import { fireEvent, render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ACTIVE_SCHOOL_YEAR_STORAGE_KEY,
  ActiveSchoolYearProvider,
  useActiveSchoolYearContext,
} from "./ActiveSchoolYearContext";

const ENV_CONTEXT = { VITE_SCHOOL_ID: "env-school", VITE_ACADEMIC_YEAR_ID: "env-year" };

function Harness() {
  const { activeContext, setActiveContext, clearActiveContext } = useActiveSchoolYearContext();
  const [error, setError] = useState("");
  return (
    <>
      <output>{activeContext === null ? "none" : `${activeContext.schoolId}/${activeContext.academicYearId}`}</output>
      <button type="button" onClick={() => setActiveContext({ schoolId: "runtime-school", academicYearId: "runtime-year" })}>
        Set runtime
      </button>
      <button type="button" onClick={clearActiveContext}>Clear</button>
      <button type="button" onClick={() => {
        try {
          setActiveContext({ schoolId: " ", academicYearId: "year" });
        } catch (caught) {
          setError(caught instanceof Error ? caught.message : "error");
        }
      }}>
        Set invalid
      </button>
      {error !== "" && <span role="alert">{error}</span>}
    </>
  );
}

function RequestProbe({ request }: { request: (schoolId: string, academicYearId: string) => void }) {
  const { activeContext, setActiveContext } = useActiveSchoolYearContext();
  useEffect(() => {
    if (activeContext !== null) {
      request(activeContext.schoolId, activeContext.academicYearId);
    }
  }, [activeContext, request]);
  return (
    <button type="button" onClick={() => setActiveContext({ schoolId: "new-school", academicYearId: "new-year" })}>
      Switch
    </button>
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

describe("ActiveSchoolYearProvider", () => {
  it("prefers persisted runtime context over the Vite fallback", () => {
    window.localStorage.setItem(ACTIVE_SCHOOL_YEAR_STORAGE_KEY, JSON.stringify({
      version: 1, schoolId: "stored-school", academicYearId: "stored-year",
    }));
    render(<ActiveSchoolYearProvider env={ENV_CONTEXT}><Harness /></ActiveSchoolYearProvider>);
    expect(screen.getByText("stored-school/stored-year")).toBeInTheDocument();
  });

  it("uses the Vite fallback when no persisted context exists", () => {
    render(<ActiveSchoolYearProvider env={ENV_CONTEXT}><Harness /></ActiveSchoolYearProvider>);
    expect(screen.getByText("env-school/env-year")).toBeInTheDocument();
  });

  it("has no active context without storage or complete Vite values", () => {
    render(<ActiveSchoolYearProvider env={{}}><Harness /></ActiveSchoolYearProvider>);
    expect(screen.getByText("none")).toBeInTheDocument();
  });

  it.each([
    ["malformed JSON", "{"],
    ["wrong version", JSON.stringify({ version: 2, schoolId: "stored-school", academicYearId: "stored-year" })],
    ["missing ID", JSON.stringify({ version: 1, schoolId: "stored-school" })],
    ["blank ID", JSON.stringify({ version: 1, schoolId: " ", academicYearId: "stored-year" })],
  ])("ignores %s safely", (_label, stored) => {
    window.localStorage.setItem(ACTIVE_SCHOOL_YEAR_STORAGE_KEY, stored);
    render(<ActiveSchoolYearProvider env={ENV_CONTEXT}><Harness /></ActiveSchoolYearProvider>);
    expect(screen.getByText("env-school/env-year")).toBeInTheDocument();
  });

  it("sets context immediately and persists the exact versioned payload", () => {
    render(<ActiveSchoolYearProvider env={ENV_CONTEXT}><Harness /></ActiveSchoolYearProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Set runtime" }));
    expect(screen.getByText("runtime-school/runtime-year")).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem(ACTIVE_SCHOOL_YEAR_STORAGE_KEY) ?? "null")).toEqual({
      version: 1, schoolId: "runtime-school", academicYearId: "runtime-year",
    });
  });

  it("restores persisted runtime context after a remount", () => {
    const first = render(<ActiveSchoolYearProvider env={ENV_CONTEXT}><Harness /></ActiveSchoolYearProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Set runtime" }));
    first.unmount();
    render(<ActiveSchoolYearProvider env={ENV_CONTEXT}><Harness /></ActiveSchoolYearProvider>);
    expect(screen.getByText("runtime-school/runtime-year")).toBeInTheDocument();
  });

  it("clear removes the override and stays empty until a remount reapplies Vite fallback", () => {
    window.localStorage.setItem(ACTIVE_SCHOOL_YEAR_STORAGE_KEY, JSON.stringify({
      version: 1, schoolId: "stored-school", academicYearId: "stored-year",
    }));
    const first = render(<ActiveSchoolYearProvider env={ENV_CONTEXT}><Harness /></ActiveSchoolYearProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByText("none")).toBeInTheDocument();
    expect(window.localStorage.getItem(ACTIVE_SCHOOL_YEAR_STORAGE_KEY)).toBeNull();
    first.unmount();
    render(<ActiveSchoolYearProvider env={ENV_CONTEXT}><Harness /></ActiveSchoolYearProvider>);
    expect(screen.getByText("env-school/env-year")).toBeInTheDocument();
  });

  it("rejects blank IDs without changing context", () => {
    render(<ActiveSchoolYearProvider env={ENV_CONTEXT}><Harness /></ActiveSchoolYearProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Set invalid" }));
    expect(screen.getByRole("alert")).toHaveTextContent("must be nonblank");
    expect(screen.getByText("env-school/env-year")).toBeInTheDocument();
  });

  it("makes switched IDs immediately authoritative for subsequent requests", () => {
    const request = vi.fn();
    render(
      <ActiveSchoolYearProvider env={ENV_CONTEXT}>
        <RequestProbe request={request} />
      </ActiveSchoolYearProvider>,
    );
    expect(request).toHaveBeenLastCalledWith("env-school", "env-year");
    fireEvent.click(screen.getByRole("button", { name: "Switch" }));
    expect(request).toHaveBeenLastCalledWith("new-school", "new-year");
  });
});
