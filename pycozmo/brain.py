"""

Brain class - high level behavior and emotion engine.

"""

from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from PIL import Image
from threading import Event, RLock, Thread
from typing import Optional as _Optional
from queue import Queue, Empty
import random
import time

from .logger import logger, logger_reaction, logger_behavior, logger_emotion
from . import client
from . import event
from . import emotions
from . import needs
from . import behavior
from . import cube_behaviors
from . import game_behaviors
from . import activity
from . import motion_detection
from . import marker_detection
from . import cube_lights
from . import camera
from . import cubes
from . import protocol_encoder
from . import util
from . import robot
from . import unlocks


__all__ = [
    "Brain"
]


class EventRelay:
    """ A child dispatcher of the client that hands what the client dispatches to a brain. See Brain.relay() . """

    def __init__(self, brain: "Brain") -> None:
        self.brain = brain

    def dispatch(self, evt: type, *args: Any, **kwargs: Any) -> None:
        self.brain.relay(evt, *args, **kwargs)


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

    #: Animation trigger the robot wakes up with when the brain starts, as it did when the Cozmo application
    #: connected to it. Five animations answer it, anim_launch_wakeup_01 to 05.
    WAKE_UP_TRIGGER = "ConnectWakeUp"
    #: Longest the brain waits for the wake up to finish before getting on with things regardless.
    WAKE_UP_TIMEOUT = 15.0

    #: How long the brain waits before looking for something to do again, once it has found
    #: nothing. Every activity is consulted each time, so this is not free.
    IDLE_RETRY_TIME = 1.0

    #: How often the camera images are searched for cube markers, at most, in seconds: it takes about 20 ms an
    #: image, where motion detection takes 2.4.
    MARKER_INTERVAL = 0.2
    #: How often they are searched for faces, at most, in seconds: 4.6 ms an image, and 15 ms more for each face
    #: told afresh. Nothing is found without OpenCV: see pycozmo.faces.
    FACE_INTERVAL = 0.2
    #: How recently a cube must have been seen for its moving to be something the robot saw.
    CUBE_IN_VIEW_TIME = 2.0

    #: Need action applied for each orientation the robot can end up in. Only being laid on its
    #: side is worth anything in Anki's table; landing on its back or face is not.
    ORIENTATION_NEED_ACTIONS = {
        robot.RobotOrientation.ON_LEFT_SIDE: "PlacedOnSide",
        robot.RobotOrientation.ON_RIGHT_SIDE: "PlacedOnSide",
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
        # The needs are loaded before the behaviors, which are handed them: six of them ask about
        # them, and they are what makes a robot left alone start asking to be played with.
        self.needs = needs.load_needs(resource_dir)
        self.behaviors = behavior.load_behaviors(resource_dir, self.cli, self.needs)
        # What the robot has earned the right to do: everything, since nothing here keeps a progression. See
        # pycozmo.unlocks .
        self.unlocks = unlocks.load_all_unlocks(resource_dir)
        for script in self.behaviors.values():
            if isinstance(script, cube_behaviors.BehaviorScript):
                script.get_mood = self.get_mood
        for candidate in self.activities.values():
            if candidate.strategy.type == "PlayWithHumans":
                candidate.strategy.can_request_game = self.can_request_game
        self.reaction_trigger_behavior_map = behavior.load_reaction_trigger_behavior_map(resource_dir)
        self.emotion_types = emotions.load_emotion_types(resource_dir)
        self.emotion_events = emotions.load_emotion_events(resource_dir)
        # Until the robot's own calibration is read, in start(), a typical one places motion on the ground.
        self.motion_detector = motion_detection.MotionDetector(
            motion_detection.load_motion_detector_config(resource_dir), camera.DEFAULT_CALIBRATION)
        self.cli.load_anims()
        self.cli.cubes.light_animations = cube_lights.load_cube_light_animations(resource_dir)
        logger.info("Loaded resources in {:.02f} s.".format(time.perf_counter() - start_time))

        # The brain's handlers, and the behavior running, which it makes a child of this. They get the client's
        # events on the brain's own thread, through the relay. See relay() .
        self.dispatcher = event.Dispatcher()
        self.event_queue: Queue = Queue()
        self.event_relay = EventRelay(self)
        self.cli.add_child_dispatcher(self.event_relay)
        self.listen(event.EvtBehaviorDone, self.on_behavior_done)
        self.listen(event.EvtEmotionEvent, self.on_emotion_event)
        self.listen(event.EvtCliffDetectedChange, self.on_cliff_detected)
        self.listen(event.EvtRobotOrientationChange, self.on_robot_orientation_change)
        self.listen(event.EvtRobotPickedUpChange, self.on_robot_picked_up_change)
        self.listen(event.EvtRobotFallingChange, self.on_robot_falling_change)
        self.listen(event.EvtRobotOnChargerChange, self.on_robot_on_charger_change)
        self.listen(event.EvtNewRawCameraImage, self.on_camera_image)
        self.listen(event.EvtCubeMovingChange, self.on_cube_moving_change)
        self.listen(event.EvtCubeObserved, self.on_cube_observed)
        self.listen(event.EvtFaceAppeared, self.on_face_appeared)
        self.listen(event.EvtGameRequestAnswered, self.on_game_request_answered)
        # TODO: ...

        # When the camera images were last searched for cube markers, and for faces. See on_camera_image() .
        self.markers_time = 0.0
        self.faces_time = 0.0
        # The cubes seen since they last moved: seeing any other is news. See on_cube_observed() .
        self.acknowledged_cubes: Set[protocol_encoder.ObjectType] = set()

        # Reaction trigger queue
        self.reaction_queue: Queue = Queue()

        self.stop_flag = False
        self.event_thread: _Optional[Thread] = \
            Thread(daemon=True, name="BrainEventThread", target=self.event_thread_run)
        self.reaction_thread: _Optional[Thread] = \
            Thread(daemon=True, name="ReactionThread", target=self.reaction_thread_run)
        self.heartbeat_thread: _Optional[Thread] = \
            Thread(daemon=True, name="HeartbeatThread", target=self.heartbeat_thread_run)

        # Current activity, the one that holds the others as sub-activities in priority order
        self.activity: Optional[activity.Activity] = self.activities["Freeplay"]
        # Sub-activity of the above that has the robot, if any
        self.sub_activity: Optional[activity.Activity] = None
        # Current behavior
        self.behavior: Optional[behavior.Behavior] = None
        # Behavior a reaction interrupted, to put back once the reaction is over
        self.behavior_to_resume: Optional[behavior.Behavior] = None
        # Three threads activate behaviors: the heartbeat looking for something to do, the reaction
        # thread answering a trigger, and the event thread reporting a behavior done.
        self.behavior_lock = RLock()
        # When the engine may look for something to do again. See update_activity() .
        self.next_choice_time = 0.0
        # When the robot last came to rest on its treads and last drove off its charger. Two
        # behaviors and one activity in the resources ask to run only just after one of those.
        self.on_treads_time: Optional[float] = None
        self.drive_off_charger_time: Optional[float] = None
        # Hiccups left in the bout under way, and when the next one is due. See update_hiccups() .
        self.hiccups_left = 0
        self.next_hiccup_time = 0.0
        self.schedule_hiccup_bout()

    def start(self) -> None:
        # Connect to robot. The threads are created in __init__ and only cleared by stop(), which a Thread
        # cannot be restarted after anyway.
        assert self.event_thread is not None and self.reaction_thread is not None and \
            self.heartbeat_thread is not None
        self.event_thread.start()
        self.reaction_thread.start()
        self.heartbeat_thread.start()

        calibration = self.cli.read_camera_calibration()
        if calibration is not None:
            self.motion_detector.calibration = calibration
        else:
            logger.warning("Could not read the camera calibration. Using a typical one.")
        # Grayscale is all motion detection needs.
        self.cli.enable_camera(True, color=False)
        # One cube of each kind, as the Cozmo application connected them.
        self.cli.cubes.auto_connect = True

        # The robot stops by itself at a cliff, as the Cozmo application had it.
        self.cli.enable_stop_on_cliff(True)
        # TODO: Drive off if on charger.

    def listen(self, evt: type, f: Callable) -> None:
        """ Handle an event from the client, on the brain's thread. """
        self.dispatcher.add_handler(evt, f)

    def relay(self, evt: type, *args: Any, **kwargs: Any) -> None:
        """
        Pass an event the client dispatches on to the brain's handlers and the behavior running.

        The client dispatches on the thread that handles everything the robot sends, so what a handler does
        holds all that up. Behaviors start animations there, which the first time loads and prepares them:
        up to 0.64 s without the robot's state, and without its reports of the animation frames it has played,
        which the frames it is sent wait on. The event goes to the brain's thread instead - or is handled at
        once while the brain is not started, which is how it is driven without a robot.
        """
        if not self.dispatcher.listens_to(evt):
            return
        if self.event_thread is not None and self.event_thread.is_alive():
            self.event_queue.put((evt, args, kwargs))
        else:
            self.dispatcher.dispatch(evt, *args, **kwargs)

    def event_thread_run(self) -> None:
        """ Event thread loop. The client's events, relayed. """
        while not self.stop_flag:
            try:
                evt, args, kwargs = self.event_queue.get(timeout=0.05)
            except Empty:
                continue
            try:
                self.dispatcher.dispatch(evt, *args, **kwargs)
            except Exception as e:
                logger.error("Failed to process event {}. {}".format(evt, e))

    def stop(self) -> None:
        # Disconnect from robot
        self.stop_flag = True
        for thread in (self.heartbeat_thread, self.reaction_thread, self.event_thread):
            if thread is not None and thread.ident is not None:
                thread.join()
        self.heartbeat_thread = self.reaction_thread = self.event_thread = None
        # Stop listening. A reaction posted from here on would queue up for nobody to process, and a
        # behavior reporting itself done would have the brain start another one.
        self.cli.del_child_dispatcher(self.event_relay)
        # Whatever was running keeps its animation playing and its timers armed otherwise.
        with self.behavior_lock:
            self.behavior_to_resume = None
            self.deactivate_behavior()
            self.end_sub_activity()

    def on_behavior_done(self, cli: client.Client) -> None:
        with self.behavior_lock:
            if not self.behavior:
                return
            logger_reaction.info("Done.")
            # A behavior whose name is also a need action is worth what that action is worth. Two
            # behaviors in the resources are: FistBump, worth 0.25 of Play, and PopAWheelie, 0.2.
            self.apply_need_action_if_known(self.behavior.get_id())
            if isinstance(self.behavior, behavior.BehaviorDriveOffCharger):
                self.drive_off_charger_time = time.perf_counter()
            self.deactivate_behavior()
            self.resume_behavior()

    def on_emotion_event(self, cli: client.Client, name: str) -> None:
        self.post_emotion_event(name)

    def on_cliff_detected(self, cli: client.Client, state: bool) -> None:
        if state and not cli.robot_picked_up and cli.robot_moving:
            self.post_reaction("CliffDetected")

    def on_robot_orientation_change(self, cli: client.Client, orientation: robot.RobotOrientation) -> None:
        if orientation == robot.RobotOrientation.ON_THREADS:
            self.on_treads_time = time.perf_counter()
        if isinstance(self.behavior, cube_behaviors.BehaviorPopAWheelie):
            # On its back of its own doing: nothing to react to.
            return
        action = self.ORIENTATION_NEED_ACTIONS.get(orientation)
        if action:
            self.apply_need_action(action)
        reaction = self.ORIENTATION_REACTIONS.get(orientation)
        if reaction:
            self.post_reaction(reaction)

    def on_robot_picked_up_change(self, cli: client.Client, state: bool) -> None:
        if state:
            self.post_reaction("RobotPickedUp")

    def on_robot_falling_change(self, cli: client.Client, state: bool) -> None:
        if state:
            # A fall costs the most of any accident in Anki's table: 0.15 of Repair and 0.1 of Play.
            self.apply_need_action("Fall")
            self.post_reaction("RobotFalling")

    def on_robot_on_charger_change(self, cli: client.Client, state: bool) -> None:
        if state:
            self.post_reaction("PlacedOnCharger")

    def on_camera_image(self, cli: client.Client, new_im: Image.Image) -> None:
        """ Process images, coming from the robot camera. """
        pose = cli.pose
        now = time.perf_counter()
        moving = bool(cli.robot_status & robot.RobotStatusFlag.IS_MOVING) or cli.robot_picked_up
        motion = self.motion_detector.process(
            new_im, now,
            pose=(pose.position.x, pose.position.y, pose.rotation.angle_z.radians, cli.head_angle.radians),
            moving=moving,
            timestamp=cli.last_image_timestamp,
            pitch=cli.pose_pitch.radians)
        if motion is not None and motion.any:
            cli.dispatch(event.EvtMotionObserved, cli, motion)
        # Markers are placed by the robot's pose, which a moving robot's images lag behind.
        if not moving and now - self.markers_time >= self.MARKER_INTERVAL:
            self.markers_time = now
            for marker in marker_detection.observe_markers(new_im, self.motion_detector.calibration,
                                                           cli.head_angle.radians, cli.pose_pitch.radians):
                if marker.cube is not None:
                    cli.cubes.observe(marker.cube, marker.position, marker.normal, now)
        # Faces too, for the same reason.
        if not moving and now - self.faces_time >= self.FACE_INTERVAL:
            self.faces_time = now
            cli.faces.process(new_im, self.motion_detector.calibration, cli.head_angle.radians,
                              cli.pose_pitch.radians, now)
        # TODO: See cozmo_resources/config/engine/vision_config.json
        # TODO: pet detection
        # self.process_reaction_trigger("PetInitialDetection")
        # TODO: laser detection
        # TODO: facial expression estimation
        # TODO: smile amount detection
        # TODO: blink amount detection
        # TODO: gaze detection?
        # TODO: image quality check
        pass

    def on_cube_moving_change(self, cli: client.Client, cube: cubes.LightCube, moving: bool) -> None:
        if not moving or cube.in_use or cube is cli.cubes.carried:
            # The robot's own doing, or a game's, is no news.
            return
        if isinstance(self.behavior, cube_behaviors.BehaviorScript):
            # Nor is a cube the robot knocked, handling another: the reaction would cut the handling short.
            return
        # Where it is now is news again.
        self.acknowledged_cubes.discard(cube.object_type)
        if cube.seen_within(self.CUBE_IN_VIEW_TIME) and not cli.robot_picked_up:
            self.post_reaction("CubeMoved")

    def on_cube_observed(self, cli: client.Client, cube: cubes.LightCube) -> None:
        # A cube seen for the first time, or where it was moved to, is acknowledged, as Anki's engine did.
        if cube.object_type not in self.acknowledged_cubes:
            self.acknowledged_cubes.add(cube.object_type)
            self.post_reaction("ObjectPositionUpdated")

    def on_face_appeared(self, cli: client.Client, face: Any) -> None:
        # A face seen where there was none is acknowledged, as Anki's engine did, unless the robot is at a game or
        # handling a cube: the reaction would cut it short.
        if isinstance(self.behavior, cube_behaviors.BehaviorScript) or cli.robot_picked_up:
            return
        self.post_reaction("FacePositionUpdated")

    def can_request_game(self) -> bool:
        """ Whether the robot could ask the player for a game now: PlayWithHumans waits for it. """
        now = time.perf_counter()
        return any(isinstance(candidate, game_behaviors.BehaviorRequestGameSimple) and
                   self.can_run_behavior(behavior_id, now, now) for behavior_id, candidate in self.behaviors.items())

    def on_game_request_answered(self, cli: client.Client, accepted: bool) -> None:
        # The more often the player says no, the longer the robot waits to ask again.
        for candidate in self.activities.values():
            if candidate.strategy.type == "PlayWithHumans":
                candidate.strategy.answered(accepted)

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
        with self.behavior_lock:
            self._activate_behavior(behavior_id, resume_last)

    def _activate_behavior(self, behavior_id: str, resume_last: bool = False) -> None:
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
        self.cli.activate_behavior(new_behavior, self.dispatcher)

    def resume_behavior(self) -> None:
        """ Put back the behavior a reaction interrupted, if there is one. """
        resumed, self.behavior_to_resume = self.behavior_to_resume, None
        if resumed is None:
            return
        # A behavior has no notion of being suspended, so a resumed one starts over.
        logger_behavior.info("Resuming {}".format(resumed.get_id()))
        self.behavior = resumed
        self.cli.activate_behavior(resumed, self.dispatcher)

    def deactivate_behavior(self) -> None:
        if self.behavior:
            logger_behavior.info("Deactivating {}".format(self.behavior.get_id()))
            self.cli.deactivate_behavior(self.behavior, self.dispatcher)
            self.behavior = None

    def update_activity(self, now: Optional[float] = None) -> None:
        """
        Keep something running while no reaction is.

        The current activity - Freeplay, unless the application says otherwise - lists
        sub-activities in priority order, and the first one that wants to run and has a behavior to
        offer gets the robot. A behavior runs to completion: the engine only looks again once
        nothing is running, so an animation is never cut short by anything but a reaction.
        """
        now = time.perf_counter() if now is None else now
        with self.behavior_lock:
            if self.behavior is not None or self.behavior_to_resume is not None:
                return
            if now < self.next_choice_time:
                return
            if self.sub_activity is not None and self.sub_activity.should_end(now):
                self.end_sub_activity(now)
            chosen = self.choose_activity(now)
            if chosen is None:
                # Nothing to do, which is the ordinary state of a robot with no cube to play with
                # and nobody in sight: most of what it could do needs one or the other.
                self.next_choice_time = now + self.IDLE_RETRY_TIME
                return
            chosen_activity, behavior_id = chosen
            if chosen_activity is not self.sub_activity:
                self.end_sub_activity(now)
                self.start_sub_activity(chosen_activity, now)
            self._activate_behavior(behavior_id)
            chosen_activity.ran(behavior_id, now)

    def get_candidate_activities(self) -> List[activity.Activity]:
        """ The sub-activities to consider, best first, or the activity itself if it has none. """
        current = self.activity
        if current is None:
            return []
        if not current.sub_activities:
            return [current]
        candidates = []
        # The lower priority number comes first: the sparks the application asks for, then the
        # severe needs, then the freeplay activities from the most engaging to the least.
        for entry in sorted(current.sub_activities, key=lambda e: e.get("activityPriority", 0)):
            candidate = self.activities.get(entry["activityID"])
            if candidate is None:
                logger_behavior.error("Activity {} names unknown sub-activity {}.".format(
                    current.id, entry["activityID"]))
                continue
            candidates.append(candidate)
        return candidates

    def choose_activity(self, now: Optional[float] = None) -> Optional[Tuple[activity.Activity, str]]:
        """
        The best sub-activity that has something to run, and what it would run.

        An activity that wants the robot but can offer no behavior is passed over rather than
        entered. Most of what the resources give it needs a cube, a face or a player, none of which
        this library sees, and entering it would leave the robot doing nothing for as long as its
        should-end duration - twenty-five seconds for PlayAlone, a minute for Hiking.
        """
        now = time.perf_counter() if now is None else now
        mood = self.get_mood()
        for candidate in self.get_candidate_activities():
            if not candidate.wants_to_run(mood, now=now, on_treads_time=self.on_treads_time,
                                          robot_needs=self.needs):
                continue
            # A behavior that asks to run just after the switch to its activity gets its chance:
            # the activity is about to start unless it is the one already running.
            start_time = now
            if candidate is self.sub_activity and candidate.start_time is not None:
                start_time = candidate.start_time
            behavior_id = candidate.choose(
                lambda behavior_id: self.can_run_behavior(behavior_id, start_time, now), now)
            if behavior_id is None:
                logger_behavior.debug("Activity {} has nothing to run.".format(candidate.id))
                continue
            return candidate, behavior_id
        return None

    def can_run_behavior(self, behavior_id: str, activity_start_time: float, now: float) -> bool:
        """
        Whether the engine may offer a behavior the robot now.

        On top of the behavior's own answer, a behavior can need an unlock the robot has not earned,
        and two behaviors in the resources ask for something to have just happened: the hiking intro
        wants to run within a quarter of a second of its activity being entered, the hiking wake-up
        within a second of driving off the charger.
        """
        candidate = self.behaviors.get(behavior_id)
        if candidate is None:
            logger_behavior.error("Failed to find behavior {}.".format(behavior_id))
            return False
        required_unlock = candidate.conf.get("requiredUnlockId")
        if required_unlock is not None and required_unlock not in self.unlocks:
            return False
        if not candidate.wants_to_run():
            return False
        recent_switch = candidate.conf.get("requiredRecentSwitchToParent_sec")
        if recent_switch is not None and now - activity_start_time > float(recent_switch):
            return False
        recent_drive_off = candidate.conf.get("requiredRecentDriveOffCharger_sec")
        if recent_drive_off is not None and \
                (self.drive_off_charger_time is None or
                 now - self.drive_off_charger_time > float(recent_drive_off)):
            return False
        return True

    def start_sub_activity(self, new_activity: activity.Activity, now: Optional[float] = None) -> None:
        """ Give an activity the robot. """
        now = time.perf_counter() if now is None else now
        logger_behavior.info("Starting activity {}".format(new_activity.id))
        self.sub_activity = new_activity
        new_activity.started(now, self.needs)

    def end_sub_activity(self, now: Optional[float] = None) -> None:
        """ Take the robot back from the activity that has it, and put that one on cooldown. """
        if self.sub_activity is None:
            return
        logger_behavior.info("Ending activity {}".format(self.sub_activity.id))
        self.sub_activity.ended(now, self.needs)
        self.sub_activity = None

    def wake_up(self, timeout: Optional[float] = None) -> None:
        """
        Play the wake up animation and wait for it to finish, or for the brain to be stopped.

        A reaction can still interrupt it: it is the robot's first moment, not a blindfold. The wake up
        ends with the head up, where the heartbeat used to raise it.
        """
        if self.WAKE_UP_TRIGGER not in getattr(self.cli, "animation_groups", {}):
            logger.warning("No {} animation group. Not waking up.".format(self.WAKE_UP_TRIGGER))
            return
        done = Event()
        handler = self.cli.add_handler(event.EvtAnimationCompleted, lambda cli: done.set(), one_shot=True)
        try:
            self.cli.play_anim_group(self.WAKE_UP_TRIGGER)
            deadline = time.perf_counter() + (self.WAKE_UP_TIMEOUT if timeout is None else timeout)
            while not done.wait(0.05) and not self.stop_flag and time.perf_counter() < deadline:
                pass
        finally:
            if not done.is_set():
                self.cli.del_handler(event.EvtAnimationCompleted, handler)

    def heartbeat_thread_run(self) -> None:
        """ Heartbeat thread loop. """

        # Nothing is chosen for the robot to do until it has woken up.
        self.wake_up()

        timer = util.FPSTimer(robot.FRAME_RATE)
        while not self.stop_flag:

            self.update_emotion_types()
            self.update_needs()
            self.update_hiccups()
            self.update_activity()
            self.cli.cubes.update()
            # TODO: Timers

            timer.sleep()

    def update_emotion_types(self) -> None:
        """ Update emotion types from their decay functions. """
        for emotion_type in self.emotion_types.values():
            emotion_type.update()

    def update_needs(self, now: Optional[float] = None) -> None:
        """
        Let the nurture needs fall.

        They move once a minute, not once a frame: the rates are quoted per minute against a decay
        period of one, and the needs themselves take hours to go anywhere. Play reaches its warning
        bracket after about 55 minutes of a robot left to itself, and its critical one after 83;
        Energy after 99 and 204; Repair after ten hours and nineteen.
        """
        self.needs.update(now)

    def apply_need_action(self, action_id: str, now: Optional[float] = None) -> bool:
        """
        Apply what one action is worth to the needs, and say whether it counted.

        This is how a need goes back up. Nothing in this library does it on its own for the actions
        that matter most - "Feed" is worth a third of Energy, "RepairHead", "RepairLift" and
        "RepairTreads" a third of Repair each - because on a real robot they were a thing the player
        did in the application. An application built on PyCozmo has to offer them the same way.
        """
        return self.needs.apply_action(action_id, now)

    def apply_need_action_if_known(self, action_id: str, now: Optional[float] = None) -> bool:
        """ Apply an action if the configuration names one, quietly doing nothing if it does not. """
        if action_id not in self.needs.actions:
            return False
        return self.needs.apply_action(action_id, now)

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
