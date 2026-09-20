import os
import unittest
from typing import Dict, List, Set, cast

import pycozmo


class FakeConnection(pycozmo.event.Dispatcher):
    """ Stands in for Connection, which is what behaviors post EvtBehaviorDone to. """

    def __init__(self):
        super().__init__()
        self.events = []

    def post_event(self, evt, *args, **kwargs):
        self.events.append((evt, args))


class FakeClient(pycozmo.event.Dispatcher):
    """ Stands in for Client, recording the animations a behavior asks for. """

    #: Animation groups the fake knows about, which is every trigger the behaviors under test use.
    ANIMATION_GROUPS = (
        "AcknowledgeObject",
        "CubeMovedSense",
        "DizzyShakeStop",
        "FacePlantRoll",
        "FistBumpLeftHanging",
        "FistBumpRequestOnce",
        "FlipDownFromBack",
        "FrustratedByFailure",
        "GoToSleepGetIn",
        "GoToSleepSleeping",
        "PetDetectionShort",
        "PlacedOnCharger",
        "ReactToCliff",
        "ReactToImpact",
        "ReactToNewBlockSmall",
        "ReactToOnLeftSide",
        "ReactToOnRightSide",
        "ReactToPickup",
        "ReactToUnexpectedMovement",
        "SoundOnlyRamIntoBlock",
        "SparkGetIn",
    )

    def __init__(self):
        super().__init__()
        self.conn = FakeConnection()
        self.animation_groups = {name: None for name in self.ANIMATION_GROUPS}
        self.robot_orientation = pycozmo.robot.RobotOrientation.ON_THREADS
        self.robot_status = 0
        self.played = []
        self.cancelled = 0
        self.wheel_speeds = []
        self.stopped = 0

    def play_anim_group(self, name):
        self.played.append(name)

    def cancel_anim(self):
        self.cancelled += 1

    def drive_wheels(self, lwheel_speed, rwheel_speed, *args, **kwargs):
        self.wheel_speeds.append((lwheel_speed, rwheel_speed))

    def stop_all_motors(self):
        self.stopped += 1


class BehaviorTestCase(unittest.TestCase):

    def setUp(self):
        self.cli = FakeClient()

    def make(self, behavior_class, conf=None, behavior_id="TestBehavior"):
        conf = dict(conf or {})
        conf.setdefault("behaviorID", behavior_id)
        behavior = behavior_class(self.cli, conf)
        # Behaviors receive events as a child dispatcher of the client, as activate_behavior does.
        self.cli.add_child_dispatcher(behavior)
        self.addCleanup(self.cli.del_child_dispatcher, behavior)
        return behavior

    def complete_animation(self):
        """ Report the animation the client is playing as finished, the way the controller does. """
        self.cli.dispatch(pycozmo.event.EvtAnimationCompleted, self.cli)

    def posted(self, evt):
        """ The arguments of every posting of an event. """
        return [args for posted, args in self.cli.conn.events if posted is evt]

    def posted_emotion_events(self):
        """ The name of every emotion event the behavior posted. """
        return [args[1] for args in self.posted(pycozmo.event.EvtEmotionEvent)]

    def assertDone(self):
        self.assertTrue(self.posted(pycozmo.event.EvtBehaviorDone))

    def assertNotDone(self):
        self.assertFalse(self.posted(pycozmo.event.EvtBehaviorDone))


class TestBehavior(BehaviorTestCase):

    def test_unimplemented_behavior_completes_immediately(self):
        behavior = self.make(pycozmo.behavior.Behavior)
        behavior.activate()
        self.assertEqual(self.cli.played, [])
        self.assertDone()

    def test_a_behavior_taken_off_the_robot_cannot_report_itself_done(self):
        # A behavior waiting on a timer or an animation can finish just after something else has
        # taken the robot - a reaction, usually. Reporting itself done then would end the reaction
        # instead of itself.
        behavior = self.make(pycozmo.behavior.BehaviorPlayAnim, {"animTriggers": ["ReactToCliff"]})
        behavior.activate()
        behavior.deactivated = True
        self.complete_animation()
        self.assertNotDone()

    def test_giving_up_is_silenced_the_same_way(self):
        behavior = self.make(pycozmo.behavior.Behavior)
        behavior.deactivated = True
        behavior.activate()
        self.assertNotDone()

    def test_the_client_is_what_marks_a_behavior_off_the_robot(self):
        behavior = self.make(pycozmo.behavior.BehaviorPlayAnim, {"animTriggers": ["ReactToCliff"]})
        cli = cast(pycozmo.client.Client, self.cli)
        self.assertFalse(behavior.deactivated)
        pycozmo.client.Client.deactivate_behavior(cli, behavior)
        self.assertTrue(behavior.deactivated)
        pycozmo.client.Client.activate_behavior(cli, behavior)
        self.assertFalse(behavior.deactivated, "activating it again puts it back on the robot")


class TestBehaviorPlayAnim(BehaviorTestCase):

    def test_plays_the_configured_trigger(self):
        behavior = self.make(pycozmo.behavior.BehaviorPlayAnim, {"animTriggers": ["ReactToCliff"]})
        behavior.activate()
        self.assertEqual(self.cli.played, ["ReactToCliff"])
        self.assertNotDone()
        self.complete_animation()
        self.assertDone()

    def test_plays_the_whole_sequence(self):
        # Only the first animation of a sequence used to be played, the behavior reporting itself
        # done as soon as it had finished.
        behavior = self.make(
            pycozmo.behavior.BehaviorPlayAnim, {"animTriggers": ["ReactToCliff", "ReactToPickup"]})
        behavior.activate()
        self.assertEqual(self.cli.played, ["ReactToCliff"])
        self.complete_animation()
        self.assertEqual(self.cli.played, ["ReactToCliff", "ReactToPickup"])
        self.assertNotDone()
        self.complete_animation()
        self.assertDone()

    def test_unknown_trigger_does_not_leave_the_behavior_active(self):
        # An animation group the assets do not define is never played, so waiting for it to
        # complete would keep the behavior active forever.
        behavior = self.make(pycozmo.behavior.BehaviorPlayAnim, {"animTriggers": ["NoSuchTrigger"]})
        behavior.activate()
        self.assertEqual(self.cli.played, [])
        self.assertDone()

    def test_unknown_triggers_are_skipped(self):
        behavior = self.make(
            pycozmo.behavior.BehaviorPlayAnim, {"animTriggers": ["NoSuchTrigger", "ReactToCliff"]})
        behavior.activate()
        self.assertEqual(self.cli.played, ["ReactToCliff"])

    def test_can_be_activated_twice(self):
        # Behaviors are loaded once and activated on every reaction, so an activation must not
        # consume the configured triggers.
        behavior = self.make(pycozmo.behavior.BehaviorPlayAnim, {"animTriggers": ["ReactToCliff"]})
        behavior.activate()
        self.complete_animation()
        behavior.activate()
        self.assertEqual(self.cli.played, ["ReactToCliff", "ReactToCliff"])

    def test_deactivate_cancels_the_animation(self):
        behavior = self.make(pycozmo.behavior.BehaviorPlayAnim, {"animTriggers": ["ReactToCliff"]})
        behavior.activate()
        behavior.deactivate()
        self.assertEqual(self.cli.cancelled, 1)


class TestReactions(BehaviorTestCase):
    """ Each reaction plays the animation trigger it is expected to, then reports itself done. """

    REACTIONS = (
        (pycozmo.behavior.BehaviorReactToCliff, None, ["ReactToCliff"]),
        (pycozmo.behavior.BehaviorReactToPickup, None, ["ReactToPickup"]),
        (pycozmo.behavior.BehaviorReactToImpact, None, ["ReactToImpact"]),
        (pycozmo.behavior.BehaviorReactToUnexpectedMovement, None, ["ReactToUnexpectedMovement"]),
        (pycozmo.behavior.BehaviorReactToRobotOnBack, None, ["FlipDownFromBack"]),
        (pycozmo.behavior.BehaviorReactToRobotOnFace, None, ["FacePlantRoll"]),
        (pycozmo.behavior.BehaviorReactToRobotShaken, None, ["DizzyShakeStop"]),
        (pycozmo.behavior.BehaviorReactToPet, None, ["PetDetectionShort"]),
        (pycozmo.behavior.BehaviorReactToCubeMoved, None, ["CubeMovedSense"]),
        (pycozmo.behavior.BehaviorReactToSparked, None, ["SparkGetIn"]),
        (pycozmo.behavior.BehaviorRamIntoBlock, None, ["SoundOnlyRamIntoBlock"]),
        (pycozmo.behavior.BehaviorFistBump, None, ["FistBumpRequestOnce", "FistBumpLeftHanging"]),
        (pycozmo.behavior.BehaviorAcknowledge,
         {"ReactionAnimGroup": "ReactToNewBlockSmall"}, ["ReactToNewBlockSmall"]),
        (pycozmo.behavior.BehaviorAcknowledge,
         {"ReactionAnimGroup": "AcknowledgeObject"}, ["AcknowledgeObject"]),
        (pycozmo.behavior.BehaviorReactToFrustration,
         {"anim": "FrustratedByFailure"}, ["FrustratedByFailure"]),
    )

    def test_reactions(self):
        for behavior_class, conf, expected in self.REACTIONS:
            with self.subTest(behavior=behavior_class.__name__, expected=expected):
                self.setUp()
                behavior = self.make(behavior_class, conf, behavior_id=behavior_class.__name__)
                behavior.activate()
                for i, name in enumerate(expected):
                    self.assertEqual(self.cli.played, expected[:i + 1])
                    self.assertNotDone()
                    self.complete_animation()
                self.assertDone()


class TestBehaviorReactToRobotOnSide(BehaviorTestCase):

    def test_left_side(self):
        self.cli.robot_orientation = pycozmo.robot.RobotOrientation.ON_LEFT_SIDE
        behavior = self.make(pycozmo.behavior.BehaviorReactToRobotOnSide)
        behavior.activate()
        self.assertEqual(self.cli.played, ["ReactToOnLeftSide"])
        self.complete_animation()
        self.assertDone()

    def test_right_side(self):
        self.cli.robot_orientation = pycozmo.robot.RobotOrientation.ON_RIGHT_SIDE
        behavior = self.make(pycozmo.behavior.BehaviorReactToRobotOnSide)
        behavior.activate()
        self.assertEqual(self.cli.played, ["ReactToOnRightSide"])
        self.complete_animation()
        self.assertDone()


class TestBehaviorReactToOnCharger(BehaviorTestCase):

    # Short enough to keep the tests quick, long enough not to expire while they run.
    CONF = {
        "behaviorID": "ReactToOnCharger",
        "timeTilSleepAnimation_s": 0.05,
        "timeTilDisconnection_s": 0.1,
    }

    def make_behavior(self, **overrides):
        conf = dict(self.CONF)
        conf.update(overrides)
        return self.make(pycozmo.behavior.BehaviorReactToOnCharger, conf)

    def wait_for_timer(self, behavior):
        """ Wait out the behavior's pending timer. """
        timer = behavior.timer
        self.assertIsNotNone(timer)
        timer.join(2.0)
        self.assertFalse(timer.is_alive())

    def test_reads_the_delays_from_the_configuration(self):
        behavior = self.make_behavior()
        self.assertEqual(behavior.time_til_sleep_animation, 0.05)
        self.assertEqual(behavior.time_til_disconnection, 0.1)

    def test_defaults_the_delays(self):
        behavior = self.make(pycozmo.behavior.BehaviorReactToOnCharger)
        self.assertEqual(behavior.time_til_sleep_animation, 300.0)
        self.assertEqual(behavior.time_til_disconnection, 330.0)

    def test_plays_the_charger_reaction_then_sleeps_then_completes(self):
        behavior = self.make_behavior()
        behavior.activate()
        self.assertEqual(self.cli.played, ["PlacedOnCharger"])

        # The reaction being over only starts the wait before sleep.
        self.complete_animation()
        self.assertNotDone()
        self.wait_for_timer(behavior)
        self.assertEqual(self.cli.played, ["PlacedOnCharger", "GoToSleepGetIn"])

        self.complete_animation()
        self.assertEqual(
            self.cli.played, ["PlacedOnCharger", "GoToSleepGetIn", "GoToSleepSleeping"])
        self.assertNotDone()

        # Only the disconnection delay, after the sleep animation, ends the behavior.
        self.complete_animation()
        self.assertNotDone()
        self.wait_for_timer(behavior)
        self.assertDone()

    def test_completes_when_taken_off_the_charger(self):
        behavior = self.make_behavior(timeTilSleepAnimation_s=300.0, timeTilDisconnection_s=330.0)
        behavior.activate()
        self.complete_animation()
        self.assertNotDone()
        self.cli.dispatch(pycozmo.event.EvtRobotOnChargerChange, self.cli, False)
        self.assertDone()
        self.assertIsNone(behavior.timer)

    def test_stays_active_while_on_the_charger(self):
        behavior = self.make_behavior(timeTilSleepAnimation_s=300.0, timeTilDisconnection_s=330.0)
        behavior.activate()
        self.complete_animation()
        self.cli.dispatch(pycozmo.event.EvtRobotOnChargerChange, self.cli, True)
        self.assertNotDone()

    def test_deactivate_cancels_the_pending_timer(self):
        behavior = self.make_behavior(timeTilSleepAnimation_s=300.0, timeTilDisconnection_s=330.0)
        behavior.activate()
        self.complete_animation()
        self.assertIsNotNone(behavior.timer)
        behavior.deactivate()
        self.assertIsNone(behavior.timer)
        self.assertEqual(self.cli.cancelled, 1)


class TestWantsToRun(BehaviorTestCase):
    """
    What a behavior answers when the activity engine offers it the robot.

    Nothing consults this for a reaction: a trigger names its behavior and the brain runs it. It is
    the activity engine, choosing what to do when nothing has happened, that asks.
    """

    def test_an_unimplemented_behavior_never_runs(self):
        # Activating one only logs that and reports it done, and the engine would offer it the
        # robot again at once.
        self.assertFalse(self.make(pycozmo.behavior.Behavior).wants_to_run())

    def test_an_animation_behavior_runs_when_its_animation_is_there(self):
        behavior = self.make(pycozmo.behavior.BehaviorPlayAnim, {"animTriggers": ["ReactToCliff"]})
        self.assertTrue(behavior.wants_to_run())

    def test_an_animation_behavior_holds_back_without_its_animation(self):
        behavior = self.make(pycozmo.behavior.BehaviorPlayAnim, {"animTriggers": ["NoSuchTrigger"]})
        self.assertFalse(behavior.wants_to_run())

    def test_a_strategy_this_library_cannot_read_holds_a_behavior_back(self):
        # ReactToObstacle is the only behavior in the resources carrying one, and it asks for
        # ObstacleDetected. Nothing here sees an obstacle.
        behavior = self.make(pycozmo.behavior.BehaviorPlayAnim, {
            "animTriggers": ["ReactToCliff"],
            "wantsToRunStrategyConfig": {"strategyType": "ObstacleDetected"}})
        self.assertFalse(behavior.wants_to_run())


class TestBehaviorDriveOffCharger(BehaviorTestCase):

    def make_behavior(self, extra_distance=60.0):
        return self.make(pycozmo.behavior.BehaviorDriveOffCharger,
                         {"extraDistanceToDrive_mm": extra_distance}, "DriveOffCharger")

    def test_it_only_runs_on_the_charger(self):
        behavior = self.make_behavior()
        self.assertFalse(behavior.wants_to_run())
        self.cli.robot_status = pycozmo.robot.RobotStatusFlag.IS_ON_CHARGER
        self.assertTrue(behavior.wants_to_run())

    def test_it_drives_forward(self):
        # It used to report itself done without moving, so a robot on its charger stayed on it -
        # and, offered the robot by the engine, would have been offered it again straight away.
        behavior = self.make_behavior()
        behavior.activate()
        self.assertEqual(behavior.timer.interval, (40.0 + 60.0) / behavior.SPEED)
        self.assertEqual(self.cli.wheel_speeds, [(behavior.SPEED, behavior.SPEED)])
        self.assertEqual(self.posted_emotion_events(), ["DriveOffCharger"])
        self.assertNotDone()

    def test_it_stops_where_the_configuration_says(self):
        behavior = self.make_behavior(extra_distance=0.0)
        behavior.activate()
        self.assertEqual(behavior.timer.interval, 40.0 / behavior.SPEED)

    def test_it_stops_when_it_has_gone_far_enough(self):
        behavior = self.make_behavior()
        behavior.activate()
        behavior.timer.cancel()
        behavior._arrived()
        self.assertEqual(self.cli.stopped, 1)
        self.assertDone()

    def test_it_does_not_set_off_again_at_once(self):
        # The robot's status takes a moment to show the charger clear, and a second attempt in the
        # meantime would drive it twice as far as the configuration asks.
        behavior = self.make_behavior()
        self.cli.robot_status = pycozmo.robot.RobotStatusFlag.IS_ON_CHARGER
        behavior.activate()
        behavior.timer.cancel()
        self.assertFalse(behavior.wants_to_run())

    def test_it_gives_up_rather_than_driving_across_the_table(self):
        # A status stuck on the charger would otherwise have the robot drive off it for ever.
        behavior = self.make_behavior()
        self.cli.robot_status = pycozmo.robot.RobotStatusFlag.IS_ON_CHARGER
        for attempt in range(behavior.MAX_ATTEMPTS):
            behavior.last_run = 0.0
            self.assertTrue(behavior.wants_to_run(), attempt)
            behavior.activate()
            behavior.timer.cancel()
        behavior.last_run = 0.0
        self.assertFalse(behavior.wants_to_run())
        # Taken off the charger, it is willing again.
        self.cli.robot_status = 0
        self.assertFalse(behavior.wants_to_run())
        self.cli.robot_status = pycozmo.robot.RobotStatusFlag.IS_ON_CHARGER
        self.assertTrue(behavior.wants_to_run())

    def test_being_deactivated_stops_the_motors(self):
        behavior = self.make_behavior()
        behavior.activate()
        behavior.deactivate()
        self.assertIsNone(behavior.timer)
        self.assertEqual(self.cli.stopped, 1)


class TestGetBehaviorClassFromDict(unittest.TestCase):
    """ Every reaction behavior of reactionTrigger_behavior_map.json that has an animation. """

    EXPECTED = {
        "AcknowledgeFace": pycozmo.behavior.BehaviorAcknowledge,
        "AcknowledgeObject": pycozmo.behavior.BehaviorAcknowledge,
        "DriveOffCharger": pycozmo.behavior.BehaviorDriveOffCharger,
        "FistBump": pycozmo.behavior.BehaviorFistBump,
        "PlayAnim": pycozmo.behavior.BehaviorPlayAnim,
        "PlayArbitraryAnim": pycozmo.behavior.BehaviorPlayArbitraryAnim,
        "RamIntoBlock": pycozmo.behavior.BehaviorRamIntoBlock,
        "ReactToCliff": pycozmo.behavior.BehaviorReactToCliff,
        "ReactToCubeMoved": pycozmo.behavior.BehaviorReactToCubeMoved,
        "ReactToFrustration": pycozmo.behavior.BehaviorReactToFrustration,
        "ReactToImpact": pycozmo.behavior.BehaviorReactToImpact,
        "ReactToOnCharger": pycozmo.behavior.BehaviorReactToOnCharger,
        "ReactToPet": pycozmo.behavior.BehaviorReactToPet,
        "ReactToPickup": pycozmo.behavior.BehaviorReactToPickup,
        "ReactToRobotOnBack": pycozmo.behavior.BehaviorReactToRobotOnBack,
        "ReactToRobotOnFace": pycozmo.behavior.BehaviorReactToRobotOnFace,
        "ReactToRobotOnSide": pycozmo.behavior.BehaviorReactToRobotOnSide,
        "ReactToRobotShaken": pycozmo.behavior.BehaviorReactToRobotShaken,
        "ReactToSparked": pycozmo.behavior.BehaviorReactToSparked,
        "ReactToUnexpectedMovement": pycozmo.behavior.BehaviorReactToUnexpectedMovement,
    }

    def test_known_classes(self):
        for behavior_class, expected in self.EXPECTED.items():
            with self.subTest(behavior_class=behavior_class):
                self.assertIs(
                    pycozmo.behavior.get_behavior_class_from_dict({"behaviorClass": behavior_class}),
                    expected)

    def test_unknown_class_falls_back(self):
        # AnimationTriggerMap.json holds no animation for these three, so they stay unimplemented.
        for behavior_class in ("ReactToMotorCalibration", "ReactToPlacedOnSlope",
                               "ReactToReturnedToTreads", "NoSuchBehaviorClass"):
            with self.subTest(behavior_class=behavior_class):
                self.assertIs(
                    pycozmo.behavior.get_behavior_class_from_dict({"behaviorClass": behavior_class}),
                    pycozmo.behavior.Behavior)


def cozmo_assets_available():
    try:
        pycozmo.util.check_assets()
    except pycozmo.exception.ResourcesNotFound:
        return False
    return True


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestAgainstCozmoAssets(unittest.TestCase):
    """ Check the behaviors against the Anki resources they name animations from. """

    resource_dir: str
    anim_triggers: Set[str]
    behaviors: Dict[str, pycozmo.behavior.Behavior]
    reaction_triggers: Dict[str, List[pycozmo.behavior.ReactionTrigger]]

    @classmethod
    def setUpClass(cls):
        cls.resource_dir = str(pycozmo.util.get_cozmo_asset_dir())
        # Animation triggers are the "CladEvent" names of the animation trigger map, and are only
        # usable if the animation group they name is on disk, since the loader skips the others.
        trigger_map = pycozmo.json_loader.load_json_file(os.path.join(
            cls.resource_dir, "cozmo_resources", "assets", "animationGroupMaps",
            "AnimationTriggerMap.json"))
        groups = set()
        for _, _, files in os.walk(os.path.join(
                cls.resource_dir, "cozmo_resources", "assets", "animationGroups")):
            groups.update(files)
        cls.anim_triggers = {pair["CladEvent"] for pair in trigger_map["Pairs"]
                             if pair["AnimName"] + ".json" in groups}
        cls.behaviors = pycozmo.behavior.load_behaviors(cls.resource_dir, cast(pycozmo.client.Client, FakeClient()))
        cls.reaction_triggers = pycozmo.behavior.load_reaction_trigger_behavior_map(cls.resource_dir)

    def test_every_named_animation_trigger_exists(self):
        # A trigger the resources do not define is dropped on activation, so a behavior whose name
        # for one is wrong would silently do nothing at all.
        for behavior in self.behaviors.values():
            if not isinstance(behavior, pycozmo.behavior.BehaviorPlayAnim):
                continue
            for trigger in behavior.get_anim_triggers():
                with self.subTest(behavior=behavior.get_id(), trigger=trigger):
                    self.assertIn(trigger, self.anim_triggers)

    def test_reactions_resolve_to_an_animation(self):
        # The reactions that are expected to be implemented, and the animation each plays.
        expected = {
            "AcknowledgeFace": ["ReactToNewBlockSmall"],
            "AcknowledgeObject": ["AcknowledgeObject"],
            "FistBump": ["FistBumpRequestOnce", "FistBumpLeftHanging"],
            "Hiccup": ["Hiccup"],
            "RamIntoBlock": ["SoundOnlyRamIntoBlock"],
            "ReactToCliff": ["ReactToCliff"],
            "ReactToCubeMoved": ["CubeMovedSense"],
            "ReactToFrustrationMajor": ["FrustratedByFailureMajor"],
            "ReactToFrustrationMinor": ["FrustratedByFailure"],
            "ReactToImpact": ["ReactToImpact"],
            "ReactToOnCharger": ["PlacedOnCharger"],
            "ReactToPet": ["PetDetectionShort"],
            "ReactToPickup": ["ReactToPickup"],
            "ReactToRobotOnBack": ["FlipDownFromBack"],
            "ReactToRobotOnFace": ["FacePlantRoll"],
            "ReactToRobotOnSide": ["ReactToOnRightSide"],
            "ReactToRobotShaken": ["DizzyShakeStop"],
            "ReactToSparked": ["SparkGetIn"],
            "ReactToUnexpectedMovement": ["ReactToUnexpectedMovement"],
        }
        for behavior_id, triggers in expected.items():
            with self.subTest(behavior=behavior_id):
                behavior = self.behaviors[behavior_id]
                assert isinstance(behavior, pycozmo.behavior.BehaviorPlayAnim)
                self.assertEqual(list(behavior.get_anim_triggers()), triggers)
                for trigger in triggers:
                    self.assertIn(trigger, self.anim_triggers)

    def test_unimplemented_reactions(self):
        # AnimationTriggerMap.json holds no animation for these, under their behaviour ID, their
        # reaction trigger, or any name close to either.
        for behavior_id in ("ReactToMotorCalibration", "ReactToPlacedOnSlope",
                            "ReactToReturnedToTreads"):
            with self.subTest(behavior=behavior_id):
                self.assertIs(type(self.behaviors[behavior_id]), pycozmo.behavior.Behavior)

    def test_every_reaction_trigger_has_a_behavior(self):
        for reactions in self.reaction_triggers.values():
            for reaction in reactions:
                with self.subTest(reaction=reaction.name, behavior=reaction.behavior_id):
                    self.assertIn(reaction.behavior_id, self.behaviors)

    def test_frustration_keeps_both_of_its_behaviors(self):
        # The map is keyed by trigger and Frustration appears twice, so keeping one behavior per
        # trigger dropped the minor variant and always ran the major one.
        frustration = self.reaction_triggers["Frustration"]
        self.assertEqual({reaction.behavior_id for reaction in frustration},
                         {"ReactToFrustrationMinor", "ReactToFrustrationMajor"})
        by_id = {reaction.behavior_id: reaction for reaction in frustration}
        self.assertEqual(by_id["ReactToFrustrationMinor"].max_confidence, -0.6)
        self.assertEqual(by_id["ReactToFrustrationMajor"].max_confidence, -0.9)
        self.assertEqual(by_id["ReactToFrustrationMinor"].cooldown_time, 60.0)


class TestEmotionEvents(BehaviorTestCase):
    """ Behaviors that shift the mood as well as playing an animation. """

    def test_frustration_posts_the_configured_emotion_event(self):
        behavior = self.make(pycozmo.behavior.BehaviorReactToFrustration,
                             {"anim": "FrustratedByFailure",
                              "finalEmotionEvent": "FinishedMinorFrustration"},
                             behavior_id="ReactToFrustrationMinor")
        behavior.activate()
        self.assertEqual(self.posted_emotion_events(), [])
        self.complete_animation()
        # Only once it is over, which is what "final" means in the configuration.
        self.assertEqual(self.posted_emotion_events(), ["FinishedMinorFrustration"])
        self.assertDone()

    def test_frustration_without_an_emotion_event(self):
        behavior = self.make(pycozmo.behavior.BehaviorReactToFrustration,
                             {"anim": "FrustratedByFailure"})
        behavior.activate()
        self.complete_animation()
        self.assertEqual(self.posted_emotion_events(), [])
        self.assertDone()

    def test_unexpected_movement_costs_confidence(self):
        behavior = self.make(pycozmo.behavior.BehaviorReactToUnexpectedMovement)
        behavior.activate()
        self.complete_animation()
        self.assertEqual(self.posted_emotion_events(), ["ReactToUnexpectedMovement"])
        self.assertDone()

    def test_driving_off_the_charger_builds_confidence(self):
        behavior = self.make(pycozmo.behavior.BehaviorDriveOffCharger)
        behavior.activate()
        self.addCleanup(behavior.deactivate)
        self.assertEqual(self.posted_emotion_events(), ["DriveOffCharger"])

    def test_a_plain_reaction_posts_none(self):
        behavior = self.make(pycozmo.behavior.BehaviorReactToPickup)
        behavior.activate()
        self.complete_animation()
        self.assertEqual(self.posted_emotion_events(), [])
