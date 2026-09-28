"""

Animation clip representation, reading, and preprocessing.

"""

import glob
import math
import os
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, Iterator, List, NamedTuple, Optional, Tuple

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
    "MAX_MOVE_MS",
    "STRAIGHT",
    "TURN_IN_PLACE",

    "Move",
    "split_moves",
    "PreprocessedClip",
    "load_face_animation",
    "AnimationGroupMember",
    "AnimationGroup",

    "load_animation_groups",
    "load_cube_animation_groups",
    "load_backpack_light_patterns"
]


#: Longest head or lift move one AnimHead or AnimLift can carry: its duration is a byte, in ms.
MAX_MOVE_MS = 255

#: AnimBody's curvature radius for driving straight ahead.
STRAIGHT = 32767
#: AnimBody's curvature radius for turning in place. Speed is then in degrees per second.
TURN_IN_PLACE = 0


class Move(NamedTuple):
    """ A head or lift keyframe: reach target, in degrees or mm, duration_ms after trigger_ms. """
    trigger_ms: int
    duration_ms: int
    target: float
    variability: int


def split_moves(moves: List[Move], start: float) -> Iterator[Tuple[int, int, int, int]]:
    """
    Lay head or lift moves out as the robot can take them, as (time, duration, target, variability).

    One AnimHead or AnimLift moves for at most MAX_MOVE_MS, and a longer keyframe was sent as one, so
    the robot made the whole move in 255 ms: one in eight of Anki's head keyframes, and up to 18 times
    too fast. A longer move goes out in equal pieces instead, each to where the move should be when
    it ends. Measured on a robot, four pieces of 250 ms took the head from -20 to +20 degrees at an
    even pace in 0.93 s.

    Where each piece aims depends on where the move starts: where the last one ended, and for the
    first, start - where the head or lift is when the animation plays. A keyframe that starts before
    the last has finished takes over from wherever that one has got to.
    """
    position = start
    for i, move in enumerate(moves):
        following = moves[i + 1].trigger_ms if i + 1 < len(moves) else None
        if move.duration_ms <= 0:
            yield move.trigger_ms, 0, int(round(move.target)), move.variability
            position = move.target
            continue
        pieces = math.ceil(move.duration_ms / MAX_MOVE_MS)
        bounds = [move.trigger_ms + round(j * move.duration_ms / pieces) for j in range(pieces + 1)]
        for j in range(pieces):
            if following is not None and bounds[j] >= following:
                break
            fraction = (bounds[j + 1] - move.trigger_ms) / move.duration_ms
            target = position + (move.target - position) * fraction
            yield bounds[j], min(bounds[j + 1] - bounds[j], MAX_MOVE_MS), int(round(target)), \
                move.variability if j == pieces - 1 else 0
        if following is not None and following < move.trigger_ms + move.duration_ms:
            position += (move.target - position) * (following - move.trigger_ms) / move.duration_ms
        else:
            position = move.target


class PreprocessedClip(object):
    """ Preprocessed animation clip that can be played back. """

    def __init__(self, keyframes: Optional[Dict[int, List[protocol_encoder.Packet]]] = None,
                 head_moves: Optional[List[Move]] = None, lift_moves: Optional[List[Move]] = None):
        self.keyframes = keyframes or defaultdict(list)
        # Head and lift moves are kept as they are and only turned into packets when the clip plays,
        # since a long first move can only be split knowing where the head or lift starts from.
        self.head_moves: List[Move] = head_moves or []
        self.lift_moves: List[Move] = lift_moves or []

    def motion_keyframes(self, head_angle_deg: float,
                         lift_height_mm: float) -> Dict[int, List[protocol_encoder.Packet]]:
        """ The head and lift moves as packets, from where the head and lift are now. """
        keyframes: Dict[int, List[protocol_encoder.Packet]] = defaultdict(list)
        for time_ms, duration, angle, variability in split_moves(self.head_moves, head_angle_deg):
            keyframes[time_ms].append(protocol_encoder.AnimHead(
                duration_ms=duration, variability_deg=variability, angle_deg=max(-128, min(127, angle))))
        for time_ms, duration, height, variability in split_moves(self.lift_moves, lift_height_mm):
            keyframes[time_ms].append(protocol_encoder.AnimLift(
                duration_ms=duration, variability_mm=variability, height_mm=max(0, min(255, height))))
        return keyframes

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
                       audio_library: Optional[Any] = None,
                       face_animation_dir: Optional[str] = None) -> "PreprocessedClip":
        """
        Preprocess an animation clip into the packets that play it.

        The audio library, when given, resolves the WWise events the clip names into sound. Without
        one the animation plays silently, which is what happened before there was a library at all.
        face_animation_dir, when given, is where the image sequences the clip names are found - see
        load_face_animation(). Without it those keyframes show nothing.
        """
        keyframes: Dict[int, List[protocol_encoder.Packet]] = defaultdict(list)
        head_moves: List[Move] = []
        lift_moves: List[Move] = []
        body_motions: List[anim_encoder.AnimBodyMotion] = []
        face_animations: List[anim_encoder.AnimFaceAnimation] = []
        pkt: protocol_base.Packet
        for keyframe in clip.keyframes:
            if isinstance(keyframe, anim_encoder.AnimHeadAngle):
                head_moves.append(Move(keyframe.trigger_time_ms, keyframe.duration_ms,
                                       keyframe.angle_deg, keyframe.variability_deg))
            elif isinstance(keyframe, anim_encoder.AnimLiftHeight):
                lift_moves.append(Move(keyframe.trigger_time_ms, keyframe.duration_ms,
                                       keyframe.height_mm, keyframe.variability_mm))
            elif isinstance(keyframe, anim_encoder.AnimRecordHeading):
                pkt = protocol_encoder.RecordHeading()
                keyframes[keyframe.trigger_time_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimTurnToRecordedHeading):
                pkt = protocol_encoder.TurnToRecordedHeading()
                keyframes[keyframe.trigger_time_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimBodyMotion):
                body_motions.append(keyframe)
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
                face_animations.append(keyframe)
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
        cls._add_body_motions(keyframes, body_motions)
        # Last, so that on a frame with a procedural face too, the image sequence is what shows: the
        # robot has one screen, and play_anim_ppclip() keeps the last image laid on a frame.
        for face_animation in face_animations:
            if face_animation_dir is None:
                continue
            images = load_face_animation(os.path.join(face_animation_dir, face_animation.anim_name))
            if not images:
                logger.warning("Face animation '{}' not found.".format(face_animation.anim_name))
            for i, image in enumerate(images):
                keyframes[face_animation.trigger_time_ms + i * robot.FRAME_MS].append(image)
        head_moves.sort(key=lambda move: move.trigger_ms)
        lift_moves.sort(key=lambda move: move.trigger_ms)
        ppclip = cls(keyframes=keyframes, head_moves=head_moves, lift_moves=lift_moves)
        return ppclip

    @classmethod
    def _add_body_motions(cls, keyframes: Dict[int, List[protocol_encoder.Packet]],
                          motions: List[anim_encoder.AnimBodyMotion]) -> None:
        """
        Lay the body motions out as AnimBody, which the robot takes with the keyframe's own speed and
        radius - see its declaration. Arcs used to go out as DriveWheels with each wheel at the speed
        times a radius, a hundred to a million mm/s, and turns in place as TurnInPlaceAtSpeed with the
        keyframe's degrees per second taken for mm/s, which the robot answers with a jolt.

        A motion stops when it ends, unless the next one has taken over by then.
        """
        motions = sorted(motions, key=lambda motion: motion.trigger_time_ms)
        for i, motion in enumerate(motions):
            if motion.radius_mm == "STRAIGHT":
                radius = STRAIGHT
            elif motion.radius_mm == "TURN_IN_PLACE":
                radius = TURN_IN_PLACE
            else:
                assert isinstance(motion.radius_mm, float)
                # 0 would be read as a turn in place, and 32767 as straight ahead.
                radius = int(math.copysign(max(1, min(STRAIGHT - 1, round(abs(motion.radius_mm)))),
                                           motion.radius_mm))
            speed = max(-32768, min(32767, int(round(motion.speed))))
            keyframes[motion.trigger_time_ms].append(
                protocol_encoder.AnimBody(speed=speed, curvature_radius_mm=radius))
            end = motion.trigger_time_ms + motion.duration_ms
            if i + 1 < len(motions) and motions[i + 1].trigger_time_ms <= end:
                continue
            keyframes[end].append(protocol_encoder.AnimBody(speed=0, curvature_radius_mm=STRAIGHT))

    @classmethod
    def _add_audio(cls, keyframes: Dict[int, List[protocol_encoder.Packet]],
                   keyframe: anim_encoder.AnimRobotAudio, audio_library: Any) -> None:
        """
        Lay a keyframe's sound out over the animation frames that follow its trigger.

        A keyframe can name several events, which play together on a real robot; the robot has one
        speaker and OutputAudio carries one frame, so the last one placed on a frame is the one
        heard. One frame holds 744 samples, which is 33.74 ms of sound at the speaker's rate but
        goes out on a 33.33 ms animation frame, so the robot is handed sound about 1 % faster than
        it plays it and a long one ends a little behind its animation.
        """
        for event_id in keyframe.audio_event_ids:
            frames = audio_library.get_frames(event_id, keyframe.volume)
            if not frames:
                continue
            for i, pkt in enumerate(frames):
                keyframes[keyframe.trigger_time_ms + i * robot.FRAME_MS].append(pkt)


def load_face_animation(directory: str) -> List[protocol_encoder.DisplayImage]:
    """
    Read a face animation: a directory of PNG images, one per animation frame, in name order.

    Anki drew them at 128 x 64 in greyscale, each line doubled; the screen is 128 x 32 and one bit
    deep, so every other line is kept, and a pixel is lit from half grey up.

    Two of the robot's animations show one, anim_bored_event_02 and anim_bored_event_04, and until
    these were read their faces stayed still - or black - while their sound played.
    """
    images = []
    for fspec in sorted(glob.glob(os.path.join(directory, "*.png"))):
        with Image.open(fspec) as im:
            pixels = np.asarray(im.convert("L"))[::2] >= 128
        encoder = image_encoder.ImageEncoder(Image.fromarray(pixels).convert("1"))
        images.append(protocol_encoder.DisplayImage(image=bytes(encoder.encode())))
    return images


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
