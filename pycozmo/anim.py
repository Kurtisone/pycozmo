"""

Animation clip representation, reading, and preprocessing.

"""

import math
import os
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from PIL import Image
import numpy as np

from .logger import logger
from . import anim_encoder
from . import image_encoder
from . import lights
from . import procedural_face
from . import protocol_base
from . import protocol_encoder
from . import robot
from . import util
from .json_loader import find_file, load_json_file


__all__ = [
    "PreprocessedClip",
    "AnimationGroupMember",
    "AnimationGroup",

    "load_animation_groups",
    "load_cube_animation_groups",
    "load_backpack_light_patterns"
]


class PreprocessedClip(object):
    """ Preprocessed animation clip that can be played back. """

    def __init__(self, keyframes: Optional[Dict[int, List[protocol_encoder.Packet]]] = None):
        self.keyframes = keyframes or defaultdict(list)

    @classmethod
    def keyframe_to_im(cls, keyframe: anim_encoder.AnimProceduralFace) -> Image.Image:
        params = [keyframe.center_x, keyframe.center_y, keyframe.scale_x, keyframe.scale_y, keyframe.angle] + \
                 keyframe.left_eye + keyframe.right_eye
        face = procedural_face.ProceduralFace(params)
        im = face.render()
        # The Cozmo protocol expects a 128x32 image, so take only the even lines.
        np_im = np.array(im)
        np_im2 = np_im[::2]
        im = Image.fromarray(np_im2)
        return im

    @classmethod
    def from_anim_clip(cls, clip: anim_encoder.AnimClip,
                       audio_library: Optional[Any] = None) -> "PreprocessedClip":
        """
        Preprocess an animation clip into the packets that play it.

        The audio library, when given, resolves the WWise events the clip names into sound. Without
        one the animation plays silently, which is what happened before there was a library at all.
        """
        keyframes: Dict[int, List[protocol_encoder.Packet]] = defaultdict(list)
        for keyframe in clip.keyframes:
            if isinstance(keyframe, anim_encoder.AnimHeadAngle):
                # FIXME: Why can duration be larger than 255?
                pkt: protocol_base.Packet = protocol_encoder.AnimHead(
                    duration_ms=min(keyframe.duration_ms, 255),
                    variability_deg=keyframe.variability_deg,
                    angle_deg=keyframe.angle_deg)
                keyframes[keyframe.trigger_time_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimLiftHeight):
                # FIXME: Why can duration be larger than 255?
                pkt = protocol_encoder.AnimLift(duration_ms=min(keyframe.duration_ms, 255),
                                                variability_mm=keyframe.variability_mm,
                                                height_mm=keyframe.height_mm)
                keyframes[keyframe.trigger_time_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimRecordHeading):
                pkt = protocol_encoder.RecordHeading()
                keyframes[keyframe.trigger_time_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimTurnToRecordedHeading):
                pkt = protocol_encoder.TurnToRecordedHeading()
                keyframes[keyframe.trigger_time_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimBodyMotion):
                if keyframe.radius_mm == "STRAIGHT":
                    pkt = protocol_encoder.AnimBody(speed=keyframe.speed, unknown=32767)
                elif keyframe.radius_mm == "TURN_IN_PLACE":
                    pkt = protocol_encoder.TurnInPlaceAtSpeed(wheel_speed_mmps=keyframe.speed,
                                                              direction=math.copysign(1.0, keyframe.speed))
                else:
                    assert isinstance(keyframe.radius_mm, float)
                    vl = keyframe.speed * (keyframe.radius_mm - robot.TRACK_WIDTH.mm / 2.0)
                    vr = keyframe.speed * (keyframe.radius_mm + robot.TRACK_WIDTH.mm / 2.0)
                    pkt = protocol_encoder.DriveWheels(lwheel_speed_mmps=vl, rwheel_speed_mmps=vr)
                keyframes[keyframe.trigger_time_ms].append(pkt)
                pkt = protocol_encoder.DriveWheels()
                keyframes[keyframe.trigger_time_ms + keyframe.duration_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimBackpackLights):
                left = lights.Color(rgb=(keyframe.left.red, keyframe.left.green, keyframe.left.blue))
                front = lights.Color(rgb=(keyframe.front.red, keyframe.front.green, keyframe.front.blue))
                middle = lights.Color(rgb=(keyframe.middle.red, keyframe.middle.green, keyframe.middle.blue))
                back = lights.Color(rgb=(keyframe.back.red, keyframe.back.green, keyframe.back.blue))
                right = lights.Color(rgb=(keyframe.right.red, keyframe.right.green, keyframe.right.blue))
                pkt = protocol_encoder.AnimBackpackLights(colors=(left.to_int16(),
                                                                  front.to_int16(), middle.to_int16(), back.to_int16(),
                                                                  right.to_int16()))
                keyframes[keyframe.trigger_time_ms].append(pkt)
                off_light = lights.off.to_int16()
                pkt = protocol_encoder.AnimBackpackLights(colors=(off_light,
                                                                  off_light, off_light, off_light,
                                                                  off_light))
                keyframes[keyframe.trigger_time_ms + keyframe.duration_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimFaceAnimation):
                # TODO
                pass
            elif isinstance(keyframe, anim_encoder.AnimProceduralFace):
                im = cls.keyframe_to_im(keyframe)
                encoder = image_encoder.ImageEncoder(im)
                buf = bytes(encoder.encode())
                pkt = protocol_encoder.DisplayImage(image=buf)
                keyframes[keyframe.trigger_time_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimRobotAudio):
                if audio_library is not None:
                    cls._add_audio(keyframes, keyframe, audio_library)
            elif isinstance(keyframe, anim_encoder.AnimEvent):
                # TODO
                pass
            else:
                raise RuntimeError("Unexpected keyframe type '{}'".format(type(keyframe)))
        ppclip = cls(keyframes=keyframes)
        return ppclip

    @classmethod
    def _add_audio(cls, keyframes: Dict[int, List[protocol_encoder.Packet]],
                   keyframe: anim_encoder.AnimRobotAudio, audio_library: Any) -> None:
        """
        Lay a keyframe's sound out over the animation frames that follow its trigger.

        A keyframe can name several events, which play together on a real robot; the robot has one
        speaker and OutputAudio carries one frame, so the last one placed on a frame is the one
        heard. Frames are laid on the 33 ms animation grid rather than the 33.74 ms an OutputAudio
        frame actually lasts, so a long sound drifts about 2% late against its animation.
        """
        for event_id in keyframe.audio_event_ids:
            frames = audio_library.get_frames(event_id, keyframe.volume)
            if not frames:
                continue
            for i, pkt in enumerate(frames):
                keyframes[keyframe.trigger_time_ms + i * 33].append(pkt)


class LightAnimation:
    # TODO: create play method for light animations
    __slots__ = [
        "on_colors",
        "off_colors",
        "on_period",
        "off_period",
        "transition_on_period",
        "transition_off_period",
        "offset",
    ]

    def __init__(self,
                 on_colors: List[List],
                 off_colors: List[List],
                 on_period: List[int],
                 off_period: List[int],
                 transition_on_period: List[int],
                 transition_off_period: List[int],
                 offset: List[int]):
        self.on_colors = on_colors
        self.off_colors = off_colors
        self.on_period = on_period
        self.off_period = off_period
        self.transition_on_period = transition_on_period
        self.transition_off_period = transition_off_period
        self.offset = offset


class CubeAnimation(LightAnimation):
    __slots__ = [
        "duration",
        "rotation_period"
    ]

    def __init__(self, duration: int, rotation_period: int, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.duration = int(duration)
        self.rotation_period = int(rotation_period)

    @classmethod
    def from_json(cls, data: Dict) -> "CubeAnimation":
        return cls(on_colors=data['pattern']['onColors'],
                   off_colors=data['pattern']['offColors'],
                   on_period=data['pattern']['onPeriod_ms'],
                   off_period=data['pattern']['offPeriod_ms'],
                   transition_on_period=data['pattern']['transitionOnPeriod_ms'],
                   transition_off_period=data['pattern']['transitionOffPeriod_ms'],
                   offset=data['pattern']['offset'],
                   rotation_period=data['pattern']['rotationPeriod_ms'],
                   duration=data['duration_ms'])


class BackpackAnimation(LightAnimation):
    __slots__ = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def from_json(cls, data: Dict) -> "BackpackAnimation":
        return cls(on_colors=data['onColors'],
                   off_colors=data['offColors'],
                   on_period=data['onPeriod_ms'],
                   off_period=data['offPeriod_ms'],
                   transition_on_period=data['transitionOnPeriod_ms'],
                   transition_off_period=data['transitionOffPeriod_ms'],
                   offset=data['offset'])


class AnimationGroupMember:

    __slots__ = [
        "name",
        "weight",
        "cooldown_time",
        "mood",
        "use_head_angle",
        "head_angle_max",
        "head_angle_min",
        "last_played",
    ]

    def __init__(self,
                 name: str,
                 weight: float,
                 cooldown_time: float,
                 mood: str,
                 use_head_angle: bool = False,
                 head_angle_min: float = 0.0,
                 head_angle_max: float = 0.0) -> None:
        self.name = str(name)
        self.weight = float(weight)
        self.mood = str(mood)
        self.use_head_angle = bool(use_head_angle)
        # seconds
        self.cooldown_time = float(cooldown_time)
        # Degrees
        self.head_angle_min = float(head_angle_min)
        self.head_angle_max = float(head_angle_max)
        # When the member was last played, from time.perf_counter(). 0.0 until it is played once.
        self.last_played = 0.0

    def matches_head_angle(self, head_angle: util.Angle) -> bool:
        """ Whether the member suits a head angle. A member that declares no band suits any. """
        if not self.use_head_angle:
            return True
        return self.head_angle_min <= head_angle.degrees <= self.head_angle_max

    def is_on_cooldown(self, now: Optional[float] = None) -> bool:
        """ Whether the member was played too recently to be played again. """
        if not self.cooldown_time or not self.last_played:
            return False
        now = time.perf_counter() if now is None else now
        return now - self.last_played < self.cooldown_time

    def played(self, now: Optional[float] = None) -> None:
        """ Record the member as just played, which starts its cooldown. """
        self.last_played = time.perf_counter() if now is None else now

    @classmethod
    def from_json(cls, data: Dict) -> "AnimationGroupMember":
        return cls(name=data['Name'],
                   weight=data['Weight'],
                   cooldown_time=data['CooldownTime_Sec'],
                   mood=data['Mood'],
                   use_head_angle=data.get('UseHeadAngle', False),
                   head_angle_min=data.get('HeadAngleMin_Deg', 0.0),
                   head_angle_max=data.get('HeadAngleMax_Deg', 0.0))


class AnimationGroup:

    __slots__ = [
        "members",
    ]

    def __init__(self, members: Iterable[AnimationGroupMember]) -> None:
        self.members = list(members)

    @classmethod
    def from_json(cls, data: Dict) -> "AnimationGroup":
        animations = [AnimationGroupMember.from_json(a) for a in data['Animations']]
        return cls(animations)

    def get_candidates(self, head_angle: Optional[util.Angle] = None) -> List[AnimationGroupMember]:
        """
        Members that may be played right now.

        Some groups hold one animation per head angle band. The angle is baked into the animation -
        43 of the 507 groups are built that way - so playing the member that does not match the
        current angle makes the head jump. A member played less recently than its cooldown is
        skipped, unless skipping it would leave nothing to play.
        """
        candidates = self.members
        if head_angle is not None:
            suitable = [member for member in candidates if member.matches_head_angle(head_angle)]
            if suitable:
                candidates = suitable
        now = time.perf_counter()
        ready = [member for member in candidates if not member.is_on_cooldown(now)]
        return ready or candidates

    def choose_member(self, head_angle: Optional[util.Angle] = None) -> AnimationGroupMember:
        """ Choose a member by weight, among those that may be played right now. """
        candidates = self.get_candidates(head_angle)
        weights = [member.weight for member in candidates]
        weight_sum = sum(weights)
        if weight_sum:
            probabilities = [weight / weight_sum for weight in weights]
        else:
            # A group whose candidates all carry no weight is still playable.
            probabilities = [1.0 / len(candidates)] * len(candidates)
        member = candidates[np.random.choice(len(candidates), p=probabilities)]
        member.played()
        return member


def load_trigger_map(resource_dir: str, map_relative_path: str) -> Iterator[Tuple[str, str, Dict]]:
    json_data = load_json_file(os.path.join(resource_dir, map_relative_path))
    for pair in json_data['Pairs']:
        anim_file = find_file(resource_dir, pair['AnimName'] + '.json')
        if anim_file:
            yield pair['CladEvent'], pair['AnimName'], load_json_file(anim_file)


def load_animation_groups(resource_dir: str) -> Dict[str, AnimationGroup]:
    start_time = time.perf_counter()
    animation_groups = {}
    trigger_map_loader = load_trigger_map(resource_dir, os.path.join('cozmo_resources', 'assets',
                                                                     'animationGroupMaps', 'AnimationTriggerMap.json'))
    for evt, name, json_data in trigger_map_loader:
        animation_groups[evt] = AnimationGroup.from_json(json_data)
    logger.debug("Loaded {} animation groups in {:.02f} s.".format(
        len(animation_groups), time.perf_counter() - start_time))
    return animation_groups


def load_cube_animation_groups(resource_dir: str) -> Dict[str, List[CubeAnimation]]:
    start_time = time.perf_counter()
    cube_animation_groups: Dict[str, List[CubeAnimation]] = {}
    trigger_map_loader = load_trigger_map(resource_dir,
                                          os.path.join('cozmo_resources', 'assets',
                                                       'cubeAnimationGroupMaps', 'CubeAnimationTriggerMap.json'))
    for evt, name, json_data in trigger_map_loader:
        cube_animation_groups[evt] = []
        for cube_anim in json_data[name]:
            cube_animation_groups[evt].append(CubeAnimation.from_json(cube_anim))
    logger.debug("Loaded {} cube animation groups in {:.02f} s.".format(
        len(cube_animation_groups), time.perf_counter() - start_time))
    return cube_animation_groups


def load_backpack_light_patterns(resource_dir: str) -> Dict[str, BackpackAnimation]:
    backpack_light_patterns = {}
    json_data = load_json_file(os.path.join(resource_dir, 'cozmo_resources', 'config',
                               'engine', 'lights', 'backpackLights', 'backpackLightPatterns.json'))

    for key in json_data:
        backpack_light_patterns[key] = BackpackAnimation.from_json(json_data[key])
    return backpack_light_patterns
