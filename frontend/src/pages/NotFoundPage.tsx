import { Link } from "react-router-dom";

/** Phase 3C.3a's catch-all route (`*`) -- a compact, honest "not found"
 * state rather than a silent redirect, so a mistyped/stale URL is
 * diagnosable instead of quietly landing back on the timetable. */
function NotFoundPage() {
  return (
    <>
      <h1>Page not found</h1>
      <p>
        <Link to="/timetable">Go to Timetable</Link>
      </p>
    </>
  );
}

export default NotFoundPage;
