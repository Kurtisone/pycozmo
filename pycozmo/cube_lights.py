"""

Light Cube light animations, from Cozmo's resources.

Anki's engine lit the cubes by trigger: "Connected" once a cube is connected - a dim cyan breath every five
seconds - "Visible" while the robot sees it, a steady cyan, and some forty more for its games and tricks.
cozmo_resources/assets/cubeAnimationGroupMaps/CubeAnimationTriggerMap.json names the animation for each
trigger, and cozmo_resources/config/engine/lights/cubeLights holds them: a sequence of patterns, each with a
duration, 0 for as long as nothing else is asked for.

A pattern gives each of the four lights an on and an off colour, how long each lasts, how long the fades
between them take, and when it starts, in ms; and how long the pattern takes to go round the four lights.
The robot takes all that in light frames, a byte each.

"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from . import lights
from . import protocol_encoder
from .json_loader import load_json_file, get_json_files


__all__ = [
    "MS_PER_LIGHT_FRAME",
    "STEADY_FRAMES",
    "CHANNEL_COST",
    "COLOR_BUDGET",

    "CubeLightPattern",

    "limit",
    "steady",
    "load_cube_light_animations",
]


#: Light frame length, in ms. Measured on a robot, filming a cube that blinked 30 frames on and 30 off: 33.2 and
#: 33.3 ms, from its rising and its falling edges - 30 frames a second. The five seconds between two breaths of
#: "Connected", the longest period in the resources, are 150 of them, which a period's byte holds.
MS_PER_LIGHT_FRAME = 1000.0 / 30.0
#: How many light frames a light that stays one colour is on for, in Anki's patterns: a second, 1000 ms, and off for
#: none. A cube given none at all, 0 frames on and 0 off, showed white as two lights, a yellow and a red.
STEADY_FRAMES = 30
#: How strong a colour a cube's four lights can show together. On a robot's cubes a colour of several channels, on all
#: four lights, lost channels when it was too strong: a white of 31 each came out as two yellow lights and two red, a
#: yellow of 31 and 24 the same, a cyan of 31 and 31 as two green and two cyan, a white of 21 each as a cross and one
#: of 26 as a little green; a white of 20 each, a yellow of 24 and 18, and a colour of one channel, at 31, were right.
#: What fits that is a cost that counts a channel's strength with a weight - green's the heaviest, then red's, then
#: blue's - against a budget: the yellow of 24 and 18 that was right costs 49, and one of 27 and 21, a cost of 56, had
#: one side a little green. The colours are scaled down to it, keeping their hue.
CHANNEL_COST = (1.0, 1.4, 0.7)
COLOR_BUDGET = 50.0


@dataclass(frozen=True)
class CubeLightPattern:
    """ One step of a cube light animation. """

    #: The four lights' states, as the robot takes them.
    states: Tuple[protocol_encoder.LightState, ...]
    #: How long the pattern takes to go round the four lights, in light frames; 0 for not at all.
    rotation_period_frames: int
    #: How long the step lasts, in seconds; 0 for as long as nothing else is asked for.
    duration: float

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "CubeLightPattern":
        pattern = data["pattern"]
        states = tuple(
            protocol_encoder.LightState(
                on_color=_color(pattern["onColors"][i]),
                off_color=_color(pattern["offColors"][i]),
                on_frames=_frames(pattern["onPeriod_ms"][i]),
                off_frames=_frames(pattern["offPeriod_ms"][i]),
                transition_on_frames=_frames(pattern["transitionOnPeriod_ms"][i]),
                transition_off_frames=_frames(pattern["transitionOffPeriod_ms"][i]),
                offset=_frames(pattern.get("offset", (0, 0, 0, 0))[i], 32767))
            for i in range(4))
        return cls(states=states, rotation_period_frames=_frames(pattern.get("rotationPeriod_ms", 0)),
                   duration=float(data.get("duration_ms", 0)) / 1000.0)


def limit(color: int) -> int:
    """
    A colour as a light takes it, 15 bits of 5 for red, green and blue, brought down to what a cube's four lights can
    show together: see COLOR_BUDGET. The hue is kept; the colours of one channel, the strongest, are not changed.
    """
    channels = [(color & lights.LED_ENC_RED) >> lights.LED_ENC_RED_SHIFT,
                (color & lights.LED_ENC_GREEN) >> lights.LED_ENC_GREEN_SHIFT,
                (color & lights.LED_ENC_BLUE) >> lights.LED_ENC_BLUE_SHIFT]
    cost = sum(weight * channel for weight, channel in zip(CHANNEL_COST, channels))
    if cost <= COLOR_BUDGET:
        return color
    scale = COLOR_BUDGET / cost
    red, green, blue = (int(channel * scale) for channel in channels)
    return (color & lights.LED_ENC_IR) | (red << lights.LED_ENC_RED_SHIFT) | (green << lights.LED_ENC_GREEN_SHIFT) | \
        (blue << lights.LED_ENC_BLUE_SHIFT)


def steady(color: lights.Color) -> protocol_encoder.LightState:
    """
    A cube light that stays one colour, as Anki's patterns for one are: on for a second, off for none. The colour is
    limited to what the cube's lights can show together: see limit().
    """
    value = limit(color.to_int16())
    return protocol_encoder.LightState(on_color=value, off_color=value, on_frames=STEADY_FRAMES, off_frames=0)


def _color(rgba: Sequence[int]) -> int:
    return limit(lights.Color(rgb=(int(rgba[0]), int(rgba[1]), int(rgba[2]))).to_int16())


def _frames(ms: float, limit: int = 255) -> int:
    # A period that is there at all is a frame at least: "Connected" lights up for 10 ms, and 0 can mean
    # for ever.
    frames = int(round(float(ms) / MS_PER_LIGHT_FRAME))
    if ms > 0:
        frames = max(frames, 1)
    return max(0, min(limit, frames))


def load_cube_light_animations(resource_dir: str) -> Dict[str, List[CubeLightPattern]]:
    """ Cube light animations by trigger. """
    base = os.path.join(resource_dir, "cozmo_resources")
    animations: Dict[str, List[CubeLightPattern]] = {}
    for filename in get_json_files(base, [os.path.join("config", "engine", "lights", "cubeLights")]):
        for name, steps in load_json_file(filename).items():
            animations[name] = [CubeLightPattern.from_json(step) for step in steps]
    trigger_map = load_json_file(os.path.join(base, "assets", "cubeAnimationGroupMaps",
                                              "CubeAnimationTriggerMap.json"))
    return {pair["CladEvent"]: animations[pair["AnimName"]] for pair in trigger_map["Pairs"]
            if pair["AnimName"] in animations}
