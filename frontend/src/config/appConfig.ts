/**
 * The one, single place the frontend reads/interprets Vite environment
 * variables for the pilot school/academic-year scope (Phase 3B first-view
 * Decision #32, Owner Decision 6). Every other module must import
 * `loadAppConfig`/`AppConfig` from here rather than touching
 * `import.meta.env` itself.
 *
 * School/AcademicYear are pilot-fixed for this first visual slice -- this
 * is a first-slice UI simplification only, not a change to the
 * multi-school domain architecture (see Decision #32).
 */

export interface AppConfig {
  schoolId: string;
  academicYearId: string;
}

export class AppConfigError extends Error {}

/** The subset of `import.meta.env` this module actually reads -- kept as
 * a plain parameter (defaulting to the real `import.meta.env`) so tests
 * can inject arbitrary values without depending on, or mutating, the
 * developer's actual environment. */
export interface AppConfigEnv {
  readonly VITE_SCHOOL_ID?: string | undefined;
  readonly VITE_ACADEMIC_YEAR_ID?: string | undefined;
}

function requireTrimmed(env: AppConfigEnv, key: keyof AppConfigEnv): string {
  const raw = env[key];
  const trimmed = raw?.trim() ?? "";
  if (trimmed === "") {
    throw new AppConfigError(
      `Missing required frontend configuration value: ${key}. ` +
        "Copy frontend/.env.example to frontend/.env.local and set it.",
    );
  }
  return trimmed;
}

export function loadAppConfig(env: AppConfigEnv = import.meta.env): AppConfig {
  return {
    schoolId: requireTrimmed(env, "VITE_SCHOOL_ID"),
    academicYearId: requireTrimmed(env, "VITE_ACADEMIC_YEAR_ID"),
  };
}
