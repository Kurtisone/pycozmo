"""

Brain class - high level behavior and emotion engine.

"""

from typing import Callable, Dict, List, Optional, Tuple
from PIL import Image
from threading import Thread
from typing import Optional as _Optional
from queue import Queue, Empty
import random
import time

from .logger import logger, logger_reaction, logger_behavior, logger_emotion
from . import client
from . import event
from . import emotions
from . import behavior
from . import activity
from . import util
from . import robot


__all__ = [
    "Brain"
]


class Brain:
    """ Cozmo robot brain class. """

    #: Hiccup parameters, should the resources not carry them.
    HICCUP_DEFAULTS: Dict[str, float] = {
        "minHiccupOccurrenceFrequency_s": 300.0,
        "maxHiccupOccurrenceFrequency_s": 3300.0,
        "minNumberOfHiccupsToDo": 5,
        "maxNumberOfHiccupsToDo": 10,
        "minHiccupSpacing_ms": 4500.0,
        "maxHiccupSpacing_ms": 8000.0,
    }

    #: Reaction posted for each orientation the robot can end up in.
    ORIENTATION_REACTIONS = {
        robot.RobotOrientation.ON_THREADS: "ReturnedToTreads",
        robot.RobotOrientation.ON_BACK: "RobotOnBack",
        robot.RobotOrientation.ON_FACE: "RobotOnFace",
        robot.RobotOrientation.ON_LEFT_SIDE: "RobotOnSide",
        robot.RobotOrientation.ON_RIGHT_SIDE: "RobotOnSide",
    }

    def __init__(self, cli: client.Client):
        super().__init__()

        self.cli = cli

        # TODO: Load configuration. See cozmo_resources/config/features.json

        util.check_assets()

        start_time = time.perf_counter()
        resource_dir = str(util.get_cozmo_asset_dir())
        self.activities = activity.load_activities(resource_dir)
        self.behaviors = behavior.load_behaviors(resource_dir, self.cli)
        self.reaction_trigger_behavior_map = behavior.load_reaction_trigger_behavior_map(resource_dir)
        self.emotion_types = emotions.load_emotion_types(resource_dir)
        self.emotion_events = emotions.load_emotion_events(resource_dir)
        self.cli.load_anims()
        logger.info("Loaded resources in {:.02f} s.".format(time.perf_counter() - start_time))

        # Kept so that stop() can stop listening.
        self.handlers: List[Tuple[type, event.Handler]] = []
        self.listen(event.EvtBehaviorDone, self.on_behavior_done)
        self.listen(event.EvtEmotionEvent, self.on_emotion_event)
        self.listen(event.EvtCliffDetectedChange, self.on_cliff_detected)
        self.listen(event.EvtRobotOrientationChange, self.on_robot_orientation_change)
        self.listen(event.EvtRobotPickedUpChange, self.on_robot_picked_up_change)
        self.listen(event.EvtRobotFallingChange, self.on_robot_falling_change)
        self.listen(event.EvtRobotOnChargerChange, self.on_robot_on_charger_change)
        # TODO: ...

        # Reaction trigger queue
        self.reaction_queue: Queue = Queue()

        self.stop_flag = False
        self.reaction_thread: _Optional[Thread] = \
            Thread(daemon=True, name="ReactionThread", target=self.reaction_thread_run)
        self.heartbeat_thread: _Optional[Thread] = \
            Thread(daemon=True, name="HeartbeatThread", target=self.heartbeat_thread_run)

        # Current activity
        self.activity = self.activities["Freeplay"]
        # Current behavior
        self.behavior: Optional[behavior.Behavior] = None
        # Behavior a reaction interrupted, to put back once the reaction is over
        self.behavior_to_resume: Optional[behavior.Behavior] = None
        # Hiccups left in the bout under way, and when the next one is due. See update_hiccups() .
        self.hiccups_left = 0
        self.next_hiccup_time = 0.0
        self.schedule_hiccup_bout()

    def start(self) -> None:
        # Connect to robot. Both threads are created in __init__ and only cleared by stop(), which a Thread
        # cannot be restarted after anyway.
        assert self.reaction_thread is not None and self.heartbeat_thread is not None
        self.reaction_thread.start()
        self.heartbeat_thread.start()

        # TODO: Enable stop on cliff.
        # TODO: Enable camera.
        # TODO: Drive off if on charger.

    def listen(self, evt: type, f: Callable) -> None:
        """ Handle an event from the client, and remember it so that stop() can undo it. """
        self.handlers.append((evt, self.cli.add_handler(evt, f)))

    def stop(self) -> None:
        # Disconnect from robot
        self.stop_flag = True
        if self.heartbeat_thread:
            self.heartbeat_thread.join()
            self.heartbeat_thread = None
        if self.reaction_thread:
            self.reaction_thread.join()
            self.reaction_thread = None
        # Stop listening. A reaction posted from here on would queue up for nobody to process, and a
        # behavior reporting itself done would have the brain start another one.
        for evt, handler in self.handlers:
            self.cli.del_handler(evt, handler)
        self.handlers = []
        # Whatever was running keeps its animation playing and its timers armed otherwise.
        self.behavior_to_resume = None
        self.deactivate_behavior()

    def on_behavior_done(self, cli: client.Client) -> None:
        if self.behavior:
            logger_reaction.info("Done.")
            self.deactivate_behavior()
            self.resume_behavior()

    def on_emotion_event(self, cli: client.Client, name: str) -> None:
        self.post_emotion_event(name)

    def on_cliff_detected(self, cli: client.Client, state: bool) -> None:
        if state and not cli.robot_picked_up and cli.robot_moving:
            self.post_reaction("CliffDetected")

    def on_robot_orientation_change(self, cli: client.Client, orientation: robot.RobotOrientation) -> None:
        reaction = self.ORIENTATION_REACTIONS.get(orientation)
        if reaction:
            self.post_reaction(reaction)

    def on_robot_picked_up_change(self, cli: client.Client, state: bool) -> None:
        if state:
            self.post_reaction("RobotPickedUp")

    def on_robot_falling_change(self, cli: client.Client, state: bool) -> None:
        if state:
            self.post_reaction("RobotFalling")

    def on_robot_on_charger_change(self, cli: client.Client, state: bool) -> None:
        if state:
            self.post_reaction("PlacedOnCharger")

    def on_camera_image(self, cli: client.Client, new_im: Image.Image) -> None:
        """ Process images, coming from the robot camera. """
        # TODO: See cozmo_resources/config/engine/vision_config.json
        # TODO: motion detection
        # self.process_reaction_trigger("UnexpectedMovement")
        # TODO: face detection
        # self.process_reaction_trigger("FacePositionUpdate")?
        # TODO: pet detection
        # self.process_reaction_trigger("PetInitialDetection")
        # TODO: laser detection
        # TODO: cube marker detection
        # TODO: facial expression estimation
        # TODO: smile amount detection
        # TODO: blink amount detection
        # TODO: gaze detection?
        # TODO: image quality check
        pass

    def post_reaction(self, reaction_trigger: str) -> None:
        """ Post a reaction trigger to the reaction trigger queue. """
        logger_reaction.debug("Posting {}".format(reaction_trigger))
        self.reaction_queue.put(reaction_trigger)

    def reaction_thread_run(self) -> None:
        """ Reaction thread loop. Reaction trigger queue processing. """
        while not self.stop_flag:
            try:
                reaction_trigger = self.reaction_queue.get(timeout=0.05)
            except Empty:
                continue
            except Exception as e:
                logger.error("Failed to get from reaction trigger queue. {}".format(e))
                continue

            try:
                self.process_reaction(reaction_trigger)
            except Exception as e:
                logger.error("Failed to dispatch reaction trigger '{}'. {}".format(reaction_trigger, e))
                continue

    def process_reaction(self, reaction_trigger: str) -> None:
        logger_reaction.info("Processing {}".format(reaction_trigger))
        reactions = self.reaction_trigger_behavior_map.get(reaction_trigger)
        if not reactions:
            logger_reaction.error("Failed to find reaction for {}.".format(reaction_trigger))
            return
        reaction = self.choose_reaction(reactions)
        if reaction is None:
            logger_reaction.debug("{} is on cooldown.".format(reaction_trigger))
            return
        reaction.ran()
        # Some triggers have an emotion event of the same name - CliffDetected costs the robot
        # some Happy, Calm and Brave. The behavior a reaction runs may post one of its own.
        self.post_emotion_event(reaction_trigger)
        self.activate_behavior(reaction.behavior_id, resume_last=reaction.should_resume_last)

    def choose_reaction(
            self, reactions: List[behavior.ReactionTrigger]) -> Optional[behavior.ReactionTrigger]:
        """
        Choose between the behaviors a reaction trigger names, or nothing if none may run.

        Frustration is the only trigger in the resources that names more than one: a minor and a
        major variant, each declaring the confidence at or below which it applies, -0.6 and -0.9.
        The most severe variant the mood allows wins. When the robot is too confident for any of
        them the mildest runs anyway, a trigger that fired being better answered than ignored.
        """
        ready = [reaction for reaction in reactions if not reaction.is_on_cooldown()]
        if not ready:
            return None
        if len(ready) == 1:
            return ready[0]
        confidence = self.emotion_types["Confident"].value
        # Most severe first, those that grade nothing last.
        graded = sorted(ready, key=lambda r: (r.max_confidence is None, r.max_confidence or 0.0))
        for reaction in graded:
            if reaction.max_confidence is not None and confidence <= reaction.max_confidence:
                return reaction
        return graded[-1]

    def post_emotion_event(self, name: str) -> None:
        """ Apply an emotion event to the mood, by name. Names with no event are ignored. """
        emotion_event = self.emotion_events.get(name)
        if emotion_event is None:
            return
        for emotion_type_name, value in emotion_event.affectors.items():
            emotion_type = self.emotion_types.get(emotion_type_name)
            if emotion_type is None:
                logger.error("Emotion event '{}' affects unknown emotion '{}'.".format(
                    name, emotion_type_name))
                continue
            emotion_type.add(value)
        logger_emotion.info("{}: {}".format(name, self.get_mood_description()))

    def get_mood(self) -> Dict[str, float]:
        """ The current value of every emotion. """
        return {name: emotion_type.value for name, emotion_type in self.emotion_types.items()}

    def get_mood_description(self) -> str:
        """ The emotions that are not at rest, for logging. """
        mood = {name: value for name, value in self.get_mood().items() if round(value, 3)}
        if not mood:
            return "neutral"
        return ", ".join("{} {:+.2f}".format(name, value) for name, value in sorted(mood.items()))

    def activate_behavior(self, behavior_id: str, resume_last: bool = False) -> None:
        new_behavior = self.behaviors.get(behavior_id)
        if new_behavior is None:
            logger_reaction.error("Failed to find behavior {}.".format(behavior_id))
            return
        # A reaction marked shouldResumeLast puts back what it interrupted once it is over: a
        # cliff, a shove or a motor calibration interrupts what the robot was doing rather than
        # ending it. The other eighteen reaction triggers do not resume anything.
        interrupted = self.behavior if resume_last and self.behavior is not new_behavior else None
        self.deactivate_behavior()
        self.behavior_to_resume = interrupted
        logger_behavior.info("Activating {}".format(behavior_id))
        self.behavior = new_behavior
        self.cli.activate_behavior(new_behavior)

    def resume_behavior(self) -> None:
        """ Put back the behavior a reaction interrupted, if there is one. """
        resumed, self.behavior_to_resume = self.behavior_to_resume, None
        if resumed is None:
            return
        # A behavior has no notion of being suspended, so a resumed one starts over.
        logger_behavior.info("Resuming {}".format(resumed.get_id()))
        self.behavior = resumed
        self.cli.activate_behavior(resumed)

    def deactivate_behavior(self) -> None:
        if self.behavior:
            logger_behavior.info("Deactivating {}".format(self.behavior.get_id()))
            self.cli.deactivate_behavior(self.behavior)
            self.behavior = None
            # TODO: Choose behavior from activity?

    def heartbeat_thread_run(self) -> None:
        """ Heartbeat thread loop. """

        # Raise head.
        angle = (robot.MAX_HEAD_ANGLE.radians - robot.MIN_HEAD_ANGLE.radians) / 2.0
        self.cli.set_head_angle(angle)

        timer = util.FPSTimer(robot.FRAME_RATE)
        while not self.stop_flag:

            self.update_emotion_types()
            self.update_hiccups()
            # TODO: Timers

            timer.sleep()

    def update_emotion_types(self) -> None:
        """ Update emotion types from their decay functions. """
        for emotion_type in self.emotion_types.values():
            emotion_type.update()

    def get_hiccup_params(self) -> Dict[str, float]:
        """ Hiccup parameters, from the reaction trigger map, over the defaults kept here. """
        params = dict(self.HICCUP_DEFAULTS)
        for reaction in self.reaction_trigger_behavior_map.get("Hiccup", []):
            params.update(reaction.conf.get("hiccupParams", {}))
        return params

    def schedule_hiccup_bout(self, now: Optional[float] = None) -> None:
        """ Put the next bout of hiccups off to its own time, and decide how long it will be. """
        now = time.perf_counter() if now is None else now
        params = self.get_hiccup_params()
        self.hiccups_left = random.randint(int(params["minNumberOfHiccupsToDo"]),
                                           int(params["maxNumberOfHiccupsToDo"]))
        self.next_hiccup_time = now + random.uniform(
            float(params["minHiccupOccurrenceFrequency_s"]),
            float(params["maxHiccupOccurrenceFrequency_s"]))

    def update_hiccups(self, now: Optional[float] = None) -> None:
        """
        Hiccup in bouts, the way the reaction trigger map asks.

        The robot hiccups five to ten times in a row, four and a half to eight seconds apart, then
        goes five to fifty-five minutes without. It used to hiccup once every sixty seconds, often
        enough to cut into whatever it was doing - a charger sleep sequence, in one measurement.
        """
        now = time.perf_counter() if now is None else now
        if now < self.next_hiccup_time:
            return
        self.post_reaction("Hiccup")
        self.hiccups_left -= 1
        if self.hiccups_left > 0:
            params = self.get_hiccup_params()
            self.next_hiccup_time = now + random.uniform(
                float(params["minHiccupSpacing_ms"]) / 1000.0,
                float(params["maxHiccupSpacing_ms"]) / 1000.0)
        else:
            self.schedule_hiccup_bout(now)
