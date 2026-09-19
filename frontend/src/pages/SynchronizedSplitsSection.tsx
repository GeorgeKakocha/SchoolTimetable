import { useMemo, useRef, useState } from "react";
import type {
  SynchronizedSplitConfigActivity,
  SynchronizedSplitConfigResponse,
  SynchronizedSplitCreateRequest,
  TeacherSummary,
} from "../api/types";
import { deriveSynchronizedSplits } from "./synchronizedSplitReadback";

interface Props {
  config: SynchronizedSplitConfigResponse;
  configurationLocked: boolean;
  controlsDisabled: boolean;
  error: string | null;
  onSubmit: (request: SynchronizedSplitCreateRequest) => Promise<boolean>;
}

interface FormValues {
  classSectionId: string;
  weeklyPeriods: string;
  subgroupA: string;
  teacherA: string;
  activityA: string;
  subgroupB: string;
  teacherB: string;
  activityB: string;
}

const INITIAL_VALUES: FormValues = {
  classSectionId: "", weeklyPeriods: "1",
  subgroupA: "", teacherA: "", activityA: "",
  subgroupB: "", teacherB: "", activityB: "",
};

function validate(values: FormValues): Record<string, string> {
  const errors: Record<string, string> = {};
  const weekly = Number(values.weeklyPeriods);
  if (values.classSectionId === "") errors.classSectionId = "Select a class.";
  if (!Number.isInteger(weekly) || weekly <= 0) errors.weeklyPeriods = "Enter a positive whole number.";
  if (values.subgroupA.trim() === "") errors.subgroupA = "Enter a subgroup name.";
  if (values.subgroupB.trim() === "") errors.subgroupB = "Enter a subgroup name.";
  if (values.subgroupA.trim() !== "" && values.subgroupA.trim() === values.subgroupB.trim()) {
    errors.subgroupB = "Subgroup names must be different.";
  }
  if (values.teacherA === "") errors.teacherA = "Select a teacher.";
  if (values.teacherB === "") errors.teacherB = "Select a teacher.";
  if (values.teacherA !== "" && values.teacherA === values.teacherB) {
    errors.teacherB = "The simultaneous branches need different teachers.";
  }
  if (values.activityA === "") errors.activityA = "Select a subject.";
  if (values.activityB === "") errors.activityB = "Select a subject.";
  return errors;
}

function SynchronizedSplitsSection({ config, configurationLocked, controlsDisabled, error, onSubmit }: Props) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<FormValues>(INITIAL_VALUES);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const readback = useMemo(() => deriveSynchronizedSplits(config), [config]);
  const ordinaryActivities = config.activities.filter((activity) => activity.kind === "ORDINARY");

  const setValue = (field: keyof FormValues, value: string) => {
    setValues((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: "" }));
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (submittingRef.current || configurationLocked || controlsDisabled) return;
    const nextErrors = validate(values);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    submittingRef.current = true;
    setSubmitting(true);
    const succeeded = await onSubmit({
      class_section_id: values.classSectionId,
      weekly_periods: Number(values.weeklyPeriods),
      branches: [
        {
          participant_group_name: values.subgroupA.trim(),
          teacher_id: values.teacherA,
          activity_id: values.activityA,
        },
        {
          participant_group_name: values.subgroupB.trim(),
          teacher_id: values.teacherB,
          activity_id: values.activityB,
        },
      ],
    });
    submittingRef.current = false;
    setSubmitting(false);
    if (succeeded) {
      setValues(INITIAL_VALUES);
      setErrors({});
      setOpen(false);
    }
  };

  return (
    <section className="page-section synchronized-splits" aria-labelledby="synchronized-splits-heading">
      <div className="assignments-toolbar">
        <div>
          <h2 id="synchronized-splits-heading">Synchronized splits</h2>
          <p className="section-caption">Two subgroup lessons that always run at the same time.</p>
        </div>
        <button
          type="button"
          className="btn-primary"
          onClick={() => setOpen((current) => !current)}
          disabled={configurationLocked || controlsDisabled || submitting}
          aria-expanded={open}
        >
          Add synchronized split
        </button>
      </div>

      {open && (
        <form className="synchronized-split-form" onSubmit={submit} noValidate>
          <p className="form-guidance">Both branches will be scheduled simultaneously.</p>
          <fieldset className="split-form-controls" disabled={configurationLocked || controlsDisabled || submitting}>
          <div className="split-shared-fields">
            <Field label="Class" error={errors.classSectionId}>
              <select value={values.classSectionId} onChange={(e) => setValue("classSectionId", e.target.value)}>
                <option value="">Select class</option>
                {config.class_sections.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </Field>
            <Field label="Weekly periods" error={errors.weeklyPeriods}>
              <input type="number" min="1" step="1" value={values.weeklyPeriods}
                onChange={(e) => setValue("weeklyPeriods", e.target.value)} />
            </Field>
          </div>
          <div className="split-branches">
            <BranchFields label="Branch A" suffix="A" values={values} errors={errors}
              teachers={config.teachers} activities={ordinaryActivities} setValue={setValue} />
            <BranchFields label="Branch B" suffix="B" values={values} errors={errors}
              teachers={config.teachers} activities={ordinaryActivities} setValue={setValue} />
          </div>
          </fieldset>
          {error !== null && <p role="alert" className="drawer-error">{error}</p>}
          <div className="drawer-actions">
            <button type="submit" disabled={configurationLocked || controlsDisabled || submitting}>
              {submitting ? "Saving…" : "Save synchronized split"}
            </button>
            <button type="button" disabled={submitting} onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </form>
      )}

      {readback.malformedSplitGroupIds.length > 0 && (
        <p role="alert" className="page-error">
          Some synchronized split data could not be displayed safely. Refresh or contact an administrator.
        </p>
      )}
      {readback.splits.length === 0 ? (
        <p>No synchronized splits configured yet.</p>
      ) : (
        <div className="synchronized-split-list">
          {readback.splits.map((split) => (
            <article key={split.splitGroupId} className="synchronized-split-item">
              <h3>{split.classSectionName} · {split.weeklyPeriods} per week</h3>
              <p className="simultaneous-label">Branches run simultaneously</p>
              <ul>
                {split.branches.map((branch) => (
                  <li key={branch.requirementId}>
                    <strong>{branch.participantGroupName}</strong> — {branch.activityName} — {branch.teacherName}
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function Field({ label, error, children }: {
  label: string;
  error: string | undefined;
  children: React.ReactElement;
}) {
  return <label>{label}{children}{error && <span className="field-error">{error}</span>}</label>;
}

interface BranchFieldsProps {
  label: string;
  suffix: "A" | "B";
  values: FormValues;
  errors: Record<string, string>;
  teachers: TeacherSummary[];
  activities: SynchronizedSplitConfigActivity[];
  setValue: (field: keyof FormValues, value: string) => void;
}

function BranchFields({ label, suffix, values, errors, teachers, activities, setValue }: BranchFieldsProps) {
  const subgroup = `subgroup${suffix}` as keyof FormValues;
  const teacher = `teacher${suffix}` as keyof FormValues;
  const activity = `activity${suffix}` as keyof FormValues;
  return (
    <fieldset><legend>{label}</legend>
      <Field label="Subgroup name" error={errors[subgroup]}>
        <input value={values[subgroup]} onChange={(e) => setValue(subgroup, e.target.value)} />
      </Field>
      <Field label="Teacher" error={errors[teacher]}>
        <select value={values[teacher]} onChange={(e) => setValue(teacher, e.target.value)}>
          <option value="">Select teacher</option>
          {teachers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </Field>
      <Field label="Subject" error={errors[activity]}>
        <select value={values[activity]} onChange={(e) => setValue(activity, e.target.value)}>
          <option value="">Select subject</option>
          {activities.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </Field>
    </fieldset>
  );
}

export default SynchronizedSplitsSection;
