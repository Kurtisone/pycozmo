"""

Tests for the nurture needs: Repair, Energy and Play.

A need is not an emotion. An emotion is a shove that decays back to nothing within a couple of
minutes; a need falls for hours and only something done to the robot puts it back. What the needs are
for is the behaviors and activities that read them, so those are tested here too - the requests a
robot slips in between whatever else it is doing as it gets bored, and the activities that take it
over once a need is critical.

"""

import unittest

import pycozmo
from pycozmo import needs

from .test_behavior import BehaviorTestCase


def make_need(name="Play", level=1.0, rates=(), modifiers=(), fullness_cooldown=0.0, now=0.0):
    """ A need with the brackets Anki gives Play and whatever else a test asks for. """
    return needs.Need(
        name=name,
        level=level,
        minimum=0.03,
        maximum=1.0,
        brackets={"Full": 0.99, "Normal": 0.5, "Warning": 0.14, "Critical": 0.0},
        decay_rates=[needs.DecayRate(threshold, per_minute) for threshold, per_minute in rates],
        decay_modifiers=[needs.DecayModifier(threshold, multipliers)
                         for threshold, multipliers in modifiers],
        fullness_cooldown=fullness_cooldown,
        now=now)


class TestNeed(unittest.TestCase):

    def test_the_bracket_a_level_is_read_in(self):
        need = make_need()
        for level, bracket in ((1.0, "Full"), (0.99, "Full"), (0.98, "Normal"), (0.5, "Normal"),
                               (0.49, "Warning"), (0.14, "Warning"), (0.13, "Critical"),
                               (0.03, "Critical")):
            need.level = level
            self.assertEqual(bracket, need.bracket, "at {}".format(level))
            self.assertTrue(need.in_bracket(bracket))

    def test_the_level_stays_within_its_bounds(self):
        need = make_need(level=0.5)
        need.add(10.0)
        self.assertEqual(1.0, need.level)
        need.add(-10.0)
        self.assertEqual(0.03, need.level, "a need bottoms out at the configured minimum, not nought")

    def test_the_decay_rate_comes_from_the_level(self):
        # As Anki writes them for Play: the rate applies from its threshold upwards.
        need = make_need(rates=((0.5, 0.014), (0.2, 0.013), (0.03, 0.012)))
        for level, rate in ((1.0, 0.014), (0.5, 0.014), (0.49, 0.013), (0.2, 0.013), (0.1, 0.012)):
            need.level = level
            self.assertAlmostEqual(rate, need.decay_per_minute, msg="at {}".format(level))

    def test_the_rate_is_chosen_by_threshold_and_not_by_position(self):
        # needs_decay_config.json lists these in no reliable order.
        need = make_need(rates=((0.03, 0.012), (0.5, 0.014), (0.2, 0.013)))
        need.level = 1.0
        self.assertAlmostEqual(0.014, need.decay_per_minute)

    def test_a_need_below_every_threshold_stops_falling(self):
        need = make_need(level=0.03, rates=((0.5, 0.014), ))
        self.assertEqual(0.0, need.decay_per_minute)

    def test_decay_takes_one_rate_per_period(self):
        need = make_need(rates=((0.0, 0.01), ))
        need.decay(3)
        self.assertAlmostEqual(0.97, need.level)

    def test_a_multiplier_makes_a_need_fall_faster(self):
        need = make_need(rates=((0.0, 0.01), ))
        need.decay(2, 2.0)
        self.assertAlmostEqual(0.96, need.level)


class TestFullnessCooldown(unittest.TestCase):
    """ A full need waits before it starts falling: twenty minutes, for all three. """

    def test_a_full_need_holds_for_its_cooldown(self):
        need = make_need(level=1.0, fullness_cooldown=1200.0, now=0.0)
        self.assertTrue(need.holds_at_full(0.0))
        self.assertTrue(need.holds_at_full(1199.0))
        self.assertFalse(need.holds_at_full(1200.0))

    def test_a_need_that_is_not_full_does_not_hold(self):
        need = make_need(level=0.5, fullness_cooldown=1200.0, now=0.0)
        self.assertFalse(need.holds_at_full(0.0))

    def test_coming_back_up_to_full_starts_the_wait_again(self):
        need = make_need(level=0.5, fullness_cooldown=1200.0, now=0.0)
        need.add(0.5, now=5000.0)
        self.assertEqual(1.0, need.level)
        self.assertTrue(need.holds_at_full(5000.0))
        self.assertFalse(need.holds_at_full(6200.0))

    def test_falling_below_full_ends_the_wait(self):
        need = make_need(level=1.0, fullness_cooldown=1200.0, now=0.0)
        need.add(-0.02, now=10.0)
        self.assertIsNone(need.full_since)


class TestNeeds(unittest.TestCase):

    def make(self, **levels):
        held = {}
        for name in needs.NEEDS:
            held[name] = make_need(name=name, level=levels.get(name, 1.0),
                                   rates=((0.0, 0.01), ), now=0.0)
        return needs.Needs(needs=held, decay_period=60.0)

    def test_nothing_happens_before_a_whole_period_has_passed(self):
        held = self.make()
        held.update(now=59.0)
        self.assertEqual(1.0, held.level("Play"))
        held.update(now=60.0)
        self.assertAlmostEqual(0.99, held.level("Play"))

    def test_time_left_over_is_carried_rather_than_lost(self):
        # Called every frame, as the heartbeat does, a need still falls once a minute exactly.
        held = self.make()
        for tick in range(1, 30 * 300 + 1):
            held.update(now=tick / 30.0)
        # 300 s is five periods.
        self.assertAlmostEqual(0.95, held.level("Play"), places=6)

    def test_stepping_coarsely_and_finely_agree(self):
        # The rate a need falls at depends on the bracket it is in, so several periods at once have
        # to be applied one at a time. They are not, and a caller as slow as a stalled heartbeat
        # thread would see a need fall at a rate it left behind long ago.
        rates = ((0.5, 0.01), (0.2, 0.05), (0.03, 0.2))
        fine = needs.Needs(needs={"Play": make_need(rates=rates, now=0.0)}, decay_period=60.0)
        coarse = needs.Needs(needs={"Play": make_need(rates=rates, now=0.0)}, decay_period=60.0)
        for minute in range(1, 61):
            fine.update(now=minute * 60.0)
        coarse.update(now=3600.0)
        self.assertAlmostEqual(fine.level("Play"), coarse.level("Play"), places=9)
        self.assertLess(fine.level("Play"), 1.0, "the need did fall")

    def test_a_need_that_is_not_held_reads_as_full(self):
        held = needs.Needs(needs={})
        self.assertEqual(1.0, held.level("Play"))
        self.assertEqual("Full", held.bracket("Play"))
        self.assertTrue(held.in_bracket("Play", "Full"))

    def test_one_need_can_make_another_fall_faster(self):
        # The only modifier that does anything in Anki's own configuration: Repair between 0.03 and
        # 0.3 makes Play fall twice as fast, so a broken robot also gets bored quicker.
        repair = make_need(name="Repair", level=0.1, rates=((0.0, 0.0), ),
                           modifiers=((0.3, {"Play": 1.0}), (0.03, {"Play": 2.0}),
                                      (0.0, {"Play": 1.0})), now=0.0)
        play = make_need(name="Play", level=1.0, rates=((0.0, 0.01), ), now=0.0)
        held = needs.Needs(needs={"Repair": repair, "Play": play}, decay_period=60.0)
        self.assertEqual(2.0, held.multiplier_for("Play"))
        held.update(now=60.0)
        self.assertAlmostEqual(0.98, held.level("Play"))

        repair.level = 0.5
        self.assertEqual(1.0, held.multiplier_for("Play"))

    def test_every_need_falls_against_the_same_picture_of_the_others(self):
        # Repair is about to cross into the band that doubles Play's decay. Play must fall at the
        # rate that held when the period started, whichever order the needs happen to be held in.
        repair = make_need(name="Repair", level=0.31, rates=((0.0, 0.02), ),
                           modifiers=((0.3, {"Play": 1.0}), (0.03, {"Play": 2.0})), now=0.0)
        play = make_need(name="Play", level=1.0, rates=((0.0, 0.01), ), now=0.0)
        held = needs.Needs(needs={"Repair": repair, "Play": play}, decay_period=60.0)
        held.update(now=60.0)
        self.assertAlmostEqual(0.29, repair.level)
        self.assertAlmostEqual(0.99, play.level, msg="not yet doubled")


class TestNeedAction(unittest.TestCase):

    def test_an_action_is_read_from_its_configuration(self):
        action = needs.NeedAction.from_json({
            "actionId": "Feed", "repairDelta": 0.001, "repairRange": 0,
            "energyDelta": 0.33, "energyRange": 0.005, "playDelta": 0, "playRange": 0,
            "cooldownSecs": 0, "freeplaySparksRewardWeight": 0})
        self.assertEqual("Feed", action.action_id)
        self.assertEqual({"Repair": (0.001, 0.0), "Energy": (0.33, 0.005)}, action.deltas)
        self.assertNotIn("Play", action.deltas, "a need worth nothing is left out")

    def test_a_figure_with_no_range_is_exact(self):
        action = needs.NeedAction("Test", {"Play": (0.25, 0.0)})
        self.assertEqual({"Play": 0.25}, action.sample())

    def test_a_range_spreads_either_side_of_the_figure(self):
        action = needs.NeedAction("Test", {"Play": (0.75, 0.03)})
        drawn = [action.sample()["Play"] for _ in range(200)]
        self.assertTrue(all(0.72 <= value <= 0.78 for value in drawn))
        self.assertLess(min(drawn), 0.75)
        self.assertGreater(max(drawn), 0.75)

    def test_an_action_holds_back_for_its_cooldown(self):
        action = needs.NeedAction("SeeFace", {"Play": (0.05, 0.0)}, cooldown=60.0)
        self.assertTrue(action.is_ready(0.0))
        action.last_applied_time = 0.0
        self.assertFalse(action.is_ready(59.0))
        self.assertTrue(action.is_ready(60.0))

    def test_applying_an_action_moves_the_needs(self):
        held = needs.Needs(
            needs={"Play": make_need(name="Play", level=0.5)},
            actions={"FistBump": needs.NeedAction("FistBump", {"Play": (0.25, 0.0)})})
        self.assertTrue(held.apply_action("FistBump", now=0.0))
        self.assertAlmostEqual(0.75, held.level("Play"))

    def test_an_action_nothing_names_does_nothing(self):
        held = needs.Needs(needs={"Play": make_need()})
        self.assertFalse(held.apply_action("NoSuchAction", now=0.0))

    def test_an_action_inside_its_cooldown_does_nothing(self):
        held = needs.Needs(
            needs={"Play": make_need(name="Play", level=0.5)},
            actions={"SeeFace": needs.NeedAction("SeeFace", {"Play": (0.05, 0.0)}, cooldown=60.0)})
        self.assertTrue(held.apply_action("SeeFace", now=0.0))
        self.assertFalse(held.apply_action("SeeFace", now=30.0))
        self.assertAlmostEqual(0.55, held.level("Play"))
        self.assertTrue(held.apply_action("SeeFace", now=60.0))


class TestNeedsStrategyConfig(unittest.TestCase):

    def held(self, **levels):
        return needs.Needs(needs={name: make_need(name=name, level=levels.get(name, 1.0))
                                  for name in needs.NEEDS})

    def test_a_bracket_condition_holds_while_the_need_is_in_it(self):
        config = pycozmo.activity.NeedsStrategyConfig.from_json(
            {"strategyType": "InNeedsBracket", "need": "Energy", "needBracket": "Critical"})
        self.assertTrue(config.is_supported)
        self.assertFalse(config.holds(self.held(Energy=1.0)))
        self.assertTrue(config.holds(self.held(Energy=0.1)))

    def test_a_transition_condition_holds_once(self):
        config = pycozmo.activity.NeedsStrategyConfig.from_json(
            {"strategyType": "ExpressNeedsTransition", "need": "Play"})
        full, critical = self.held(Play=1.0), self.held(Play=0.05)
        self.assertFalse(config.holds(full))
        self.assertTrue(config.holds(critical))
        config.expressed(critical)
        self.assertFalse(config.holds(critical), "announced once, not again")
        # Coming back up and falling again is a fresh transition.
        config.expressed(full)
        self.assertTrue(config.holds(critical))

    def test_a_condition_nothing_tracks_never_holds(self):
        config = pycozmo.activity.NeedsStrategyConfig.from_json(
            {"strategyType": "InNeedsBracket", "need": "Energy", "needBracket": "Critical"})
        self.assertFalse(config.holds(None))

    def test_an_unknown_condition_never_holds(self):
        config = pycozmo.activity.NeedsStrategyConfig.from_json(
            {"strategyType": "SomethingElse", "need": "Energy"})
        self.assertFalse(config.is_supported)
        self.assertFalse(config.holds(self.held(Energy=0.1)))


class TestNeedsStrategies(unittest.TestCase):

    @staticmethod
    def held(**levels):
        return needs.Needs(needs={name: make_need(name=name, level=levels.get(name, 1.0))
                                  for name in needs.NEEDS})

    @staticmethod
    def strategy(data):
        return pycozmo.activity.ActivityStrategy.from_json(data)

    def test_the_needs_strategies_are_evaluated(self):
        for name in ("Simple", "Needs", "SevereNeedTransition", "NeedBasedCooldown"):
            self.assertTrue(self.strategy({"type": name}).is_supported, name)
        for name in ("Spark", "Pyramid", "PlayWithHumans"):
            self.assertFalse(self.strategy({"type": name}).is_supported, name)

    def test_a_strategy_with_no_condition_is_never_held_back_by_the_needs(self):
        strategy = self.strategy({"type": "Simple"})
        self.assertTrue(strategy.needs_allow(self.held(Energy=0.05)))
        self.assertTrue(strategy.needs_allow(None))

    def test_a_higher_priority_need_takes_precedence(self):
        # needsSevereLowEnergy, as the resources configure it: a robot both broken and starving asks
        # to be mended rather than fed.
        strategy = self.strategy({
            "type": "Needs",
            "wantsToRunStrategyConfig": {"strategyType": "InNeedsBracket",
                                         "need": "Energy", "needBracket": "Critical"},
            "higherPriorityStrategyConfig": {"strategyType": "InNeedsBracket",
                                             "need": "Repair", "needBracket": "Critical"}})
        self.assertTrue(strategy.needs_allow(self.held(Energy=0.1)))
        self.assertFalse(strategy.needs_allow(self.held(Energy=0.1, Repair=0.01)))

    def test_a_need_based_cooldown_is_read_off_its_graph(self):
        # Singing, as the resources configure it.
        strategy = self.strategy({
            "type": "NeedBasedCooldown", "needId": "Play",
            "cooldownBaseSecs": 600.0, "cooldownRandomnessSecs": 0.0,
            "needCooldownGraph": {"nodes": [{"x": 0.0, "y": 1500.0}, {"x": 0.2, "y": 1200.0},
                                            {"x": 0.8, "y": 900.0}, {"x": 1.0, "y": 600.0}]},
            "needCooldownRandomnessGraph": {"nodes": [{"x": 0.0, "y": 0.0}]}})
        self.assertAlmostEqual(600.0, strategy.get_cooldown(self.held(Play=1.0)))
        self.assertAlmostEqual(900.0, strategy.get_cooldown(self.held(Play=0.8)))
        # A need bottoms out at 0.03 rather than nought, which is just short of the graph's first
        # node, so the longest rest a robot actually reaches is a little under the 1500 s there.
        self.assertAlmostEqual(1455.0, strategy.get_cooldown(self.held(Play=0.0)))

    def test_a_need_based_cooldown_falls_back_on_the_flat_figures(self):
        strategy = self.strategy({"type": "NeedBasedCooldown", "needId": "Play",
                                  "cooldownBaseSecs": 600.0, "cooldownRandomnessSecs": 0.0})
        self.assertAlmostEqual(600.0, strategy.get_cooldown(None))


class TestNeedsBehaviors(BehaviorTestCase):

    TRIGGER = "ReactToCliff"

    def held(self, **levels):
        return needs.Needs(needs={name: make_need(name=name, level=levels.get(name, 1.0))
                                  for name in needs.NEEDS})

    def make_needs_behavior(self, behavior_class, conf, robot_needs):
        conf = dict(conf)
        conf.setdefault("behaviorID", "TestNeedsBehavior")
        behavior = behavior_class(self.cli, conf, robot_needs)
        self.cli.add_child_dispatcher(behavior)
        self.addCleanup(self.cli.del_child_dispatcher, behavior)
        return behavior

    def express(self, robot_needs, need="Play", bracket="Warning", cooldown=None):
        conf = {"animTriggers": [self.TRIGGER], "need": need, "needBracket": bracket}
        if cooldown is not None:
            conf["cooldown"] = {"nodes": cooldown}
        return self.make_needs_behavior(pycozmo.behavior.BehaviorExpressNeeds, conf, robot_needs)

    def test_a_request_waits_for_its_bracket(self):
        robot_needs = self.held(Play=1.0)
        behavior = self.express(robot_needs)
        self.assertFalse(behavior.wants_to_run())
        robot_needs["Play"].level = 0.3
        self.assertTrue(behavior.wants_to_run())
        robot_needs["Play"].level = 0.05
        self.assertFalse(behavior.wants_to_run(), "a critical need is somebody else's request")

    def test_a_request_asks_nothing_when_nothing_tracks_the_needs(self):
        behavior = self.express(None)
        self.assertFalse(behavior.wants_to_run())

    def test_the_cooldown_comes_from_the_need_level(self):
        # Needs_MildLowEnergyRequest, as the resources configure it: every 20 s with the need almost
        # gone, every 60 s while it is still half full.
        nodes = [{"x": 0.0, "y": 20.0}, {"x": 0.1, "y": 20.0},
                 {"x": 0.5, "y": 60.0}, {"x": 1.0, "y": 60.0}]
        robot_needs = self.held(Play=0.05)
        behavior = self.express(robot_needs, cooldown=nodes)
        self.assertAlmostEqual(20.0, behavior.get_cooldown())
        robot_needs["Play"].level = 0.5
        self.assertAlmostEqual(60.0, behavior.get_cooldown())

    def test_a_request_holds_back_after_asking(self):
        nodes = [{"x": 0.0, "y": 60.0}]
        robot_needs = self.held(Play=0.3)
        behavior = self.express(robot_needs, cooldown=nodes)
        self.assertTrue(behavior.wants_to_run())
        behavior.activate()
        self.assertEqual([self.TRIGGER], self.cli.played)
        self.assertFalse(behavior.wants_to_run())
        # Far enough in the past that the cooldown has run out.
        behavior.last_run_time -= 61.0
        self.assertTrue(behavior.wants_to_run())

    def test_an_announcement_is_made_once_per_bracket(self):
        robot_needs = self.held(Energy=0.05)
        behavior = self.make_needs_behavior(
            pycozmo.behavior.BehaviorPlayAnimOnNeedsChange,
            {"animTriggers": [self.TRIGGER], "need": "Energy"}, robot_needs)
        self.assertTrue(behavior.wants_to_run())
        behavior.activate()
        self.assertFalse(behavior.wants_to_run())
        # Fed back up, then starved again: that is a new transition to announce.
        robot_needs["Energy"].level = 1.0
        self.assertTrue(behavior.wants_to_run())
        behavior.activate()
        robot_needs["Energy"].level = 0.05
        self.assertTrue(behavior.wants_to_run())


class TestWait(BehaviorTestCase):
    """ The last resort of a severe needs activity: stand still and let the engine think again. """

    def make_wait(self):
        return self.make(pycozmo.behavior.BehaviorWait, {}, "Needs_Wait")

    def test_waiting_is_always_something_it_can_do(self):
        self.assertTrue(self.make_wait().wants_to_run())

    def test_it_holds_the_robot_rather_than_ending_at_once(self):
        # Reporting itself done straight away would have the engine offer it the robot again on the
        # next frame, thirty times a second.
        behavior = self.make_wait()
        behavior.activate()
        self.assertEqual(behavior.DURATION, behavior.timer.interval)
        self.assertNotDone()
        self.assertEqual([], self.cli.played)
        behavior.timer.cancel()

    def test_it_ends_once_it_has_waited(self):
        behavior = self.make_wait()
        behavior.activate()
        behavior.timer.cancel()
        behavior.done()
        self.assertDone()

    def test_being_taken_off_the_robot_stops_the_wait(self):
        behavior = self.make_wait()
        behavior.activate()
        behavior.deactivate()
        self.assertIsNone(behavior.timer)
        self.assertNotDone()


class TestDriveInDesperation(BehaviorTestCase):
    """
    One round of wandering and asking for help.

    Neither of the two behaviors in the resources names the need it belongs to, so a round ends and
    the engine decides whether to start another. That is what lets a fed robot get on with its life.
    """

    TRIGGER = "ReactToCliff"

    PROFILE = {
        "speed_mmps": 40.0,
        "pointTurnSpeed_rad_per_sec": 1.5,
    }

    def make_drive(self, **conf):
        data = {"minTimeToIdle": 1.5, "maxTimeToIdle": 6.5,
                "requestAnimTrigger": self.TRIGGER, "motionProfile": dict(self.PROFILE)}
        data.update(conf)
        return self.make(pycozmo.behavior.BehaviorDriveInDesperation, data,
                         "Needs_SevereLowEnergyState")

    def test_it_reads_its_configuration(self):
        behavior = self.make_drive()
        self.assertEqual(1.5, behavior.min_time_to_idle)
        self.assertEqual(6.5, behavior.max_time_to_idle)
        self.assertEqual(40.0, behavior.speed)
        self.assertEqual(1.5, behavior.turn_speed)
        self.assertEqual((self.TRIGGER, ), tuple(behavior.get_anim_triggers()))

    def test_it_will_not_run_without_an_animation_to_ask_with(self):
        self.assertFalse(self.make_drive(requestAnimTrigger="NoSuchTrigger").wants_to_run())
        self.assertTrue(self.make_drive().wants_to_run())

    def test_it_turns_before_it_drives(self):
        # Setting off straight every time would take the robot off the table in one direction.
        behavior = self.make_drive()
        behavior.activate()
        self.assertEqual(1, len(self.cli.wheel_speeds))
        left, right = self.cli.wheel_speeds[0]
        self.assertAlmostEqual(-left, right, msg="one wheel each way turns it on the spot")
        # Twice the wheel speed over the track width is the turn rate the profile asks for.
        rate = 2.0 * abs(right) / pycozmo.robot.TRACK_WIDTH.mm
        self.assertAlmostEqual(behavior.turn_speed, rate)
        self.assertLessEqual(behavior.timer.interval, behavior.MAX_TURN / behavior.turn_speed)
        self.assertNotDone()
        behavior.timer.cancel()

    def test_it_drives_forward_for_a_while(self):
        behavior = self.make_drive()
        behavior.activate()
        behavior.timer.cancel()
        behavior._turned()
        self.assertEqual((40.0, 40.0), self.cli.wheel_speeds[-1])
        self.assertGreaterEqual(behavior.timer.interval, behavior.min_time_to_idle)
        self.assertLessEqual(behavior.timer.interval, behavior.max_time_to_idle)
        behavior.timer.cancel()

    def test_it_stops_and_asks(self):
        behavior = self.make_drive()
        behavior.activate()
        behavior.timer.cancel()
        behavior._turned()
        behavior.timer.cancel()
        behavior._arrived()
        self.assertEqual(1, self.cli.stopped)
        self.assertEqual([self.TRIGGER], self.cli.played)
        self.assertNotDone()

    def test_one_round_ends_when_it_has_asked(self):
        # The round has to end, or the engine would never look at the needs again and a fed robot
        # would go on begging.
        behavior = self.make_drive()
        behavior.activate()
        behavior.timer.cancel()
        behavior._turned()
        behavior.timer.cancel()
        behavior._arrived()
        self.complete_animation()
        self.assertDone()

    def test_being_taken_off_the_robot_stops_the_motors(self):
        behavior = self.make_drive()
        behavior.activate()
        behavior.deactivate()
        self.assertIsNone(behavior.timer)
        self.assertEqual(1, self.cli.stopped)
        self.assertEqual(1, self.cli.cancelled)

    def test_it_wanders_rather_than_repeating_itself(self):
        turns = set()
        for _ in range(40):
            behavior = self.make_drive()
            behavior.activate()
            turns.add(self.cli.wheel_speeds[-1])
            behavior.timer.cancel()
        self.assertGreater(len(turns), 1, "the turn is drawn afresh each round")
        self.assertTrue(any(left < 0 for left, _ in turns), "it turns both ways")
        self.assertTrue(any(left > 0 for left, _ in turns))


class TestAgainstCozmoAssets(unittest.TestCase):
    """ The needs as Anki configured them, which is what the timings above are drawn from. """

    @classmethod
    def setUpClass(cls):
        try:
            pycozmo.util.check_assets()
        except Exception as e:
            raise unittest.SkipTest(str(e))

    def setUp(self):
        # Loaded afresh for each test: one of them runs the needs down a whole day, and a set of
        # needs shared between tests would carry that into whichever ran next.
        self.held = needs.load_needs(str(pycozmo.util.get_cozmo_asset_dir()), now=0.0)

    def test_the_three_needs_start_full(self):
        self.assertEqual(set(needs.NEEDS), set(self.held.needs))
        for name in needs.NEEDS:
            self.assertEqual(1.0, self.held.level(name))
            self.assertEqual("Full", self.held.bracket(name))
            self.assertEqual(0.03, self.held[name].minimum)
            self.assertEqual(1200.0, self.held[name].fullness_cooldown)
        self.assertEqual(60.0, self.held.decay_period)

    def test_play_falls_fastest_and_repair_slowest(self):
        rates = [self.held[name].decay_per_minute for name in ("Repair", "Energy", "Play")]
        self.assertEqual(sorted(rates), rates)
        self.assertAlmostEqual(0.0007, rates[0])
        self.assertAlmostEqual(0.014, rates[2])

    def test_the_actions_are_all_read(self):
        self.assertGreater(len(self.held.actions), 80)
        # What puts each need back, and the only three ways Repair goes up by any real amount.
        self.assertAlmostEqual(0.33, self.held.actions["Feed"].deltas["Energy"][0])
        for part in ("RepairHead", "RepairLift", "RepairTreads"):
            self.assertAlmostEqual(0.33, self.held.actions[part].deltas["Repair"][0])
        self.assertAlmostEqual(-0.15, self.held.actions["Fall"].deltas["Repair"][0])
        # Two behaviors in the resources share a name with an action, which is how the brain
        # applies them when they finish.
        self.assertIn("FistBump", self.held.actions)
        self.assertIn("PopAWheelie", self.held.actions)

    def test_how_long_each_need_takes_to_fall(self):
        """ The timings the documentation quotes, so that a configuration change shows up here. """
        reached: dict = {name: {} for name in needs.NEEDS}
        minute = 0
        while minute < 24 * 60:
            minute += 1
            self.held.update(now=minute * 60.0)
            for name in needs.NEEDS:
                reached[name].setdefault(self.held.bracket(name), minute)
        self.assertEqual({"Full": 1, "Normal": 20, "Warning": 55, "Critical": 83}, reached["Play"])
        self.assertEqual({"Full": 1, "Normal": 22, "Warning": 99, "Critical": 204}, reached["Energy"])
        self.assertEqual({"Full": 1, "Normal": 34, "Warning": 591, "Critical": 1151},
                         reached["Repair"])

    def test_the_activities_that_read_the_needs(self):
        activities = pycozmo.activity.load_activities(str(pycozmo.util.get_cozmo_asset_dir()))
        types = {aid: activities[aid].strategy.type for aid in activities}
        self.assertEqual("Needs", types["NeedsSevereLowEnergy"])
        self.assertEqual("Needs", types["NeedsSevereLowRepair"])
        self.assertEqual("SevereNeedTransition", types["NeedsSevereLowPlayGetIn"])
        self.assertEqual("NeedBasedCooldown", types["Singing"])
        for aid in ("NeedsSevereLowEnergy", "NeedsSevereLowRepair", "NeedsSevereLowPlayGetIn",
                    "Singing"):
            self.assertTrue(activities[aid].strategy.is_supported, aid)

        # Repair outranks Energy, and nothing outranks Repair.
        energy = activities["NeedsSevereLowEnergy"].strategy
        self.assertIsNotNone(energy.higher_priority_config)
        assert energy.higher_priority_config is not None
        self.assertEqual("Repair", energy.higher_priority_config.need)
        self.assertIsNone(activities["NeedsSevereLowRepair"].strategy.higher_priority_config)

    def test_the_everyday_activities_all_carry_the_needs_requests(self):
        """ The requests are interludes, which is why they show up between ordinary behaviors. """
        activities = pycozmo.activity.load_activities(str(pycozmo.util.get_cozmo_asset_dir()))
        for aid in ("NothingToDo", "PlayAlone", "Hiking", "Socialize", "BuildPyramid",
                    "PlayWithHumans"):
            chooser = activities[aid].interlude_chooser
            self.assertIsNotNone(chooser, aid)
            assert chooser is not None
            requests = [name for name in chooser.behavior_names if name.startswith("Needs_")]
            self.assertEqual(5, len(requests), "{} lists {}".format(aid, requests))


if __name__ == "__main__":
    unittest.main()
