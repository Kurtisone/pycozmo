"""

Activity representation and reading.

"""

import os
import random
import time
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from .logger import logger, logger_behavior
from .emotions import DecayGraph, Node
from .json_loader import get_json_files, load_json_file


__all__ = [
    "MoodScorer",
    "ActivityStrategy",
    "BehaviorChooser",
    "Objective",
    "Activity",

    "load_activities",
]


class BehaviorChooser:
    """
    The behaviors an activity can run, and the order it tries them in.

    Three types appear in the resources. "Selection" leaves the choice to the application.
    "StrictPriority" names its behaviors in order, as plain identifiers. "Scoring" gives each
    behavior a flat score and, optionally, a repetition penalty graph.
    """

    __slots__ = [
        "choice_type",
        "behaviors",
        "behavior_names",
        "flat_scores",
        "repetition_penalties",
        "last_run_times",
    ]

    def __init__(self,
                 choice_type: str,
                 behaviors: List) -> None:

        self.choice_type = str(choice_type)
        self.behaviors = behaviors
        # "Scoring" names its behaviors in dictionaries, "StrictPriority" as plain strings.
        self.behavior_names: List[str] = [
            b["behaviorID"] if isinstance(b, dict) else str(b) for b in behaviors]
        self.flat_scores: List[float] = [self.get_flat_score(b) for b in behaviors]
        self.repetition_penalties: List[Optional[DecayGraph]] = \
            [self.get_repetition_penalty(b) for b in behaviors]
        self.last_run_times: List[Optional[float]] = [None] * len(behaviors)

    @classmethod
    def from_json(cls, data: Dict) -> "BehaviorChooser":
        return cls(
            choice_type=data['type'],
            behaviors=data.get('behaviors', [])
        )

    def reset(self) -> None:
        """ Forget what has run, leaving every behavior at its full score. """
        self.last_run_times = [None] * len(self.behaviors)

    @staticmethod
    def get_flat_score(behavior: Any) -> float:
        """ A behavior's score before any penalty. Only "Scoring" carries one. """
        if not isinstance(behavior, dict):
            return 0.0
        return float(behavior.get("scoring", {}).get("flatScore", 0.0))

    @staticmethod
    def get_repetition_penalty(behavior: Any) -> Optional[DecayGraph]:
        """ A behavior's repetition penalty graph, or None when it carries none. """
        if not isinstance(behavior, dict):
            return None
        nodes = behavior.get("scoring", {}).get("repetitionPenalty", {}).get("nodes")
        if not nodes:
            return None
        return DecayGraph([Node(x=n['x'], y=n['y']) for n in nodes])

    @staticmethod
    def read_penalty(graph: DecayGraph, elapsed: float) -> float:
        """
        Read a repetition penalty graph, held inside its own range.

        Past its last node a graph extrapolates, which would take the fraction above one, and the
        lockout MeetCozmo_InteractWithFaces uses ends on two nodes sharing an x, which makes the
        extrapolated line flat at nought rather than at one. Reading at the last node instead, and
        clamping the result, keeps both to what the nodes say.
        """
        return min(max(float(graph.get_increment(min(elapsed, graph.nodes_x[-1]))), 0.0), 1.0)

    def get_scores(self, now: Optional[float] = None) -> List[float]:
        """
        Each behavior's score, cut back for having run recently.

        A repetition penalty graph maps the seconds since the behavior last ran to the fraction of
        its score it has won back: GuardDog scores nothing for five minutes and is whole again a
        quarter of an hour on, and the bored animations keep half their score for nine seconds, so
        that the robot idles normally rather than playing two bored sequences in a row. The x axis
        used to be read as a number of repetitions, from which a behavior never recovered - the
        three hundreds and nine hundreds in the graphs are seconds.
        """
        now = time.perf_counter() if now is None else now
        scores = []
        for score, penalty, last_run in zip(self.flat_scores, self.repetition_penalties, self.last_run_times):
            if penalty is not None and last_run is not None:
                score *= self.read_penalty(penalty, now - last_run)
            scores.append(score)
        return scores

    def behavior_ran(self, ref: Any, now: Optional[float] = None) -> None:
        """ Note a behavior having run, so that its repetition penalty applies from now. """
        if isinstance(ref, str):
            idx = self.behavior_names.index(ref)
        elif isinstance(ref, int):
            idx = ref
        else:
            raise TypeError('Invalid behavior index: {}'.format(ref))
        self.last_run_times[idx] = time.perf_counter() if now is None else now

    def get_sorted_choices(self, now: Optional[float] = None) -> Optional[List[str]]:
        """
        The behaviors to try, best first, or None when the activity chooses none itself.

        The two populated branches used to disagree on what a choice was, "StrictPriority"
        returning the raw behavior dictionaries and "Scoring" returning identifiers. Both return
        identifiers now, which is what it takes to look a behavior up.
        """
        if self.choice_type == 'Selection':
            # The application drives this one.
            return None
        if self.choice_type == 'StrictPriority':
            return list(self.behavior_names)
        if self.choice_type == 'Scoring':
            scores = self.get_scores(now)
            total_score = sum(scores)
            if not self.behavior_names or total_score <= 0.0:
                # Nothing to offer, every behavior having run too recently.
                return None
            distribution = [score / total_score for score in scores]
            # Drawing without replacement gives an order weighted by the scores, so a high scorer
            # usually comes first without always doing so. Behaviors down to zero are left out.
            size = sum(1 for p in distribution if p > 0.0)
            # np.ndarray.tolist() is typed as Any, so bind it before returning.
            choices: List[str] = np.random.choice(
                self.behavior_names, p=distribution, size=size, replace=False).tolist()
            return choices
        raise ValueError('Unknown choice type: {}'.format(self.choice_type))


class MoodScorer:
    """
    Score one emotion through a graph, for the strategies that gate an activity on the mood.

    Socialize is the only activity in the resources that carries one: its graph scores 1.0 while
    Social is at or below 0.3 and 0.0 above it, and the activity needs 0.5 to start. The robot
    goes socializing when it has not been social for a while, in other words.
    """

    __slots__ = [
        "emotion_type",
        "score_graph",
        "track_delta",
    ]

    def __init__(self,
                 emotion_type: str,
                 score_graph: DecayGraph,
                 track_delta: bool = False) -> None:
        self.emotion_type = str(emotion_type)
        self.score_graph = score_graph
        # Scoring how much the emotion has moved rather than where it stands. Every scorer in the
        # resources asks for the value, so this is read and reported rather than implemented.
        self.track_delta = bool(track_delta)

    @classmethod
    def from_json(cls, data: Dict) -> "MoodScorer":
        return cls(
            emotion_type=data['emotionType'],
            score_graph=DecayGraph([Node(x=n['x'], y=n['y']) for n in data['scoreGraph']['nodes']]),
            track_delta=data.get('trackDelta', False))

    def score(self, mood: Dict[str, float]) -> float:
        """ The score the mood earns, the graph read at the emotion's current value. """
        if self.track_delta:
            logger.error("Mood scorer for '{}' asks for delta tracking, which is not implemented.".format(
                self.emotion_type))
            return 0.0
        return float(self.score_graph.get_increment(mood.get(self.emotion_type, 0.0)))


class ActivityStrategy:
    """
    When an activity wants to run, how long it runs, and how long it rests afterwards.

    Only the "Simple" strategy is evaluated. The others gate on what this library does not have: a
    spark sent from the application, the nurture needs (Energy, Repair and Play), a pyramid of
    cubes, or a player asking for a game. An activity carrying one of them never wants to run,
    which is also what the robot does while nothing has sparked it and its needs are full.
    """

    #: Strategy types this implementation can evaluate.
    SUPPORTED_TYPES = ("Simple", )

    __slots__ = [
        "type",
        "can_end_duration",
        "should_end_duration",
        "cooldown_base",
        "cooldown_randomness",
        "start_in_cooldown",
        "required_min_start_mood_score",
        "start_mood_scorers",
        "required_recent_on_treads",
        "max_time_without_interaction",
        "feature_gate",
    ]

    def __init__(self,
                 strategy_type: str,
                 can_end_duration: Optional[float] = None,
                 should_end_duration: Optional[float] = None,
                 cooldown_base: float = 0.0,
                 cooldown_randomness: float = 0.0,
                 start_in_cooldown: bool = False,
                 required_min_start_mood_score: Optional[float] = None,
                 start_mood_scorers: Optional[List[MoodScorer]] = None,
                 required_recent_on_treads: Optional[float] = None,
                 max_time_without_interaction: Optional[float] = None,
                 feature_gate: Optional[str] = None) -> None:
        self.type = str(strategy_type)
        #: How long before the activity may end, and how long before it should. A negative
        #: should-end duration means never, which is how the severe-needs activities hold on until
        #: the need is met; no duration at all means the application ends the activity.
        self.can_end_duration = float(can_end_duration) if can_end_duration is not None else None
        self.should_end_duration = float(should_end_duration) if should_end_duration is not None else None
        self.cooldown_base = float(cooldown_base)
        self.cooldown_randomness = float(cooldown_randomness)
        self.start_in_cooldown = bool(start_in_cooldown)
        self.required_min_start_mood_score = \
            float(required_min_start_mood_score) if required_min_start_mood_score is not None else None
        self.start_mood_scorers = start_mood_scorers or []
        self.required_recent_on_treads = \
            float(required_recent_on_treads) if required_recent_on_treads is not None else None
        # Read but not acted on: nothing tracks how long the player has left the robot alone.
        self.max_time_without_interaction = \
            float(max_time_without_interaction) if max_time_without_interaction is not None else None
        self.feature_gate = str(feature_gate) if feature_gate is not None else None

    @classmethod
    def from_json(cls, data: Dict) -> "ActivityStrategy":
        return cls(
            strategy_type=data['type'],
            can_end_duration=data.get('activityCanEndDurationSecs'),
            should_end_duration=data.get('activityShouldEndDurationSecs'),
            cooldown_base=data.get('cooldownBaseSecs', 0.0),
            cooldown_randomness=data.get('cooldownRandomnessSecs', 0.0),
            start_in_cooldown=data.get('startInCooldown', False),
            required_min_start_mood_score=data.get('requiredMinStartMoodScore'),
            start_mood_scorers=[MoodScorer.from_json(d) for d in data.get('startMoodScorer', [])],
            required_recent_on_treads=data.get('requiredRecentOnTreadsEventSecs'),
            max_time_without_interaction=data.get('maxTimeWithoutInteractionSecs'),
            feature_gate=data.get('featureGate'))

    @property
    def is_supported(self) -> bool:
        """ Whether this implementation can tell when the strategy wants to run. """
        return self.type in self.SUPPORTED_TYPES

    def get_cooldown(self) -> float:
        """ How long to rest after a run, the randomness drawn afresh each time. """
        return self.cooldown_base + random.uniform(0.0, self.cooldown_randomness)

    def mood_allows(self, mood: Dict[str, float]) -> bool:
        """
        Whether the mood scores high enough to start. An activity with no scorer always does.

        The scores are added up. Socialize is the only activity carrying a scorer and it carries
        one, so adding and averaging agree on every activity in the resources.
        """
        if self.required_min_start_mood_score is None or not self.start_mood_scorers:
            return True
        score = sum(scorer.score(mood) for scorer in self.start_mood_scorers)
        return score >= self.required_min_start_mood_score


class Objective:
    __slots__ = [
        "objective",
        "behavior_id",
        "ignore_if_locked",
        "probability_to_require_objective",
        "random_completions_needed_min",
        "random_completions_needed_max",
    ]

    def __init__(self,
                 objective: str,
                 behavior_id: str,
                 ignore_if_locked: str,
                 probability_to_require_objective: float,
                 random_completions_needed_min: Optional[int] = 0,
                 random_completions_needed_max: Optional[int] = 0) -> None:
        self.objective = str(objective)
        self.behavior_id = str(behavior_id)
        self.ignore_if_locked = str(ignore_if_locked)
        self.probability_to_require_objective = float(probability_to_require_objective)
        self.random_completions_needed_min = \
            int(random_completions_needed_min) if random_completions_needed_min is not None else 0
        self.random_completions_needed_max = \
            int(random_completions_needed_max) if random_completions_needed_max is not None else 0

    @classmethod
    def from_json(cls, data: Dict) -> "Objective":
        return cls(objective=data['objective'],
                   behavior_id=data['behaviorID'],
                   ignore_if_locked=data['ignoreIfLocked'],
                   probability_to_require_objective=data['probabilityToRequireObjective'],
                   random_completions_needed_min=data.get('randomCompletionsNeededMin', 0),
                   random_completions_needed_max=data.get('randomCompletionsNeededMax', 0))


class Activity:
    """
    Activity representation class.

    An activity is a set of behaviors together with the rules for when the robot runs them.
    Freeplay, the activity the robot is in when nobody is driving it, holds the others as
    sub-activities in priority order.
    """

    __slots__ = [
        "id",
        "type",
        "strategy",
        "behavior_chooser",
        "interlude_chooser",
        "sub_activities",
        "start_time",
        "cooldown_end_time",
    ]

    def __init__(self,
                 activity_id: str,
                 activity_type: str,
                 strategy: ActivityStrategy,
                 behavior_chooser: Optional[BehaviorChooser] = None,
                 interlude_chooser: Optional[BehaviorChooser] = None,
                 sub_activities: Optional[List[Dict]] = None) -> None:

        self.id = str(activity_id)
        self.type = str(activity_type)
        self.strategy = strategy
        self.behavior_chooser = behavior_chooser
        self.interlude_chooser = interlude_chooser
        self.sub_activities = sub_activities or []
        # When the activity took the robot, or None while it does not have it.
        self.start_time: Optional[float] = None
        # Time before which the activity will not start again.
        self.cooldown_end_time = \
            time.perf_counter() + strategy.get_cooldown() if strategy.start_in_cooldown else 0.0

    @staticmethod
    def base_kwargs(data: Dict) -> Dict[str, Any]:
        """ The attributes every activity type reads the same way. """
        return dict(
            activity_id=data['activityID'],
            activity_type=data['activityType'],
            strategy=ActivityStrategy.from_json(data['activityStrategy']),
            behavior_chooser=BehaviorChooser.from_json(data['behaviorChooser'])
            if 'behaviorChooser' in data else None,
            interlude_chooser=BehaviorChooser.from_json(data['interludeBehaviorChooser'])
            if 'interludeBehaviorChooser' in data else None,
            sub_activities=data.get('subActivities'))

    def wants_to_run(self,
                     mood: Optional[Dict[str, float]] = None,
                     now: Optional[float] = None,
                     on_treads_time: Optional[float] = None) -> bool:
        """ Whether the activity would take the robot now. """
        now = time.perf_counter() if now is None else now
        if not self.strategy.is_supported:
            logger_behavior.debug("Activity {} has a {} strategy, which is not implemented.".format(
                self.id, self.strategy.type))
            return False
        if now < self.cooldown_end_time:
            return False
        if not self.strategy.mood_allows(mood or {}):
            return False
        recent_on_treads = self.strategy.required_recent_on_treads
        if recent_on_treads is not None and \
                (on_treads_time is None or now - on_treads_time > recent_on_treads):
            return False
        return True

    def should_end(self, now: Optional[float] = None) -> bool:
        """ Whether the activity has run long enough to give way to another. """
        now = time.perf_counter() if now is None else now
        duration = self.strategy.should_end_duration
        if self.start_time is None or duration is None or duration < 0.0:
            return False
        return now - self.start_time >= duration

    def started(self, now: Optional[float] = None) -> None:
        """
        Note the activity taking the robot.

        The choosers keep what they remember. Their repetition penalties are counted in seconds
        since a behavior last ran, and NothingToDo gives the robot up after every single behavior
        it runs, so wiping them on the way in would be wiping them constantly.
        """
        self.start_time = time.perf_counter() if now is None else now

    def ended(self, now: Optional[float] = None) -> None:
        """ Note the activity giving the robot up, and put it on cooldown. """
        now = time.perf_counter() if now is None else now
        self.start_time = None
        self.cooldown_end_time = now + self.strategy.get_cooldown()

    def choose(self, can_run: Callable[[str], bool], now: Optional[float] = None) -> Optional[str]:
        """
        The behavior the activity would run now, or None if it can run none.

        Interludes come first: the resources keep for them what is meant to be slipped in between
        two ordinary behaviors, a needs request or the announcement of earned sparks.
        """
        for chooser in (self.interlude_chooser, self.behavior_chooser):
            if chooser is None:
                continue
            for behavior_id in chooser.get_sorted_choices(now) or ():
                if can_run(behavior_id):
                    return behavior_id
        return None

    def ran(self, behavior_id: str, now: Optional[float] = None) -> None:
        """ Note a behavior having run, so that it holds back for a while before running again. """
        for chooser in (self.interlude_chooser, self.behavior_chooser):
            if chooser is not None and behavior_id in chooser.behavior_names:
                chooser.behavior_ran(behavior_id, now)


class BehaviorsActivity(Activity):
    """ An activity that is no more than a set of behaviors to choose from. """

    @classmethod
    def from_json(cls, data: Dict) -> "BehaviorsActivity":
        return cls(**cls.base_kwargs(data))


class VoiceCommandActivity(Activity):

    @classmethod
    def from_json(cls, data: Dict) -> "VoiceCommandActivity":
        return cls(**cls.base_kwargs(data))


class FeedingActivity(Activity):

    @classmethod
    def from_json(cls, data: Dict) -> "FeedingActivity":
        kwargs = cls.base_kwargs(data)
        # The universal chooser holds the behaviors that may run whichever sub-activity is in
        # charge. Feeding has no sub-activities, so it is simply its chooser.
        if 'universalChooser' in data:
            kwargs["behavior_chooser"] = BehaviorChooser.from_json(data['universalChooser'])
        return cls(**kwargs)


class FreeplayActivity(Activity):
    __slots__ = [
        "cube_only_activity",
        "face_only_activity",
        "face_and_cube_activity",
        "no_face_no_cube_activity",
    ]

    def __init__(self,
                 cube_only_activity: str,
                 face_only_activity: str,
                 face_and_cube_activity: str,
                 no_face_no_cube_activity: str,
                 *args: Any, **kwargs: Any) -> None:
        self.cube_only_activity = str(cube_only_activity)
        self.face_only_activity = str(face_only_activity)
        self.face_and_cube_activity = str(face_and_cube_activity)
        self.no_face_no_cube_activity = str(no_face_no_cube_activity)

        super().__init__(*args, **kwargs)

    @classmethod
    def from_json(cls, data: Dict) -> "FreeplayActivity":
        return cls(
            cube_only_activity=data['desiredActivityNames']['cubeOnlyActivityName'],
            face_only_activity=data['desiredActivityNames']['faceOnlyActivityName'],
            face_and_cube_activity=data['desiredActivityNames']['faceAndCubeActivityName'],
            no_face_no_cube_activity=data['desiredActivityNames']['noFaceNoCubeActivityName'],
            **cls.base_kwargs(data))


class SparkedActivity(Activity):
    __slots__ = [
        "require_spark",
        "min_time",
        "max_time",
        "reps",
        "behavior_objective",
        "soft_spark_trigger",
        "sub_activity_delegate",
        "spark_success_trigger",
        "spark_fail_trigger",
        "drive_start_trigger",
        "drive_loop_trigger",
        "drive_stop_trigger",
    ]

    def __init__(self,
                 require_spark: str,
                 min_time_secs: float,
                 max_time_secs: float,
                 reps: int,
                 behavior_objective: str,
                 soft_spark_trigger: str,
                 sub_activity_delegate: Optional[Activity] = None,
                 spark_success_trigger: Optional[str] = None,
                 spark_fail_trigger: Optional[str] = None,
                 drive_start_trigger: Optional[str] = None,
                 drive_loop_trigger: Optional[str] = None,
                 drive_stop_trigger: Optional[str] = None,
                 *args: Any, **kwargs: Any) -> None:
        self.require_spark = str(require_spark)
        self.min_time = float(min_time_secs)
        self.max_time = float(max_time_secs)
        self.reps = int(reps)
        self.behavior_objective = str(behavior_objective)
        self.soft_spark_trigger = str(soft_spark_trigger)

        self.sub_activity_delegate = sub_activity_delegate
        self.spark_success_trigger = spark_success_trigger
        self.spark_fail_trigger = spark_fail_trigger

        self.drive_start_trigger = drive_start_trigger
        self.drive_loop_trigger = drive_loop_trigger
        self.drive_stop_trigger = drive_stop_trigger

        super().__init__(*args, **kwargs)

    @classmethod
    def from_json(cls, data: Dict) -> "SparkedActivity":
        if 'subActivityDelegate' in data:
            sub_act_delegate = from_dict(data['subActivityDelegate'])
        else:
            sub_act_delegate = None
        return cls(
            require_spark=data['requireSpark'],
            min_time_secs=data['minTimeSecs'],
            max_time_secs=data['maxTimeSecs'],
            reps=data['numberOfRepetitions'],
            behavior_objective=data['behaviorObjective'],
            soft_spark_trigger=data['softSparkTrigger'],
            sub_activity_delegate=sub_act_delegate,
            spark_success_trigger=data.get('sparksSuccessTrigger'),
            spark_fail_trigger=data.get('sparksFailTrigger'),
            drive_start_trigger=data.get('driveStartAnimTrigger'),
            drive_loop_trigger=data.get('driveLoopAnimTrigger'),
            drive_stop_trigger=data.get('driveStopAnimTrigger'),
            **cls.base_kwargs(data))


class PyramidActivity(Activity):
    __slots__ = [
        "setup_chooser",
        "build_chooser",
        "needs_action_id",
    ]

    def __init__(self,
                 setup_chooser: BehaviorChooser,
                 build_chooser: BehaviorChooser,
                 needs_action_id: Optional[str] = None,
                 *args: Any, **kwargs: Any) -> None:
        self.setup_chooser = setup_chooser
        self.build_chooser = build_chooser
        self.needs_action_id = needs_action_id

        super().__init__(*args, **kwargs)

    @classmethod
    def from_json(cls, data: Dict) -> "PyramidActivity":
        return cls(
            setup_chooser=BehaviorChooser.from_json(data['setupChooser']),
            build_chooser=BehaviorChooser.from_json(data['buildChooser']),
            needs_action_id=data.get('needsActionID'),
            **cls.base_kwargs(data))


class SocializeActivity(Activity):
    __slots__ = [
        "max_face_iterations",
        "required_objectives",
    ]

    def __init__(self,
                 max_face_iterations: int,
                 required_objectives: List[Objective],
                 *args: Any, **kwargs: Any) -> None:
        self.max_face_iterations = int(max_face_iterations)
        self.required_objectives = required_objectives

        super().__init__(*args, **kwargs)

    @classmethod
    def from_json(cls, data: Dict) -> "SocializeActivity":
        return cls(
            max_face_iterations=data['maxNumFindFacesSearchIterations'],
            required_objectives=[Objective.from_json(d) for d in data['requiredObjectives']],
            **cls.base_kwargs(data))


class NeedsActivity(Activity):

    @classmethod
    def from_json(cls, data: Dict) -> "NeedsActivity":
        return cls(**cls.base_kwargs(data))


def from_dict(info: Dict) -> Activity:
    if info['activityType'] == 'VoiceCommand':
        return VoiceCommandActivity.from_json(info)

    elif info['activityType'] == 'BehaviorsOnly':
        return BehaviorsActivity.from_json(info)

    elif info['activityType'] == 'Feeding':
        return FeedingActivity.from_json(info)

    elif info['activityType'] == 'Freeplay':
        return FreeplayActivity.from_json(info)

    elif info['activityType'] == 'Sparked':
        return SparkedActivity.from_json(info)

    elif info['activityType'] == 'BuildPyramid':
        return PyramidActivity.from_json(info)

    elif info['activityType'] == 'Socialize':
        return SocializeActivity.from_json(info)

    elif info['activityType'] == 'NeedsExpression':
        return NeedsActivity.from_json(info)

    else:
        return Activity(**Activity.base_kwargs(info))


def load_activities(resource_dir: str) -> Dict[str, Activity]:
    """ Load activity map from cozmo resources. """

    # TODO: cozmo_resources/config/engine/needs_action_config.json
    # TODO: cozmo_resources/config/engine/do_a_trick_weights.json

    start_time = time.perf_counter()

    activity_folders = [
        os.path.join('cozmo_resources', 'config', 'engine', 'behaviorSystem', 'activities_config.json'),
        os.path.join('cozmo_resources', 'config', 'engine', 'behaviorSystem', 'behavior_system_config.json'),
        os.path.join('cozmo_resources', 'config', 'engine', 'behaviorSystem', 'activities')
    ]
    activity_files = get_json_files(resource_dir, activity_folders)

    activities = {}

    for filename in activity_files:
        json_data = load_json_file(filename)
        if isinstance(json_data, list):
            for activity in json_data:
                activities[activity['activityID']] = from_dict(activity)
        else:
            activities[json_data['activityID']] = from_dict(json_data)

    logger.debug("Loaded {} activities in {:.02f} s.".format(len(activities), time.perf_counter() - start_time))

    return activities
