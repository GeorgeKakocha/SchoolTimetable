import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { provisionSchool } from "../api/schoolProvisioning";
import { useActiveSchoolYearContext } from "../context/ActiveSchoolYearContext";

const PUBLIC_ID_PATTERN = /^[a-z0-9][a-z0-9_-]*$/;

function validationMessages(error: ApiError): string[] {
  const errors = error.body?.["errors"];
  if (Array.isArray(errors)) {
    const messages = errors.flatMap((item) =>
      typeof item === "object" && item !== null && typeof (item as { message?: unknown }).message === "string"
        ? [(item as { message: string }).message]
        : [],
    );
    if (messages.length > 0) {
      return messages;
    }
  }
  const detail = error.body?.["detail"];
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) =>
      typeof item === "object" && item !== null && typeof (item as { msg?: unknown }).msg === "string"
        ? [(item as { msg: string }).msg]
        : [],
    );
    if (messages.length > 0) {
      return messages;
    }
  }
  return [error.detail];
}

function ProvisionPage() {
  const navigate = useNavigate();
  const { activeContext, setActiveContext, clearActiveContext } = useActiveSchoolYearContext();
  const [schoolName, setSchoolName] = useState("");
  const [schoolId, setSchoolId] = useState("");
  const [academicYearLabel, setAcademicYearLabel] = useState("");
  const [academicYearId, setAcademicYearId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessages, setErrorMessages] = useState<string[]>([]);

  const valid =
    schoolName.trim() !== "" &&
    academicYearLabel.trim() !== "" &&
    PUBLIC_ID_PATTERN.test(schoolId) &&
    PUBLIC_ID_PATTERN.test(academicYearId);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!valid || submitting) {
      return;
    }
    setSubmitting(true);
    setErrorMessages([]);
    try {
      const response = await provisionSchool({
        school_id: schoolId,
        school_name: schoolName.trim(),
        initial_academic_year: {
          academic_year_id: academicYearId,
          label: academicYearLabel.trim(),
        },
      });
      setActiveContext({
        schoolId: response.school.id,
        academicYearId: response.academic_year.id,
      });
      navigate("/configuration/setup", { replace: true });
    } catch (error) {
      setErrorMessages(
        error instanceof ApiError
          ? validationMessages(error)
          : ["The school could not be created. Check your connection and try again."],
      );
      setSubmitting(false);
    }
  };

  return (
    <main className="provision-page">
      <h1>Set up a school</h1>
      <p className="page-subtitle">
        Create one school, its first academic year, and an editable initial configuration draft.
      </p>

      {activeContext !== null && (
        <section className="provision-current" aria-label="Current school selection">
          <p>A school is currently selected.</p>
          <button type="button" onClick={clearActiveContext}>Clear current selection</button>
        </section>
      )}

      <form className="provision-form" onSubmit={handleSubmit}>
        <label>
          School name
          <input value={schoolName} onChange={(event) => setSchoolName(event.target.value)} required />
        </label>
        <label>
          School ID
          <input
            value={schoolId}
            onChange={(event) => setSchoolId(event.target.value)}
            required
            pattern="[a-z0-9][a-z0-9_-]*"
            aria-describedby="school-id-hint"
          />
        </label>
        <p id="school-id-hint" className="field-hint provision-field-hint">
          Permanent URL-safe ID: lowercase letters, numbers, hyphens, or underscores.
        </p>
        <label>
          Academic year label
          <input
            value={academicYearLabel}
            onChange={(event) => setAcademicYearLabel(event.target.value)}
            placeholder="2026/2027"
            required
          />
        </label>
        <label>
          Academic year ID
          <input
            value={academicYearId}
            onChange={(event) => setAcademicYearId(event.target.value)}
            required
            pattern="[a-z0-9][a-z0-9_-]*"
            aria-describedby="academic-year-id-hint"
          />
        </label>
        <p id="academic-year-id-hint" className="field-hint provision-field-hint">
          Permanent URL-safe ID, for example ay-2026-2027.
        </p>

        {errorMessages.length > 0 && (
          <div className="provision-error" role="alert">
            {errorMessages.map((message) => <p key={message}>{message}</p>)}
          </div>
        )}

        <button type="submit" className="btn-primary" disabled={!valid || submitting}>
          {submitting ? "Creating school…" : "Create school"}
        </button>
      </form>
    </main>
  );
}

export default ProvisionPage;
