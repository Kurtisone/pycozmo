"""

Brain class - high level behavior and emotion engine.

"""

from typing import Dict, Optional
from PIL import Image
from threading import Thread
from typing import Optional as _Optional
from queue import Queue, Empty
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
        self.reaction_trigger_beahvior_map = behavior.load_reaction_trigger_behavior_map(resource_dir)
        self.emotion_types = emotions.load_emotion_types(resource_dir)
        self.emotion_events = emotions.load_emotion_events(resource_dir)
        self.cli.load_anims()
        logger.info("Loaded resources in {:.02f} s.".format(time.perf_counter() - start_time))

        self.cli.add_handler(event.EvtBehaviorDone, self.on_behavior_done)
        self.cli.add_handler(event.EvtEmotionEvent, self.on_emotion_event)
        self.cli.add_handler(event.EvtCliffDetectedChange, self.on_cliff_detected)
        self.cli.add_handler(event.EvtRobotOrientationChange, self.on_robot_orientation_change)
        self.cli.add_handler(event.EvtRobotPickedUpChange, self.on_robot_picked_up_change)
        self.cli.add_handler(event.EvtRobotFallingChange, self.on_robot_falling_change)
        self.cli.add_handler(event.EvtRobotOnChargerChange, self.on_robot_on_charger_change)
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

    def start(self) -> None:
        # Connect to robot. Both threads are created in __init__ and only cleared by stop(), which a Thread
        # cannot be restarted after anyway.
        assert self.reaction_thread is not None and self.heartbeat_thread is not None
        self.reaction_thread.start()
        self.heartbeat_thread.start()

        # TODO: Enable stop on cliff.
        # TODO: Enable camera.
        # TODO: Drive off if on charger.

    def stop(self) -> None:
        # Disconnect from robot
        self.stop_flag = True
        if self.heartbeat_thread:
            self.heartbeat_thread.join()
            self.heartbeat_thread = None
        if self.reaction_thread:
            self.reaction_thread.join()
            self.reaction_thread = None

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
        reaction = self.reaction_trigger_beahvior_map.get(reaction_trigger)
        if reaction:
            # Some triggers have an emotion event of the same name - CliffDetected costs the robot
            # some Happy, Calm and Brave. The behavior a reaction runs may post one of its own.
            self.post_emotion_event(reaction_trigger)
            self.activate_behavior(reaction.behavior_id, resume_last=reaction.should_resume_last)
        else:
            logger_reaction.error("Failed to find reaction for {}.".format(reaction_trigger))

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

        cnt = 1
        timer = util.FPSTimer(robot.FRAME_RATE)
        while not self.stop_flag:

            self.update_emotion_types()
            # TODO: Timers

            if cnt % (30 * 60) == 0:
                self.post_reaction("Hiccup")

            cnt += 1
            timer.sleep()

    def update_emotion_types(self) -> None:
        """ Update emotion types from their decay functions. """
        for emotion_type in self.emotion_types.values():
            emotion_type.update()
