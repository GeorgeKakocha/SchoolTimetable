import { describe, expect, it } from "vitest";
import type { SynchronizedSplitConfigResponse } from "../api/types";
import { deriveSynchronizedSplits } from "./synchronizedSplitReadback";

const CONFIG: SynchronizedSplitConfigResponse = {
  class_sections: [{ id: "c1", name: "Class One" }],
  teachers: [{ id: "t1", name: "Teacher One" }, { id: "t2", name: "Teacher Two" }],
  activities: [
    { id: "a1", name: "Subject One", kind: "ORDINARY" },
    { id: "a2", name: "Subject Two", kind: "ORDINARY" },
  ],
  participant_groups: [
    { id: "g1", name: "Alpha", role: "SUBGROUP", class_sections: ["c1"] },
    { id: "g2", name: "Beta", role: "SUBGROUP", class_sections: ["c1"] },
  ],
  teaching_requirements: [
    { id: "r1", teacher_id: "t1", activity_id: "a1", participant_group_id: "g1",
      weekly_periods: 2, split_group_id: "split-1" },
    { id: "r2", teacher_id: "t2", activity_id: "a2", participant_group_id: "g2",
      weekly_periods: 2, split_group_id: "split-1" },
  ],
};

describe("deriveSynchronizedSplits", () => {
  it("groups by split identity and joins authoritative relationships without parsing names", () => {
    const result = deriveSynchronizedSplits(CONFIG);
    expect(result.malformedSplitGroupIds).toEqual([]);
    expect(result.splits).toEqual([{
      splitGroupId: "split-1", classSectionId: "c1", classSectionName: "Class One", weeklyPeriods: 2,
      branches: [
        { participantGroupId: "g1", participantGroupName: "Alpha", requirementId: "r1",
          teacherId: "t1", teacherName: "Teacher One", activityId: "a1", activityName: "Subject One" },
        { participantGroupId: "g2", participantGroupName: "Beta", requirementId: "r2",
          teacherId: "t2", teacherName: "Teacher Two", activityId: "a2", activityName: "Subject Two" },
      ],
    }]);
  });

  const malformedCases: [string, Partial<SynchronizedSplitConfigResponse>][] = [
    ["one branch", { teaching_requirements: CONFIG.teaching_requirements.slice(0, 1) }],
    ["different weekly counts", {
      teaching_requirements: [CONFIG.teaching_requirements[0]!, {
        ...CONFIG.teaching_requirements[1]!, weekly_periods: 3,
      }],
    }],
    ["non-subgroup", {
      participant_groups: [{ ...CONFIG.participant_groups[0]!, role: "WHOLE_CLASS" }, CONFIG.participant_groups[1]!],
    }],
    ["missing relationship", {
      teaching_requirements: [{ ...CONFIG.teaching_requirements[0]!, participant_group_id: "missing" },
        CONFIG.teaching_requirements[1]!],
    }],
  ];

  it.each(malformedCases)("rejects malformed aggregate: %s", (_label, change) => {
    const result = deriveSynchronizedSplits({ ...CONFIG, ...change });
    expect(result.splits).toEqual([]);
    expect(result.malformedSplitGroupIds).toEqual(["split-1"]);
  });
});
