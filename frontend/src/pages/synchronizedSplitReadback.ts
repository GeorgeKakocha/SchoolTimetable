import type { SynchronizedSplitConfigResponse } from "../api/types";

export interface SynchronizedSplitDisplayBranch {
  participantGroupId: string;
  participantGroupName: string;
  requirementId: string;
  teacherId: string;
  teacherName: string;
  activityId: string;
  activityName: string;
}

export interface SynchronizedSplitDisplay {
  splitGroupId: string;
  classSectionId: string;
  classSectionName: string;
  weeklyPeriods: number;
  branches: [SynchronizedSplitDisplayBranch, SynchronizedSplitDisplayBranch];
}

export interface SynchronizedSplitReadback {
  splits: SynchronizedSplitDisplay[];
  malformedSplitGroupIds: string[];
}

export function deriveSynchronizedSplits(config: SynchronizedSplitConfigResponse): SynchronizedSplitReadback {
  const groups = new Map(config.participant_groups.map((group) => [group.id, group]));
  const classes = new Map(config.class_sections.map((item) => [item.id, item.name]));
  const teachers = new Map(config.teachers.map((item) => [item.id, item.name]));
  const activities = new Map(config.activities.map((item) => [item.id, item.name]));
  const requirementsBySplit = new Map<string, typeof config.teaching_requirements>();

  for (const requirement of config.teaching_requirements) {
    if (requirement.split_group_id === null) continue;
    const existing = requirementsBySplit.get(requirement.split_group_id) ?? [];
    existing.push(requirement);
    requirementsBySplit.set(requirement.split_group_id, existing);
  }

  const splits: SynchronizedSplitDisplay[] = [];
  const malformedSplitGroupIds: string[] = [];
  for (const [splitGroupId, requirements] of requirementsBySplit) {
    if (requirements.length !== 2) {
      malformedSplitGroupIds.push(splitGroupId);
      continue;
    }
    const [requirementA, requirementB] = requirements;
    if (requirementA === undefined || requirementB === undefined) {
      malformedSplitGroupIds.push(splitGroupId);
      continue;
    }
    const resolved = [requirementA, requirementB].map((requirement) => {
      const group = groups.get(requirement.participant_group_id);
      if (group === undefined || group.role !== "SUBGROUP" || group.class_sections.length !== 1) return null;
      const classSectionId = group.class_sections[0];
      if (classSectionId === undefined) return null;
      const classSectionName = classes.get(classSectionId);
      const teacherName = teachers.get(requirement.teacher_id);
      const activityName = activities.get(requirement.activity_id);
      if (classSectionName === undefined || teacherName === undefined || activityName === undefined) return null;
      return {
        classSectionId,
        branch: {
          participantGroupId: group.id,
          participantGroupName: group.name,
          requirementId: requirement.id,
          teacherId: requirement.teacher_id,
          teacherName,
          activityId: requirement.activity_id,
          activityName,
        },
      };
    });
    const [resolvedA, resolvedB] = resolved;
    if (resolvedA === undefined || resolvedB === undefined || resolvedA === null || resolvedB === null
      || resolvedA.classSectionId !== resolvedB.classSectionId
      || requirementA.weekly_periods !== requirementB.weekly_periods) {
      malformedSplitGroupIds.push(splitGroupId);
      continue;
    }
    splits.push({
      splitGroupId,
      classSectionId: resolvedA.classSectionId,
      classSectionName: classes.get(resolvedA.classSectionId) as string,
      weeklyPeriods: requirementA.weekly_periods,
      branches: [resolvedA.branch, resolvedB.branch],
    });
  }
  return { splits, malformedSplitGroupIds };
}
