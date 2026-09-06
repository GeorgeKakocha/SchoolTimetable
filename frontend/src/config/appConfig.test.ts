import { describe, expect, it } from "vitest";
import { AppConfigError, loadAppConfig } from "./appConfig";

describe("loadAppConfig", () => {
  it("returns valid school/year values", () => {
    const config = loadAppConfig({ VITE_SCHOOL_ID: "school-1", VITE_ACADEMIC_YEAR_ID: "year-1" });
    expect(config).toEqual({ schoolId: "school-1", academicYearId: "year-1" });
  });

  it("trims surrounding whitespace", () => {
    const config = loadAppConfig({
      VITE_SCHOOL_ID: "  school-1  ",
      VITE_ACADEMIC_YEAR_ID: "\tyear-1\n",
    });
    expect(config).toEqual({ schoolId: "school-1", academicYearId: "year-1" });
  });

  it("throws when the school ID is missing", () => {
    expect(() => loadAppConfig({ VITE_ACADEMIC_YEAR_ID: "year-1" })).toThrow(AppConfigError);
  });

  it("throws when the academic year ID is missing", () => {
    expect(() => loadAppConfig({ VITE_SCHOOL_ID: "school-1" })).toThrow(AppConfigError);
  });

  it("throws when both values are entirely absent", () => {
    expect(() => loadAppConfig({})).toThrow(AppConfigError);
  });

  it("throws when a value is whitespace-only", () => {
    expect(() =>
      loadAppConfig({ VITE_SCHOOL_ID: "   ", VITE_ACADEMIC_YEAR_ID: "year-1" }),
    ).toThrow(AppConfigError);
  });
});
