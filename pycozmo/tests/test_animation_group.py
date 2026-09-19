
import unittest
from typing import Dict, Optional, Set

import pycozmo


def member(name, weight=1.0, cooldown=0.0, head_angle=None):
    """ Build a group member, optionally restricted to a head angle band. """
    data = {"Name": name, "Weight": weight, "CooldownTime_Sec": cooldown, "Mood": "Default"}
    if head_angle is not None:
        data.update({"UseHeadAngle": True,
                     "HeadAngleMin_Deg": head_angle[0], "HeadAngleMax_Deg": head_angle[1]})
    return pycozmo.anim.AnimationGroupMember.from_json(data)


def degrees(value):
    return pycozmo.util.Angle(degrees=value)


class TestAnimationGroupMember(unittest.TestCase):

    def test_no_band_suits_any_head_angle(self):
        m = member("anim")
        for angle in (-25.0, 0.0, 44.5):
            with self.subTest(angle=angle):
                self.assertTrue(m.matches_head_angle(degrees(angle)))

    def test_band_is_honoured(self):
        m = member("anim", head_angle=(10.0, 30.0))
        self.assertTrue(m.matches_head_angle(degrees(20.0)))
        self.assertTrue(m.matches_head_angle(degrees(10.0)))
        self.assertTrue(m.matches_head_angle(degrees(30.0)))
        self.assertFalse(m.matches_head_angle(degrees(9.0)))
        self.assertFalse(m.matches_head_angle(degrees(31.0)))

    def test_no_cooldown_is_never_on_cooldown(self):
        m = member("anim", cooldown=0.0)
        m.played(now=100.0)
        self.assertFalse(m.is_on_cooldown(now=100.0))

    def test_cooldown_expires(self):
        m = member("anim", cooldown=60.0)
        self.assertFalse(m.is_on_cooldown(now=100.0), "never played")
        m.played(now=100.0)
        self.assertTrue(m.is_on_cooldown(now=100.0))
        self.assertTrue(m.is_on_cooldown(now=159.9))
        self.assertFalse(m.is_on_cooldown(now=160.0))


class TestAnimationGroup(unittest.TestCase):

    def choices(self, group: pycozmo.anim.AnimationGroup,
                head_angle: Optional[pycozmo.util.Angle] = None,
                times: int = 60) -> Set[str]:
        return {group.choose_member(head_angle).name for _ in range(times)}

    def test_weighted_choice(self):
        group = pycozmo.anim.AnimationGroup([member("a", weight=1.0), member("b", weight=0.0)])
        self.assertEqual(self.choices(group), {"a"})

    def test_single_member_with_no_weight_is_still_playable(self):
        group = pycozmo.anim.AnimationGroup([member("a", weight=0.0)])
        self.assertEqual(self.choices(group), {"a"})

    def test_several_members_with_no_weight_are_still_playable(self):
        group = pycozmo.anim.AnimationGroup([member("a", weight=0.0), member("b", weight=0.0)])
        self.assertEqual(self.choices(group), {"a", "b"})

    def test_head_angle_picks_the_matching_band(self):
        # This is how the shipped groups are built: one animation per band, the angle baked in.
        group = pycozmo.anim.AnimationGroup([
            member("anim_head_angle_-20", head_angle=(-25.0, -10.0)),
            member("anim_0", head_angle=(-10.0, 10.0)),
            member("anim_head_angle_20", head_angle=(10.0, 30.0)),
            member("anim_head_angle_40", head_angle=(30.0, 45.0)),
        ])
        self.assertEqual(self.choices(group, degrees(-20.0)), {"anim_head_angle_-20"})
        self.assertEqual(self.choices(group, degrees(0.0)), {"anim_0"})
        self.assertEqual(self.choices(group, degrees(20.0)), {"anim_head_angle_20"})
        self.assertEqual(self.choices(group, degrees(40.0)), {"anim_head_angle_40"})

    def test_without_a_head_angle_every_member_is_a_candidate(self):
        group = pycozmo.anim.AnimationGroup([
            member("low", head_angle=(-25.0, -10.0)),
            member("high", head_angle=(30.0, 45.0)),
        ])
        self.assertEqual(self.choices(group), {"low", "high"})

    def test_a_member_with_no_band_competes_with_the_matching_one(self):
        # ag_greeting_happy is built this way: four banded variants plus one unrestricted.
        group = pycozmo.anim.AnimationGroup([
            member("banded", head_angle=(-10.0, 10.0)),
            member("unrestricted"),
        ])
        self.assertEqual(self.choices(group, degrees(0.0)), {"banded", "unrestricted"})

    def test_an_unmatched_head_angle_falls_back_to_every_member(self):
        # Rather than play nothing, which would leave a behavior waiting for an animation that
        # never completes.
        group = pycozmo.anim.AnimationGroup([member("only", head_angle=(30.0, 45.0))])
        self.assertEqual(self.choices(group, degrees(-20.0)), {"only"})

    def test_cooldown_is_avoided(self):
        group = pycozmo.anim.AnimationGroup([member("a", cooldown=60.0), member("b")])
        group.members[0].played()
        self.assertEqual(self.choices(group), {"b"})

    def test_playing_starts_the_cooldown(self):
        group = pycozmo.anim.AnimationGroup([member("a", cooldown=60.0), member("b")])
        self.assertEqual(group.members[0].last_played, 0.0)
        while group.choose_member().name != "a":
            pass
        self.assertNotEqual(group.members[0].last_played, 0.0)
        self.assertTrue(group.members[0].is_on_cooldown())

    def test_cooldown_gives_way_rather_than_playing_nothing(self):
        group = pycozmo.anim.AnimationGroup([member("a", cooldown=60.0)])
        group.choose_member()
        self.assertEqual(self.choices(group), {"a"})

    def test_head_angle_wins_over_cooldown(self):
        # The banded member that matches is on cooldown, so the band gives way, not the angle:
        # there is another member for this angle.
        group = pycozmo.anim.AnimationGroup([
            member("match_a", cooldown=60.0, head_angle=(-10.0, 10.0)),
            member("match_b", head_angle=(-10.0, 10.0)),
            member("other_band", head_angle=(30.0, 45.0)),
        ])
        group.members[0].played()
        self.assertEqual(self.choices(group, degrees(0.0)), {"match_b"})


def cozmo_assets_available():
    try:
        pycozmo.util.check_assets()
    except pycozmo.exception.ResourcesNotFound:
        return False
    return True


@unittest.skipUnless(cozmo_assets_available(), "Cozmo assets not downloaded.")
class TestAgainstCozmoAssets(unittest.TestCase):
    """ The shipped animation groups, which is where the head angle bands come from. """

    groups: Dict[str, pycozmo.anim.AnimationGroup]
    #: Head angles spanning the range the head can reach.
    ANGLES = (pycozmo.robot.MIN_HEAD_ANGLE.degrees, -20.0, -10.0, 0.0, 10.0, 20.0, 30.0, 40.0,
              pycozmo.robot.MAX_HEAD_ANGLE.degrees)

    @classmethod
    def setUpClass(cls):
        cls.groups = pycozmo.anim.load_animation_groups(str(pycozmo.util.get_cozmo_asset_dir()))

    def banded_groups(self):
        """ Groups whose every member is authored for one head angle band. """
        return {name: group for name, group in self.groups.items()
                if all(member.use_head_angle for member in group.members)}

    def find_banded_group(self, member_name):
        """ A banded group holding a member by that name. Group files are shared between triggers,
        and an animation can appear both in a banded group and in an unbanded one. """
        return next(group for group in self.banded_groups().values()
                    if any(member.name == member_name for member in group.members))

    def test_the_assets_do_use_head_angle_bands(self):
        # 43 of the 507 group files are built this way, reached through 57 of the 573 triggers.
        self.assertGreater(len(self.banded_groups()), 0)

    def test_every_group_always_has_something_to_play(self):
        # Falling back matters: a group that offered nothing would leave the behavior waiting for
        # an animation that never completes.
        for name, group in self.groups.items():
            for angle in self.ANGLES:
                with self.subTest(group=name, angle=angle):
                    self.assertTrue(group.get_candidates(pycozmo.util.Angle(degrees=angle)))

    def test_a_matching_band_is_never_passed_over(self):
        for name, group in self.banded_groups().items():
            for angle in self.ANGLES:
                head_angle = pycozmo.util.Angle(degrees=angle)
                if not any(member.matches_head_angle(head_angle) for member in group.members):
                    continue
                with self.subTest(group=name, angle=angle):
                    self.assertTrue(
                        all(member.matches_head_angle(head_angle)
                            for member in group.get_candidates(head_angle)))

    def test_the_one_group_that_does_not_cover_the_whole_range(self):
        # ag_memorymatch_cozmo_end_listen only holds low angle variants, since the head is down on
        # the cubes throughout Memory Match. Above 10 degrees the band gives way to playing
        # something rather than nothing.
        group = self.find_banded_group("anim_memorymatch_reacttopattern_01")
        self.assertEqual([(member.head_angle_min, member.head_angle_max)
                          for member in group.members],
                         [(-25.0, -10.0), (-10.0, 10.0)])
        candidates = group.get_candidates(pycozmo.util.Angle(degrees=40.0))
        self.assertEqual(len(candidates), 2)

    def test_the_chosen_animation_matches_the_head_angle(self):
        # ag_askforblock names each member after the angle it was authored for, so a mismatch is
        # plain to read. Before, all four were equally likely whatever the head was doing.
        group = self.find_banded_group("anim_reacttoblock_ask_01_0")
        expected = {
            -20.0: "anim_reacttoblock_ask_01_head_angle_-20",
            0.0: "anim_reacttoblock_ask_01_0",
            20.0: "anim_reacttoblock_ask_01_head_angle_20",
            40.0: "anim_reacttoblock_ask_01_head_angle_40",
        }
        for angle, name in expected.items():
            with self.subTest(angle=angle):
                chosen = {group.choose_member(pycozmo.util.Angle(degrees=angle)).name
                          for _ in range(20)}
                self.assertEqual(chosen, {name})
