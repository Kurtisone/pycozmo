"""

Behavior representation and reading.

"""

import os
import threading
import time
from typing import Dict, List, Optional, Sequence, Any

from . import event
from . import client
from . import robot
from .logger import logger
from .json_loader import get_json_files, load_json_file


__all__ = [
    "ReactionTrigger",
    "Behavior",

    "load_behaviors",
    "load_reaction_trigger_behavior_map",
]


class ReactionTrigger:
    """ Reaction trigger representation class. """
    __slots__ = [
        "name",
        "behavior_id",
        "should_resume_last",
        "max_confidence",
        "cooldown_time",
        "conf",
        "last_run",
    ]

    def __init__(self, name: str, behavior_id: str, should_resume_last: Optional[bool] = False,
                 max_confidence: Optional[float] = None, cooldown_time: float = 0.0,
                 conf: Optional[Dict] = None):
        self.name = str(name)
        self.behavior_id = str(behavior_id)
        self.should_resume_last = bool(should_resume_last)
        #: Confidence at or below which the reaction applies, when a trigger names several.
        self.max_confidence = None if max_confidence is None else float(max_confidence)
        #: Seconds before the reaction may run again. Zero for no cooldown at all.
        self.cooldown_time = float(cooldown_time)
        #: The whole configuration entry, which carries parameters nothing reads yet.
        self.conf = dict(conf or {})
        # When the reaction last ran, from time.perf_counter(). 0.0 until it runs once.
        self.last_run = 0.0

    @classmethod
    def from_json(cls, data: Dict) -> "ReactionTrigger":
        # The confidence and the cooldown only ever come under frustrationParams in the resources,
        # Frustration being the only trigger that grades its reaction.
        frustration = data.get('frustrationParams', {})
        return cls(name=data['reactionTrigger'],
                   behavior_id=data['behaviorID'],
                   should_resume_last=data.get('genericStrategyParams', {}).get('shouldResumeLast'),
                   max_confidence=frustration.get('maxConfidence'),
                   cooldown_time=frustration.get('cooldownTime_s', 0.0),
                   conf=data)

    def is_on_cooldown(self, now: Optional[float] = None) -> bool:
        """ Whether the reaction ran too recently to run again. """
        if not self.cooldown_time or not self.last_run:
            return False
        now = time.perf_counter() if now is None else now
        return now - self.last_run < self.cooldown_time

    def ran(self, now: Optional[float] = None) -> None:
        """ Record the reaction as just run, which starts its cooldown. """
        self.last_run = time.perf_counter() if now is None else now


class Behavior(event.Dispatcher):
    """ Behavior representation class. """

    def __init__(self, cli: client.Client, conf: Any) -> None:
        super().__init__()
        self.cli = cli
        self.conf = conf

    def get_id(self) -> str:
        behavior_id: str = self.conf["behaviorID"]
        return behavior_id

    def give_up(self, reason: str) -> None:
        """ Report the behavior as done without doing anything, and say why. """
        logger.warning("Behavior '{}' {}.".format(self.get_id(), reason))
        self.cli.conn.post_event(event.EvtBehaviorDone, self.cli)

    def done(self) -> None:
        """ Report the behavior as done. """
        self.cli.conn.post_event(event.EvtBehaviorDone, self.cli)

    def post_emotion_event(self, name: str) -> None:
        """ Post an emotion event, by name, for the brain to apply to the mood. """
        self.cli.conn.post_event(event.EvtEmotionEvent, self.cli, name)

    def wants_to_run(self) -> bool:
        """
        Whether the behavior would take the robot if the activity engine offered it now.

        A behavior whose class is not implemented never would: activating it only logs that and
        reports it done, and the engine would offer it the robot again straight away.
        """
        return False

    def activate(self) -> None:
        self.give_up("not implemented")

    def deactivate(self) -> None:
        pass


class BehaviorPlayAnim(Behavior):
    """
    Play a sequence of animation triggers.

    An animation trigger is a "CladEvent" name from
    cozmo_resources/assets/animationGroupMaps/AnimationTriggerMap.json, which the client resolves
    to an animation group. Behaviors that only react with an animation need nothing more than the
    triggers: either "animTriggers" in their own configuration, or, for the reaction behaviors
    whose configuration does not name any, the `default_anim_triggers` attribute of a subclass.
    """

    #: Animation triggers for subclasses whose configuration carries none of its own.
    default_anim_triggers: Sequence[str] = ()

    def __init__(self, cli: client.Client, conf: Any):
        super().__init__(cli, conf)
        self.anim_triggers: List[str] = list(conf.get("animTriggers", []))
        # Triggers left to play during the current activation, and the position in them.
        self.sequence: List[str] = []
        self.current_trigger = 0
        self.add_handler(event.EvtAnimationCompleted, self._on_animation_completed)

    def get_anim_triggers(self) -> Sequence[str]:
        """ Animation triggers to play, in order, on activation. """
        return self.anim_triggers or self.default_anim_triggers

    def wants_to_run(self) -> bool:
        # A strategy this library cannot evaluate holds the behavior back. ReactToObstacle is the
        # only behavior in the resources carrying one, and it asks for ObstacleDetected.
        strategy = self.conf.get("wantsToRunStrategyConfig")
        if strategy is not None:
            logger.debug("Behavior '{}' wants a {} strategy, which is not implemented.".format(
                self.get_id(), strategy.get("strategyType")))
            return False
        # Playing nothing at all would leave the engine looking for something to do again at once.
        groups = self.cli.animation_groups or {}
        return any(trigger in groups for trigger in self.get_anim_triggers())

    def activate(self) -> None:
        # An animation group the assets do not define never completes, which would leave the
        # behavior active forever, so unknown triggers are dropped before anything is played.
        groups = self.cli.animation_groups or {}
        self.sequence = [trigger for trigger in self.get_anim_triggers() if trigger in groups]
        self.current_trigger = 0
        if not self.sequence:
            self.give_up("has no animation trigger to play")
            return
        self._play_next()

    def _play_next(self) -> None:
        trigger = self.sequence[self.current_trigger]
        self.current_trigger += 1
        self.cli.play_anim_group(trigger)

    def _on_animation_completed(self, cli: client.Client) -> None:
        if self.current_trigger < len(self.sequence):
            self._play_next()
        else:
            self.on_sequence_completed()

    def on_sequence_completed(self) -> None:
        """ Called once the whole sequence has played. """
        self.done()

    def deactivate(self) -> None:
        self.cli.cancel_anim()


class BehaviorPlayArbitraryAnim(BehaviorPlayAnim):
    """ Play a random animation trigger. """

    def activate(self) -> None:
        # TODO: Pick random animation trigger and put it in sequence
        super().activate()


class BehaviorAcknowledge(BehaviorPlayAnim):
    """ AcknowledgeFace and AcknowledgeObject - acknowledge what was just seen. """

    def get_anim_triggers(self) -> Sequence[str]:
        # Both behaviors name their animation trigger, under a key of their own.
        trigger = self.conf.get("ReactionAnimGroup")
        return (trigger, ) if trigger else ()


class BehaviorReactToCliff(BehaviorPlayAnim):
    """ ReactToCliff behavior - currently, just plays animation. """

    # TODO: Back away from the cliff.
    default_anim_triggers = ("ReactToCliff", )


class BehaviorReactToPickup(BehaviorPlayAnim):
    """ ReactToPickup behavior - react to being lifted off the ground. """

    default_anim_triggers = ("ReactToPickup", )


class BehaviorReactToImpact(BehaviorPlayAnim):
    """ ReactToImpact behavior - react to hitting something. """

    # Reached from the RobotFalling reaction trigger, hence a fall that ends in an impact.
    default_anim_triggers = ("ReactToImpact", )


class BehaviorReactToUnexpectedMovement(BehaviorPlayAnim):
    """ ReactToUnexpectedMovement behavior - react to being moved or held back while driving. """

    # TODO: The engine has severe low-energy and low-repair variants of this reaction.
    default_anim_triggers = ("ReactToUnexpectedMovement", )

    def on_sequence_completed(self) -> None:
        # Being shoved around costs confidence.
        self.post_emotion_event("ReactToUnexpectedMovement")
        super().on_sequence_completed()


class BehaviorReactToRobotOnBack(BehaviorPlayAnim):
    """ ReactToRobotOnBack behavior - roll back onto the treads. """

    # The animation drives the lift and the head to flip the robot over by itself.
    default_anim_triggers = ("FlipDownFromBack", )


class BehaviorReactToRobotOnFace(BehaviorPlayAnim):
    """ ReactToRobotOnFace behavior - roll off the face and back onto the treads. """

    # TODO: FailedToRightFromFace, when the robot is still on its face afterwards.
    default_anim_triggers = ("FacePlantRoll", )


class BehaviorReactToRobotOnSide(BehaviorPlayAnim):
    """ ReactToRobotOnSide behavior - ask to be put back on the treads. """

    def get_anim_triggers(self) -> Sequence[str]:
        # The robot cannot right itself from its side, so it asks, facing the side it lies on.
        if self.cli.robot_orientation == robot.RobotOrientation.ON_LEFT_SIDE:
            return ("ReactToOnLeftSide", )
        return ("ReactToOnRightSide", )


class BehaviorReactToRobotShaken(BehaviorPlayAnim):
    """ ReactToRobotShaken behavior - recover from being shaken. """

    # TODO: Loop DizzyShakeLoop while the shaking lasts and pick the reaction by its intensity
    #  (DizzyReactionSoft, DizzyReactionMedium, DizzyReactionHard).
    default_anim_triggers = ("DizzyShakeStop", )


class BehaviorReactToPet(BehaviorPlayAnim):
    """ ReactToPet behavior - react to a cat or a dog being seen. """

    # TODO: PetDetectionShort_Cat and PetDetectionShort_Dog, once the pet type is known.
    default_anim_triggers = ("PetDetectionShort", )


class BehaviorReactToCubeMoved(BehaviorPlayAnim):
    """ ReactToCubeMoved behavior - react to a cube being moved by the player. """

    default_anim_triggers = ("CubeMovedSense", )


class BehaviorReactToSparked(BehaviorPlayAnim):
    """ ReactToSparked behavior - react to being sparked from the application. """

    default_anim_triggers = ("SparkGetIn", )


class BehaviorReactToFrustration(BehaviorPlayAnim):
    """ ReactToFrustration behavior - react to a failed attempt at something. """

    def get_anim_triggers(self) -> Sequence[str]:
        # Minor and Major frustration differ only by their configuration.
        trigger = self.conf.get("anim")
        return (trigger, ) if trigger else ()

    def on_sequence_completed(self) -> None:
        # Getting over it restores confidence: 0.1 for the minor variant, a full 1.0 for the major.
        emotion_event = self.conf.get("finalEmotionEvent")
        if emotion_event:
            self.post_emotion_event(emotion_event)
        super().on_sequence_completed()

    # TODO: For the major variant, drive away by a random randomDrive* amount.


class BehaviorRamIntoBlock(BehaviorPlayAnim):
    """ RamIntoBlock behavior - drive into a block that cannot be approached to dock with. """

    # TODO: Drive into the block. Only the sound of the crash is played for now.
    default_anim_triggers = ("SoundOnlyRamIntoBlock", )


class BehaviorFistBump(BehaviorPlayAnim):
    """ FistBump behavior - ask for a fist bump. """

    # The bump itself is felt on the accelerometer, which is not wired up, so the robot asks
    # once and is left hanging.
    # TODO: Look for a face first (maxTimeToLookForFace_s, abortIfNoFaceFound), detect the bump
    #  and play FistBumpSuccess, retrying with FistBumpRequestRetry.
    default_anim_triggers = ("FistBumpRequestOnce", "FistBumpLeftHanging")


class BehaviorReactToOnCharger(BehaviorPlayAnim):
    """
    ReactToOnCharger behavior - react to being placed on the charger, then go to sleep.

    Unlike the other reactions, this one is not over when its animation is: the configuration
    gives the delay before the robot falls asleep on the charger and the delay before it lets
    the connection go. The behavior ends early if the robot is taken off the charger.
    """

    default_anim_triggers = ("PlacedOnCharger", )
    #: Animations played when the sleep delay expires.
    sleep_anim_triggers = ("GoToSleepGetIn", "GoToSleepSleeping")

    def __init__(self, cli: client.Client, conf: Any):
        super().__init__(cli, conf)
        self.time_til_sleep_animation = float(conf.get("timeTilSleepAnimation_s", 300.0))
        self.time_til_disconnection = float(conf.get("timeTilDisconnection_s", 330.0))
        self.asleep = False
        self.timer: Optional[threading.Timer] = None
        self.add_handler(event.EvtRobotOnChargerChange, self._on_robot_on_charger_change)

    def activate(self) -> None:
        self.asleep = False
        super().activate()

    def on_sequence_completed(self) -> None:
        if not self.asleep:
            # Wait on the charger, then fall asleep.
            self._start_timer(self.time_til_sleep_animation, self._fall_asleep)
        else:
            # Asleep: wait out the rest of the disconnection delay.
            delay = max(self.time_til_disconnection - self.time_til_sleep_animation, 0.0)
            self._start_timer(delay, self._disconnection_delay_expired)

    def _start_timer(self, delay: float, f: Any) -> None:
        self._cancel_timer()
        self.timer = threading.Timer(delay, f)
        self.timer.daemon = True
        self.timer.start()

    def _cancel_timer(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def _fall_asleep(self) -> None:
        self.asleep = True
        groups = self.cli.animation_groups or {}
        self.sequence = [trigger for trigger in self.sleep_anim_triggers if trigger in groups]
        self.current_trigger = 0
        if not self.sequence:
            self.on_sequence_completed()
            return
        self._play_next()

    def _disconnection_delay_expired(self) -> None:
        # The robot would drop the connection here to save power. pycozmo keeps it, since
        # dropping it is the application's call, not a behavior's.
        logger.info("Behavior '{}' reached its disconnection delay.".format(self.get_id()))
        self.done()

    def _on_robot_on_charger_change(self, cli: client.Client, state: bool) -> None:
        if not state:
            self._cancel_timer()
            self.done()

    def deactivate(self) -> None:
        self._cancel_timer()
        super().deactivate()


class BehaviorDriveOffCharger(Behavior):
    """
    DriveOffCharger behavior - get off the charger.

    The robot backs onto its charger, so leaving it means driving forward: off the contacts, then
    the extra distance the configuration asks for. The distance is timed rather than measured, the
    robot reporting no odometry a behavior could wait on.
    """

    #: Speed the robot drives off at, in mm/s.
    SPEED = 50.0
    #: Distance covered before the extra distance the configuration asks for, in mm. That is about
    #: how far back the charger holds the treads from its lip.
    CONTACTS_DISTANCE = 40.0
    #: How long the behavior leaves the robot's status to catch up before trying again, in seconds.
    #: Without it, a status not yet showing the charger clear would have the robot drive off twice.
    SETTLE_TIME = 1.0
    #: How many times in a row the robot will try. The resources say nothing about retrying; this
    #: is here so that a status stuck on the charger cannot drive the robot across the table.
    MAX_ATTEMPTS = 3

    def __init__(self, cli: client.Client, conf: Any):
        super().__init__(cli, conf)
        self.extra_distance = float(conf.get("extraDistanceToDrive_mm", 60.0))
        self.attempts = 0
        self.last_run = 0.0
        self.timer: Optional[threading.Timer] = None

    def wants_to_run(self) -> bool:
        if not self.cli.robot_status & robot.RobotStatusFlag.IS_ON_CHARGER:
            # Off the charger, so whatever it took to get there is behind us.
            self.attempts = 0
            return False
        if self.attempts >= self.MAX_ATTEMPTS:
            return False
        return time.perf_counter() - self.last_run >= self.SETTLE_TIME

    def activate(self) -> None:
        # TODO: Play a wake up animation. The resources name none for this behavior.
        # Getting going under its own steam makes the robot more confident.
        self.attempts += 1
        self.last_run = time.perf_counter()
        self.post_emotion_event("DriveOffCharger")
        self.cli.drive_wheels(self.SPEED, self.SPEED)
        self.timer = threading.Timer(
            (self.CONTACTS_DISTANCE + self.extra_distance) / self.SPEED, self._arrived)
        self.timer.daemon = True
        self.timer.start()

    def _arrived(self) -> None:
        self.cli.stop_all_motors()
        if self.attempts >= self.MAX_ATTEMPTS:
            logger.warning(
                "Behavior '{}' has driven off the charger {} times and the robot still reads as on "
                "it. Leaving it there.".format(self.get_id(), self.attempts))
        self.done()

    def deactivate(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        self.cli.stop_all_motors()


def get_behavior_class_from_dict(data):
    """ Choose a behavior class, based on the behaviorClass JSON attribute. """
    # TODO: Replace with a behavior package.
    class_map = {
        "PlayAnim": BehaviorPlayAnim,
        "PlayArbitraryAnim": BehaviorPlayArbitraryAnim,
        "AcknowledgeFace": BehaviorAcknowledge,
        "AcknowledgeObject": BehaviorAcknowledge,
        "DriveOffCharger": BehaviorDriveOffCharger,
        "FistBump": BehaviorFistBump,
        "RamIntoBlock": BehaviorRamIntoBlock,
        "ReactToCliff": BehaviorReactToCliff,
        "ReactToCubeMoved": BehaviorReactToCubeMoved,
        "ReactToFrustration": BehaviorReactToFrustration,
        "ReactToImpact": BehaviorReactToImpact,
        "ReactToOnCharger": BehaviorReactToOnCharger,
        "ReactToPet": BehaviorReactToPet,
        "ReactToPickup": BehaviorReactToPickup,
        "ReactToRobotOnBack": BehaviorReactToRobotOnBack,
        "ReactToRobotOnFace": BehaviorReactToRobotOnFace,
        "ReactToRobotOnSide": BehaviorReactToRobotOnSide,
        "ReactToRobotShaken": BehaviorReactToRobotShaken,
        "ReactToSparked": BehaviorReactToSparked,
        "ReactToUnexpectedMovement": BehaviorReactToUnexpectedMovement,
        # Not implemented, for lack of an animation in AnimationTriggerMap.json:
        # ReactToMotorCalibration, ReactToPlacedOnSlope, ReactToReturnedToTreads.
    }
    cls = class_map.get(data["behaviorClass"], Behavior)
    return cls


def load_behaviors(resource_dir: str, cli: client.Client) -> Dict[str, Behavior]:

    start_time = time.perf_counter()

    behavior_files = get_json_files(
        resource_dir, [os.path.join('cozmo_resources', 'config', 'engine', 'behaviorSystem', 'behaviors')])
    behaviors = {}
    for filename in behavior_files:
        data = load_json_file(filename)
        cls = get_behavior_class_from_dict(data)
        behaviors[data['behaviorID']] = cls(cli, data)

    logger.debug("Loaded {} behaviors in {:.02f} s.".format(len(behaviors), time.perf_counter() - start_time))

    return behaviors


def load_reaction_trigger_behavior_map(resource_dir: str) -> Dict[str, List[ReactionTrigger]]:
    """
    Load the reaction trigger behavior map.

    A trigger can name more than one behavior, to be chosen between when it fires. Frustration names
    two, a minor and a major variant, each declaring the confidence at or below which it applies.
    Keeping one behavior per trigger silently dropped all but the last, so the map holds a list.
    """

    start_time = time.perf_counter()

    reaction_trigger_behavior_map: Dict[str, List[ReactionTrigger]] = {}
    filename = os.path.join(resource_dir, 'cozmo_resources', 'config',
                            'engine', 'behaviorSystem', 'reactionTrigger_behavior_map.json')

    json_data = load_json_file(filename)
    for trigger in json_data['reactionTriggerBehaviorMap']:
        reaction = ReactionTrigger.from_json(trigger)
        reaction_trigger_behavior_map.setdefault(reaction.name, []).append(reaction)

    logger.debug("Loaded {} entry reaction trigger behavior map in {:.02f} s.".format(
        sum(len(r) for r in reaction_trigger_behavior_map.values()), time.perf_counter() - start_time))

    return reaction_trigger_behavior_map
