import { postJson } from "./client";

export interface InitialAcademicYearProvisioningRequest {
  academic_year_id: string;
  label: string;
}

export interface ProvisionSchoolRequest {
  school_id: string;
  school_name: string;
  initial_academic_year: InitialAcademicYearProvisioningRequest;
}

export interface ProvisionSchoolResponse {
  school: { id: string; name: string };
  academic_year: { id: string; label: string };
  configuration_state: {
    published_revision_number: number | null;
    draft_revision_number: number | null;
    configuration_locked: boolean;
    timetable_out_of_date: boolean;
  };
}

export function provisionSchool(body: ProvisionSchoolRequest): Promise<ProvisionSchoolResponse> {
  return postJson<ProvisionSchoolResponse>("/schools", body);
}
