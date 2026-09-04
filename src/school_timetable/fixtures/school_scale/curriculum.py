"""Deterministic realistic-school curriculum data (Phase 2B).

All identifiers are synthetic (``class_01``, ``teacher_07``,
``subject_math``, ...) -- no real school or person data. Building the
curriculum is split from placing it (see ``constructor.py``): this module
only decides *what* the school offers (classes, teachers, subjects,
requirements, splits, merges, clubs, resources, availability) -- never
*when* anything happens. A fixed ``random.Random`` seed is used for the
handful of cosmetic choices (which slots are preferred/unavailable) so
every run of the generator is reproducible.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from school_timetable.domain.activities import Activity, ActivityKind
from school_timetable.domain.blocks import ReservedBlock
from school_timetable.domain.calendar import TimeSlot
from school_timetable.domain.groups import ClassSection, ParticipantGroup
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.domain.resources import Resource
from school_timetable.domain.requirements import (
    BlockPolicyMode,
    DistributionPolicy,
    LessonBlockPolicy,
    PreferenceWeight,
    TimePreference,
)

SEED = 20260904

NUM_CLASSES = 15
CLASS_IDS = [f"class_{i:02d}" for i in range(1, NUM_CLASSES + 1)]

# Baseline weekly periods for the 14 "always present" ordinary subjects.
# Chosen to sum to 36 -- + 3 (language) + 1 (club) = 40 per class.
BASE_SUBJECTS: list[tuple[str, int]] = [
    ("math", 5),
    ("english", 4),
    ("science", 4),
    ("history", 3),
    ("geography", 2),
    ("art", 3),
    ("music", 2),
    ("ict", 3),
    ("sport", 1),
    ("dance", 1),
    ("literature", 3),
    ("civics", 2),
    ("biology", 1),
    ("chemistry", 2),
]
assert sum(w for _, w in BASE_SUBJECTS) == 36
# Sport+dance are capacity-1-gym-constrained: with 15 classes x (1+1) = 30
# total weekly gym-periods needed against 40 total instructional slots in
# the week, resource allocation is genuinely meaningful (75% utilization)
# without being so overcommitted that no valid schedule can exist at all
# (unlike 15 x (2+2) = 60, which would exceed the 40-slot ceiling outright).

SUBJECT_TEACHER_COUNTS: dict[str, int] = {
    "math": 4, "english": 3, "science": 3, "history": 2, "geography": 2,
    "art": 2, "music": 2, "ict": 2, "sport": 2, "dance": 2, "literature": 2,
    "civics": 2, "biology": 1, "chemistry": 1, "german": 2, "russian": 2,
}

# Classes 1-5 get a synchronized German/Russian split instead of a single
# plain language course. Classes 6-10 take plain German; 11-15 plain Russian.
SPLIT_LANGUAGE_CLASSES = CLASS_IDS[0:5]
PLAIN_GERMAN_CLASSES = CLASS_IDS[5:10]
PLAIN_RUSSIAN_CLASSES = CLASS_IDS[10:15]
LANGUAGE_WEEKLY_PERIODS = 3

# Civics is delivered as a merged (2-class) lesson for these pairs instead
# of individually -- "multiple merged-group lessons involving two classes".
MERGED_CIVICS_PAIRS: list[tuple[str, str]] = [
    ("class_06", "class_07"),
    ("class_08", "class_09"),
    ("class_10", "class_11"),
]
MERGED_CIVICS_WEEKLY_PERIODS = 2
_MERGED_CIVICS_CLASS_IDS = {c for pair in MERGED_CIVICS_PAIRS for c in pair}

# Reserved club cohorts -- different classes, different day/period.
CLUB_COHORTS: list[tuple[str, str, str, str]] = [
    # (reserved_block_id, activity_id, day_id, period_id)
    ("club_chess", "club_chess", "wed", "p8"),
    ("club_robotics", "club_robotics", "thu", "p8"),
    ("club_art", "club_art", "fri", "p8"),
]
CLUB_COHORT_CLASSES: list[list[str]] = [
    CLASS_IDS[0:5],
    CLASS_IDS[5:10],
    CLASS_IDS[10:15],
]

GYM_RESOURCE_ID = "gym"

# Generic REQUIRED/PREFERRED block-pattern demonstrations at meaningful
# scale, layered on top of the base subjects above (same weekly_periods,
# different shape).
REQUIRED_2_2_CLASSES = ["class_01", "class_02", "class_03"]        # english, weekly=4
REQUIRED_3_1_CLASSES = ["class_04", "class_05", "class_06"]        # science, weekly=4
REQUIRED_2_1_1_1_CLASSES = ["class_07", "class_08", "class_09"]    # math, weekly=5
PREFERRED_2_1_1_1_CLASSES = ["class_10", "class_11"]               # math, weekly=5

PREFER_NOT_TEACHER_SLOTS = [
    ("teacher_01", "tue", "p2"),
    ("teacher_05", "wed", "p3"),
    ("teacher_11", "mon", "p1"),
    ("teacher_21", "fri", "p6"),
]
PART_TIME_TEACHERS: dict[str, list[str]] = {
    # teacher_id -> fully unavailable day_ids
    "teacher_18": ["mon", "tue"],   # 2nd music teacher
    "teacher_20": ["thu", "fri"],   # 2nd ict teacher
    "teacher_24": ["wed"],          # 2nd dance teacher
    "teacher_26": ["fri"],          # 2nd literature teacher
}

FIXED_PLACEMENT_SOURCE_SUBJECTS = ["math", "english", "history", "art", "geography", "literature"]
NUM_FIXED_PLACEMENTS = 8


@dataclass
class CurriculumRequirement:
    """One not-yet-placed requirement plus everything needed to both build
    the domain ``TeachingRequirement`` and drive the canonical-schedule
    constructor."""

    id: str
    teacher_ids: tuple[str, ...]
    activity_id: str
    participant_group_id: str
    class_ids: tuple[str, ...]
    weekly_periods: int
    block_policy: LessonBlockPolicy
    distribution_policy: DistributionPolicy = field(default_factory=DistributionPolicy)
    time_preferences: tuple[TimePreference, ...] = ()
    resource_id: str | None = None
    split_group_id: str | None = None
    linked_requirement_id: str | None = None


@dataclass
class Curriculum:
    class_sections: tuple[ClassSection, ...]
    teachers: tuple[Teacher, ...]
    activities: tuple[Activity, ...]
    resources: tuple
    participant_groups: tuple[ParticipantGroup, ...]
    reserved_blocks: tuple[ReservedBlock, ...]
    teacher_availabilities: tuple[TeacherAvailability, ...]
    requirements: list[CurriculumRequirement]


FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)


def _round_robin_assignment(class_ids: list[str], teacher_ids: list[str]) -> dict[str, str]:
    """class_id -> teacher_id, spreading classes evenly over the pool."""
    n = len(teacher_ids)
    return {c: teacher_ids[i % n] for i, c in enumerate(class_ids)}


def build_curriculum(*, extra_unavailability: bool = False) -> Curriculum:
    """Build the full deterministic curriculum.

    ``extra_unavailability`` is the Phase 2B "tight scenario" knob: it
    only ever removes scheduling freedom (more UNAVAILABLE slots) -- it
    never changes the curriculum shape, so the same known-feasibility
    reasoning applies to both scenarios (the constructor is re-run against
    whatever availability is actually configured).
    """
    rng = random.Random(SEED)

    class_sections = tuple(ClassSection(id=c, name=c.replace("_", " ").title()) for c in CLASS_IDS)

    # -- Teachers -----------------------------------------------------------
    teacher_ids_by_subject: dict[str, list[str]] = {}
    teacher_defs: list[Teacher] = []
    next_teacher_num = 1
    for subject, count in SUBJECT_TEACHER_COUNTS.items():
        ids = []
        for _ in range(count):
            tid = f"teacher_{next_teacher_num:02d}"
            teacher_defs.append(Teacher(id=tid, name=f"Teacher {next_teacher_num:02d}"))
            ids.append(tid)
            next_teacher_num += 1
        teacher_ids_by_subject[subject] = ids
    teachers = tuple(teacher_defs)

    # -- Activities -----------------------------------------------------------
    ordinary_subjects = [s for s, _ in BASE_SUBJECTS] + ["german", "russian"]
    activities = tuple(
        [Activity(id=f"subject_{s}", name=s.title()) for s in ordinary_subjects]
        + [Activity(id=cohort_id, name=cohort_id.replace("_", " ").title(), kind=ActivityKind.CLUB)
           for cohort_id, _, _, _ in CLUB_COHORTS]
    )

    # -- Resources --------------------------------------------------------------
    resources = (Resource(id=GYM_RESOURCE_ID, name="Indoor Gym", capacity=1),)

    # -- Reserved blocks ----------------------------------------------------------
    reserved_blocks = tuple(
        ReservedBlock(
            id=cohort_id, name=cohort_id.replace("_", " ").title(), activity_id=activity_id,
            class_sections=tuple(classes), slots=(TimeSlot(day_id, period_id),),
        )
        for (cohort_id, activity_id, day_id, period_id), classes in zip(CLUB_COHORTS, CLUB_COHORT_CLASSES)
    )

    participant_groups: list[ParticipantGroup] = []

    def add_group(gid: str, name: str, class_ids: tuple[str, ...]) -> str:
        if not any(g.id == gid for g in participant_groups):
            participant_groups.append(ParticipantGroup(id=gid, name=name, class_sections=class_ids))
        return gid

    def whole_class_group(class_id: str) -> str:
        return add_group(f"pg_{class_id}", f"All of {class_id}", (class_id,))

    requirements: list[CurriculumRequirement] = []
    req_counter = 0

    def next_id(prefix: str) -> str:
        nonlocal req_counter
        req_counter += 1
        return f"{prefix}_{req_counter:04d}"

    # -- Base ordinary subjects for every class ----------------------------------
    for subject, weekly in BASE_SUBJECTS:
        teacher_pool = teacher_ids_by_subject[subject]
        assignment = _round_robin_assignment(CLASS_IDS, teacher_pool)
        for class_id in CLASS_IDS:
            if subject == "civics" and class_id in _MERGED_CIVICS_CLASS_IDS:
                continue  # this class's civics comes from the merged lesson instead

            block_policy = FLEXIBLE
            distribution_policy = DistributionPolicy()
            time_preferences: tuple[TimePreference, ...] = ()

            if subject == "english" and class_id in REQUIRED_2_2_CLASSES:
                block_policy = LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(2, 2))
            elif subject == "science" and class_id in REQUIRED_3_1_CLASSES:
                block_policy = LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(3, 1))
                distribution_policy = DistributionPolicy(max_periods_per_day=3)
            elif subject == "math" and class_id in REQUIRED_2_1_1_1_CLASSES:
                block_policy = LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(2, 1, 1, 1))
            elif subject == "math" and class_id in PREFERRED_2_1_1_1_CLASSES:
                block_policy = LessonBlockPolicy(BlockPolicyMode.PREFERRED, block_sizes=(2, 1, 1, 1))

            class_num = int(class_id[-2:])
            if subject in ("history", "literature") and class_num % 4 == 0:
                distribution_policy = DistributionPolicy(
                    min_distinct_days=min(weekly, 4),
                    max_periods_per_day=distribution_policy.max_periods_per_day,
                )
            if subject in ("geography", "history") and class_num % 5 == 0:
                preferred = tuple(sorted(rng.sample(range(4), k=2)))
                time_preferences = (TimePreference(preferred_periods=preferred, weight=PreferenceWeight.MEDIUM),)

            resource_id = GYM_RESOURCE_ID if subject in ("sport", "dance") else None

            requirements.append(CurriculumRequirement(
                id=next_id(f"req_{subject}"),
                teacher_ids=(assignment[class_id],),
                activity_id=f"subject_{subject}",
                participant_group_id=whole_class_group(class_id),
                class_ids=(class_id,),
                weekly_periods=weekly,
                block_policy=block_policy,
                distribution_policy=distribution_policy,
                time_preferences=time_preferences,
                resource_id=resource_id,
            ))

    # -- Split language branches for classes 1-5 ---------------------------------
    german_pool = teacher_ids_by_subject["german"]
    russian_pool = teacher_ids_by_subject["russian"]
    for i, class_id in enumerate(SPLIT_LANGUAGE_CLASSES):
        split_group_id = f"split_lang_{class_id}"
        german_gid = add_group(f"pg_{class_id}_german", f"{class_id} German", (class_id,))
        russian_gid = add_group(f"pg_{class_id}_russian", f"{class_id} Russian", (class_id,))

        requirements.append(CurriculumRequirement(
            id=next_id("req_german_split"), teacher_ids=(german_pool[i % len(german_pool)],),
            activity_id="subject_german", participant_group_id=german_gid, class_ids=(class_id,),
            weekly_periods=LANGUAGE_WEEKLY_PERIODS, block_policy=FLEXIBLE, split_group_id=split_group_id,
        ))
        requirements.append(CurriculumRequirement(
            id=next_id("req_russian_split"), teacher_ids=(russian_pool[i % len(russian_pool)],),
            activity_id="subject_russian", participant_group_id=russian_gid, class_ids=(class_id,),
            weekly_periods=LANGUAGE_WEEKLY_PERIODS, block_policy=FLEXIBLE, split_group_id=split_group_id,
        ))

    # -- Plain language for classes 6-15 -----------------------------------------
    for i, class_id in enumerate(PLAIN_GERMAN_CLASSES):
        requirements.append(CurriculumRequirement(
            id=next_id("req_german"), teacher_ids=(german_pool[i % len(german_pool)],),
            activity_id="subject_german", participant_group_id=whole_class_group(class_id),
            class_ids=(class_id,), weekly_periods=LANGUAGE_WEEKLY_PERIODS, block_policy=FLEXIBLE,
        ))
    for i, class_id in enumerate(PLAIN_RUSSIAN_CLASSES):
        requirements.append(CurriculumRequirement(
            id=next_id("req_russian"), teacher_ids=(russian_pool[i % len(russian_pool)],),
            activity_id="subject_russian", participant_group_id=whole_class_group(class_id),
            class_ids=(class_id,), weekly_periods=LANGUAGE_WEEKLY_PERIODS, block_policy=FLEXIBLE,
        ))

    # -- Civics: merged for pairs (individual civics for every other class
    #    was already created by the BASE_SUBJECTS loop above, which skips
    #    exactly the classes in _MERGED_CIVICS_CLASS_IDS). --------------------
    civics_pool = teacher_ids_by_subject["civics"]
    civics_counter = 0
    for pair in MERGED_CIVICS_PAIRS:
        merged_gid = add_group(f"pg_merged_civics_{'_'.join(pair)}", f"Merged civics {pair}", pair)
        requirements.append(CurriculumRequirement(
            id=next_id("req_civics_merged"), teacher_ids=(civics_pool[civics_counter % len(civics_pool)],),
            activity_id="subject_civics", participant_group_id=merged_gid, class_ids=pair,
            weekly_periods=MERGED_CIVICS_WEEKLY_PERIODS, block_policy=FLEXIBLE,
        ))
        civics_counter += 1

    # -- Teacher availability -----------------------------------------------------
    teacher_availabilities: list[TeacherAvailability] = []
    all_days = ["mon", "tue", "wed", "thu", "fri"]
    all_periods = [f"p{i}" for i in range(1, 9)]

    for teacher_id, unavailable_days in PART_TIME_TEACHERS.items():
        for day_id in unavailable_days:
            for period_id in all_periods:
                teacher_availabilities.append(
                    TeacherAvailability(teacher_id, day_id, period_id, AvailabilityStatus.UNAVAILABLE)
                )

    for teacher_id, day_id, period_id in PREFER_NOT_TEACHER_SLOTS:
        teacher_availabilities.append(TeacherAvailability(teacher_id, day_id, period_id, AvailabilityStatus.PREFER_NOT))

    if extra_unavailability:
        # Tight scenario: a broader swath of teachers lose their afternoon
        # on one day, cutting into scheduling freedom without invalidating
        # the known-feasible construction (the constructor runs against
        # these same restrictions when proving feasibility).
        extra_targets = [t.id for t in teacher_defs if t.id not in PART_TIME_TEACHERS][::4]
        for teacher_id in extra_targets:
            day_id = rng.choice(all_days)
            for period_id in all_periods[4:]:  # afternoon block only
                teacher_availabilities.append(
                    TeacherAvailability(teacher_id, day_id, period_id, AvailabilityStatus.UNAVAILABLE)
                )

    return Curriculum(
        class_sections=class_sections,
        teachers=teachers,
        activities=activities,
        resources=resources,
        participant_groups=tuple(participant_groups),
        reserved_blocks=reserved_blocks,
        teacher_availabilities=tuple(teacher_availabilities),
        requirements=requirements,
    )
