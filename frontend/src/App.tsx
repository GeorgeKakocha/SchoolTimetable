import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import TimetablePage from "./pages/TimetablePage";
import SchoolSetupPage from "./pages/SchoolSetupPage";
import TeacherAvailabilityPage from "./pages/TeacherAvailabilityPage";
import TeachingAssignmentsPage from "./pages/TeachingAssignmentsPage";
import ReservedActivitiesPage from "./pages/ReservedActivitiesPage";
import NotFoundPage from "./pages/NotFoundPage";
import ProvisionPage from "./pages/ProvisionPage";
import { ActiveSchoolYearProvider, useActiveSchoolYearContext } from "./context/ActiveSchoolYearContext";

function RequireActiveSchoolYear() {
  const { activeContext } = useActiveSchoolYearContext();
  return activeContext === null ? <Navigate to="/provision" replace /> : <Outlet />;
}

/**
 * Phase 3C.3a (`docs/DECISIONS.md`): introduces `react-router-dom` now
 * that the product has a second real page (Teaching Assignments) --
 * `App.tsx` itself is now only the router root; every page's actual
 * content lives under `pages/`, wrapped by the shared `AppShell`
 * layout route. `BrowserRouter` is used, matching the existing Vite
 * dev-proxy/relative-URL deployment model (`vite.config.ts`) with no
 * concrete reason to prefer a hash/memory router.
 */
function App() {
  return (
    <ActiveSchoolYearProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/provision" element={<ProvisionPage />} />
          <Route element={<RequireActiveSchoolYear />}>
            <Route element={<AppShell />}>
              <Route path="/" element={<Navigate to="/timetable" replace />} />
              <Route path="/timetable" element={<TimetablePage />} />
              <Route path="/configuration/setup" element={<SchoolSetupPage />} />
              <Route path="/configuration/teacher-availability" element={<TeacherAvailabilityPage />} />
              <Route path="/configuration/teaching-assignments" element={<TeachingAssignmentsPage />} />
              <Route path="/configuration/reserved-activities" element={<ReservedActivitiesPage />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </ActiveSchoolYearProvider>
  );
}

export default App;
