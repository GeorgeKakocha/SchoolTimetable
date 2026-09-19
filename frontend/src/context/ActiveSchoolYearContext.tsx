import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { AppConfigError, loadAppConfig, type AppConfigEnv } from "../config/appConfig";

export interface ActiveSchoolYear {
  schoolId: string;
  academicYearId: string;
}

interface ActiveSchoolYearContextValue {
  activeContext: ActiveSchoolYear | null;
  setActiveContext: (context: ActiveSchoolYear) => void;
  clearActiveContext: () => void;
}

interface StoredActiveSchoolYear {
  version: 1;
  schoolId: string;
  academicYearId: string;
}

export const ACTIVE_SCHOOL_YEAR_STORAGE_KEY = "school-timetable.active-school-year.v1";

const ActiveSchoolYearReactContext = createContext<ActiveSchoolYearContextValue | null>(null);

function isNonblankString(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

function readPersistedContext(storage: Storage | null): ActiveSchoolYear | null {
  if (storage === null) {
    return null;
  }
  try {
    const raw = storage.getItem(ACTIVE_SCHOOL_YEAR_STORAGE_KEY);
    if (raw === null) {
      return null;
    }
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      (parsed as Partial<StoredActiveSchoolYear>).version !== 1 ||
      !isNonblankString((parsed as Partial<StoredActiveSchoolYear>).schoolId) ||
      !isNonblankString((parsed as Partial<StoredActiveSchoolYear>).academicYearId)
    ) {
      return null;
    }
    return {
      schoolId: (parsed as StoredActiveSchoolYear).schoolId,
      academicYearId: (parsed as StoredActiveSchoolYear).academicYearId,
    };
  } catch {
    return null;
  }
}

function browserStorage(): Storage | null {
  return typeof window === "undefined" ? null : window.localStorage;
}

function initialContext(storage: Storage | null, env: AppConfigEnv): ActiveSchoolYear | null {
  const persisted = readPersistedContext(storage);
  if (persisted !== null) {
    return persisted;
  }
  try {
    return loadAppConfig(env);
  } catch (error) {
    if (error instanceof AppConfigError) {
      return null;
    }
    throw error;
  }
}

interface ActiveSchoolYearProviderProps {
  children: ReactNode;
  env?: AppConfigEnv;
  storage?: Storage | null;
}

export function ActiveSchoolYearProvider({
  children,
  env = import.meta.env,
  storage = browserStorage(),
}: ActiveSchoolYearProviderProps) {
  const [activeContext, setContextState] = useState<ActiveSchoolYear | null>(() => initialContext(storage, env));

  const setActiveContext = useCallback((context: ActiveSchoolYear) => {
    if (!isNonblankString(context.schoolId) || !isNonblankString(context.academicYearId)) {
      throw new Error("Active school and academic-year IDs must be nonblank.");
    }
    const payload: StoredActiveSchoolYear = {
      version: 1,
      schoolId: context.schoolId,
      academicYearId: context.academicYearId,
    };
    storage?.setItem(ACTIVE_SCHOOL_YEAR_STORAGE_KEY, JSON.stringify(payload));
    setContextState(context);
  }, [storage]);

  const clearActiveContext = useCallback(() => {
    storage?.removeItem(ACTIVE_SCHOOL_YEAR_STORAGE_KEY);
    // Clearing is immediately observable for this mounted provider. A
    // later remount may select the Vite fallback again by design.
    setContextState(null);
  }, [storage]);

  const value = useMemo(
    () => ({ activeContext, setActiveContext, clearActiveContext }),
    [activeContext, setActiveContext, clearActiveContext],
  );
  return <ActiveSchoolYearReactContext.Provider value={value}>{children}</ActiveSchoolYearReactContext.Provider>;
}

export function useActiveSchoolYearContext(): ActiveSchoolYearContextValue {
  const value = useContext(ActiveSchoolYearReactContext);
  const [isolatedMountFallback] = useState<ActiveSchoolYearContextValue>(() => {
    let activeContext: ActiveSchoolYear | null = null;
    try {
      activeContext = loadAppConfig();
    } catch (error) {
      if (!(error instanceof AppConfigError)) {
        throw error;
      }
    }
    return {
      activeContext,
      setActiveContext: () => {
        throw new Error("ActiveSchoolYearProvider is required to change active context.");
      },
      clearActiveContext: () => {
        throw new Error("ActiveSchoolYearProvider is required to clear active context.");
      },
    };
  });
  // Production mounts exactly one provider above the router. This stable
  // fallback preserves the established isolated-component test convention
  // without giving those mounts storage or runtime-mutation behavior.
  return value ?? isolatedMountFallback;
}
