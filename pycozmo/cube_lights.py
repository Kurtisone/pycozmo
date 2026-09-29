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

    "CubeLightPattern",

    "load_cube_light_animations",
]


#: Light frame length, in ms. Not measured: 30 ms is what fits the longest period in the resources, the five
#: seconds between two breaths of "Connected", in the byte a period has.
MS_PER_LIGHT_FRAME = 30


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


def _color(rgba: Sequence[int]) -> int:
    return lights.Color(rgb=(int(rgba[0]), int(rgba[1]), int(rgba[2]))).to_int16()


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
