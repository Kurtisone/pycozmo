"""

Finding the charger's marker in the camera images.

The charger carries a marker of its own, on the face that looks the way the robot drives onto it: a dark ring, thin and
rounded, 1.4 times as wide as it is high, a white sticker around it, and inside it a battery drawn in dark lines, its
terminal to the right. The robot's camera sees it as a few pixels, dark on the charger's black plastic: 20 px wide from
35 cm, 45 from 16.

Where the cubes' markers are found as frames of dark pixels, this one is found as a picture. The marker is drawn at
every size it can have, and each is compared with the image - normalised, so that a dim room and a bright one match as
well - and the best match is where the marker is. Its four sides are then fitted one by one, from the dark of the ring
along them, and its corners are where the sides meet: from those, as for a cube's, comes where the marker is in space
and which way it faces.

The sizes are those of a charger measured with a ruler and, from the robot driving up to it in steps of 31 mm, with
the camera: the ring, along its middle, is 24.0 mm wide and 17.5 high. The drawing is from the same images.

"""

import functools
import math
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import camera
from . import marker_detection


__all__ = [
    "RING_WIDTH",
    "RING_HEIGHT",

    "ObservedCharger",

    "draw_marker",
    "observe_charger",
]


#: The marker's dark ring, along the middle of it, in mm.
RING_WIDTH = 24.0
RING_HEIGHT = 17.5

# The drawing, in its own pixels: the ring's middle is _RING_WIDTH wide.
_RING_WIDTH = 107.5
_RING_HEIGHT = _RING_WIDTH * RING_HEIGHT / RING_WIDTH
_CENTRE = (96.0, 73.0)
_SIZE = (192, 144)
_SUPERSAMPLE = 4
# What the template takes of the drawing: the white sticker and a little around it.
_CROP = (29, 20, 163, 126)

# The sizes the marker is looked for at, as the ring's width in pixels, one the next one's 1.09 times.
_MIN_WIDTH = 15.0
_MAX_WIDTH = 84.0
_STEP = 1.09
# And how much narrower than a marker seen squarely it may look, from a way round.
_SQUEEZES = (1.0, 0.8, 0.62)

#: How well the drawing has to match the image, 1 for a picture that is the template in every pixel, and how well the
#: battery in it has to match alone. The ring of a marker further than 50 cm away is under 15 px wide, and the battery
#: is not seen in that.
MIN_SCORE = 0.45
MIN_BATTERY = 0.4
# The battery's half width and half height, in the drawing's pixels.
_BATTERY = (31.0, 16.5)
# How far apart, in the ring's widths, two matches may be and still be one marker.
_SAME = 0.5
# How good a match seen squarely has to be for squeezed ones not to be looked for, how wide a marker is when it is
# looked for in a half size image, and how big a drawing can be.
_SQUARE_ENOUGH = 0.6
_HALF_FROM = 40
_MAX_TEMPLATE = 140
# How many matches are tried for a ring.
_TRIES = 6


@dataclass(frozen=True)
class ObservedCharger:
    """ The charger's marker seen in a camera image. """

    #: Corners of the ring in the image, along its middle, in pixels, clockwise from the top left one.
    corners: Tuple[Tuple[float, float], ...]
    #: How well it matched the drawing, up to 1.
    score: float
    #: Centre of the marker in the robot's frame, in mm: ahead of the origin, to its left, up from the ground.
    position: Tuple[float, float, float]
    #: The direction the marker faces, as a unit vector in the robot's frame.
    normal: Tuple[float, float, float]
    #: Distance from the camera, in mm.
    distance: float

    @property
    def facing(self) -> float:
        """ Which way the marker faces, as a heading in radians in the robot's frame: pi for one facing it squarely. """
        return math.atan2(self.normal[1], self.normal[0])


def draw_marker(width: float, squeeze: float = 1.0) -> np.ndarray:
    """
    The marker as it looks at a size: a picture of the sticker, in values from 0 to 1, with the ring's middle `width`
    pixels wide and then squeezed to `squeeze` times that, as it looks from a way round.
    """
    return _resized(_template(), width / _RING_WIDTH, squeeze)


def observe_charger(image: Image.Image, calibration: Optional[camera.CameraCalibration],
                    head_angle: float, pitch: float = 0.0, min_score: float = MIN_SCORE,
                    avoid: Sequence[np.ndarray] = ()) -> Optional[ObservedCharger]:
    """
    Find the charger's marker in a camera image and place it in the robot's frame, or say it is not there. head_angle
    and pitch are in radians, as for camera.ground_points(). Without a calibration, a typical one is used.

    `avoid` holds the corners of frames that are known to be something else, a cube's, which look enough like the marker
    to be taken for it.
    """
    gray = np.asarray(image.convert("L"), dtype=np.float64)
    calibration = (calibration or camera.DEFAULT_CALIBRATION).scaled(gray.shape[1], gray.shape[0])
    tried: List[Tuple[float, float, float, float, float]] = []
    for candidate in _candidates(gray, min_score):
        score, x, y, width, squeeze = candidate
        if any(abs(x - other[1]) < _SAME * other[3] and abs(y - other[2]) < _SAME * other[3] and
               0.7 < width / other[3] < 1.4 for other in tried) or _within(x, y, avoid):
            continue
        tried.append(candidate)
        fit = _align(gray, calibration, head_angle, pitch, x, y, width, squeeze)
        if fit is not None and fit[1] >= min_score and fit[2] >= MIN_BATTERY:
            return _observed(fit[0], fit[1], calibration, head_angle, pitch)
        if len(tried) >= _TRIES:
            break
    return None


def _within(x: float, y: float, frames: Sequence[np.ndarray]) -> bool:
    """ Whether a point is in the box round any of the frames, given by their corners. """
    for corners in frames:
        corners = np.asarray(corners, dtype=np.float64)
        low, high = corners.min(axis=0), corners.max(axis=0)
        margin = 0.15 * (high - low)
        if low[0] - margin[0] <= x <= high[0] + margin[0] and low[1] - margin[1] <= y <= high[1] + margin[1]:
            return True
    return False


def _candidates(gray: np.ndarray, min_score: float) -> List[Tuple[float, float, float, float, float]]:
    """
    Where the drawing matches the image at least as well as min_score, best first: the match's score, the x and y of its
    centre, the ring's width in pixels as drawn, and how much it was squeezed. The marker is looked for seen squarely
    first, and from a way round only where that finds nothing good.
    """
    search = _Search(gray)
    template = _template()
    candidates: List[Tuple[float, float, float, float, float]] = []
    for squeezes in (_SQUEEZES[:1], _SQUEEZES[1:]):
        width = _MIN_WIDTH
        while width <= _MAX_WIDTH:
            for squeeze in squeezes:
                match = search.best_match(_resized(template, width / _RING_WIDTH, squeeze))
                if match is not None and match[0] >= min_score:
                    candidates.append((match[0], match[1], match[2], width, squeeze))
            width *= _STEP
        if candidates and max(candidates)[0] >= _SQUARE_ENOUGH:
            break
    return sorted(candidates, reverse=True)


class _Search:
    """ An image prepared to have drawings looked for in it: its transform, and the sums of its values and squares. """

    def __init__(self, gray: np.ndarray) -> None:
        self.full = _Level(gray, 1)
        # Seen from a few pixels more, a bigger marker is found as well in an image a quarter the size.
        half = gray[:gray.shape[0] // 2 * 2, :gray.shape[1] // 2 * 2]
        half = (half[0::2, 0::2] + half[1::2, 0::2] + half[0::2, 1::2] + half[1::2, 1::2]) / 4.0
        self.half = _Level(half, 2)

    def best_match(self, template: np.ndarray) -> Optional[Tuple[float, float, float]]:
        """ Where in the image a template matches best, normalised: its score, and its centre's x and y. """
        if template.shape[1] >= _HALF_FROM:
            reduced = _shrunk(template)
            match = self.half.best_match(reduced)
            return None if match is None else (match[0], match[1] * 2.0 + 0.5, match[2] * 2.0 + 0.5)
        return self.full.best_match(template)


class _Level:
    """ An image at one size, with what is needed to correlate drawings with it. """

    def __init__(self, gray: np.ndarray, reduction: int) -> None:
        self.gray = gray
        rows, columns = gray.shape
        self.shape = (_fast_size(rows + _MAX_TEMPLATE // reduction), _fast_size(columns + _MAX_TEMPLATE // reduction))
        self.transform = np.fft.rfft2(gray, self.shape)
        self.sums = _integral(gray)
        self.square_sums = _integral(gray * gray)

    def best_match(self, template: np.ndarray) -> Optional[Tuple[float, float, float]]:
        rows, columns = self.gray.shape
        t_rows, t_columns = template.shape
        if t_rows >= rows or t_columns >= columns:
            return None
        zero_mean = template - template.mean()
        spread = float(np.sqrt((zero_mean ** 2).sum()))
        if spread < 1e-6:
            return None
        correlation = np.fft.irfft2(self.transform * np.conj(np.fft.rfft2(zero_mean, self.shape)), self.shape)
        correlation = correlation[:rows - t_rows + 1, :columns - t_columns + 1]
        count = float(t_rows * t_columns)
        total = _window(self.sums, t_rows, t_columns)
        variance = np.maximum(_window(self.square_sums, t_rows, t_columns) - total * total / count, 1e-9)
        score = correlation / (np.sqrt(variance) * spread)
        # Flat patches match anything: those the image hardly varies in do not count.
        score[variance < count * 4.0] = -1.0
        y, x = np.unravel_index(int(np.argmax(score)), score.shape)
        return float(score[y, x]), x + (t_columns - 1) / 2.0, y + (t_rows - 1) / 2.0


def _shrunk(template: np.ndarray) -> np.ndarray:
    """ A template half the size, to look for in an image half the size. """
    rows, columns = template.shape[0] // 2 * 2, template.shape[1] // 2 * 2
    t = template[:rows, :columns]
    result: np.ndarray = (t[0::2, 0::2] + t[1::2, 0::2] + t[0::2, 1::2] + t[1::2, 1::2]) / 4.0
    return result


def _integral(values: np.ndarray) -> np.ndarray:
    """ The sums of the values above and to the left of each point, with a row and a column of zeros before them. """
    integral: np.ndarray = np.pad(np.cumsum(np.cumsum(values, axis=0), axis=1), ((1, 0), (1, 0)))
    return integral


def _window(integral: np.ndarray, rows: int, columns: int) -> np.ndarray:
    """ The sum of the values in each window rows x columns that fits in the image. """
    window: np.ndarray = (integral[rows:, columns:] - integral[:-rows, columns:] - integral[rows:, :-columns] +
                          integral[:-rows, :-columns])
    return window


def _fast_size(n: int) -> int:
    """ The next size an FFT is quick at: a multiple of 16. """
    return (n + 15) // 16 * 16


def _observed(pose: np.ndarray, score: float, calibration: camera.CameraCalibration, head_angle: float,
              pitch: float) -> ObservedCharger:
    """ What a pose - the marker's centre in the robot's frame and the way it faces - looks like in the image. """
    centre = pose[:3]
    angle = pose[3]
    normal = np.array([math.cos(angle), math.sin(angle), 0.0])
    right, down = _axes(angle)
    half_w, half_h = RING_WIDTH / 2.0, RING_HEIGHT / 2.0
    corners = np.stack([_pixels(centre + a * right + b * down, calibration, head_angle, pitch)[0]
                        for a, b in ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h))])
    origin = camera.camera_to_robot(np.zeros(3), head_angle, pitch)
    return ObservedCharger(
        corners=tuple((float(u), float(v)) for u, v in corners), score=float(score),
        position=(float(centre[0]), float(centre[1]), float(centre[2])),
        normal=(float(normal[0]), float(normal[1]), float(normal[2])),
        distance=float(np.linalg.norm(centre - origin)))


def _axes(angle: float) -> Tuple[np.ndarray, np.ndarray]:
    """ The marker's right and its down, in the robot's frame, for a marker that faces the heading `angle`. """
    return np.array([-math.sin(angle), math.cos(angle), 0.0]), np.array([0.0, 0.0, -1.0])


def _pixels(points: np.ndarray, calibration: camera.CameraCalibration, head_angle: float, pitch: float
            ) -> Tuple[np.ndarray, np.ndarray]:
    """ Where points in the robot's frame show in the image, and whether they are ahead of the camera at all. """
    angle = head_angle + pitch
    c, s = math.cos(angle), math.sin(angle)
    origin_x, origin_z = camera._camera_position(angle)
    points = np.asarray(points, dtype=np.float64)
    dx, dz = points[..., 0] - origin_x, points[..., 2] - origin_z
    ahead = c * dx + s * dz
    up = -s * dx + c * dz
    visible = ahead > 10.0
    safe = np.where(visible, ahead, 1.0)
    u, v = calibration.distort(-points[..., 1] / safe, -up / safe)
    return np.stack([u, v], axis=-1), visible


@functools.lru_cache(maxsize=None)
def _template() -> np.ndarray:
    """ The marker drawn once, large, as the template every size is made from: zero mean, unit spread. """
    scale = _SUPERSAMPLE
    image = Image.new("L", (_SIZE[0] * scale, _SIZE[1] * scale), 0)
    draw = ImageDraw.Draw(image)

    def box(x0: float, y0: float, x1: float, y1: float, radius: float, value: int) -> None:
        draw.rounded_rectangle((x0 * scale, y0 * scale, x1 * scale, y1 * scale), radius=radius * scale, fill=value)

    half_w, half_h = _RING_WIDTH / 2.0, _RING_HEIGHT / 2.0
    cx, cy = _CENTRE
    thick = 2.5
    light, dark = 235, 15
    # The sticker, the ring, and what is inside it: the battery, its terminal, and its two cells.
    box(cx - half_w - 12.0, cy - half_h - 12.0, cx + half_w + 12.0, cy + half_h + 12.0, 9.0, light)
    box(cx - half_w - thick, cy - half_h - thick, cx + half_w + thick, cy + half_h + thick, 8.0, dark)
    box(cx - half_w + thick, cy - half_h + thick, cx + half_w - thick, cy + half_h - thick, 5.0, light)
    box(cx - 30.5, cy - 16.0, cx + 24.0, cy + 16.0, 4.0, dark)
    box(cx + 24.0, cy - 8.0, cx + 30.5, cy + 6.5, 1.5, dark)
    box(cx - 24.5, cy - 10.0, cx - 6.0, cy + 9.0, 1.5, light)
    box(cx + 1.0, cy - 10.0, cx + 19.5, cy + 9.0, 1.5, light)
    image = image.resize(_SIZE, Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(2.0))
    template = np.asarray(image, dtype=np.float64)[_CROP[1]:_CROP[3], _CROP[0]:_CROP[2]] / 255.0
    return template


def _resized(template: np.ndarray, scale: float, squeeze: float) -> np.ndarray:
    """ The template with the ring's width in pixels scaled, and then its width, to squeeze times that. """
    height = max(4, int(round(template.shape[0] * scale)))
    width = max(4, int(round(template.shape[1] * scale * squeeze)))
    image = Image.fromarray(np.clip(template * 255.0, 0, 255).astype(np.uint8))
    resampling = Image.Resampling.BOX if scale < 1.0 else Image.Resampling.BILINEAR
    return np.asarray(image.resize((width, height), resampling), dtype=np.float64) / 255.0


def _align(gray: np.ndarray, calibration: camera.CameraCalibration, head_angle: float, pitch: float,
           x: float, y: float, width: float, squeeze: float = 1.0) -> Optional[Tuple[np.ndarray, float, float]]:
    """
    Where the marker is, and which way it faces, from fitting the drawing to the image: the pose - centre in the robot's
    frame and heading - how well the drawing matches there, up to 1, and how well its battery alone does. It starts
    from the match found at pixel x, y, the ring `width` pixels wide, and goes where the drawing, as the camera would
    see it, matches the image better, using every pixel of the sticker. The marker stands upright, as the charger sits
    on the floor.
    """
    scale = width / _RING_WIDTH
    template = _resized(_template(), scale, 1.0)
    rows, columns = template.shape
    mm = RING_WIDTH / _RING_WIDTH / scale
    a = ((np.arange(columns) + 0.5) - columns / 2.0) * mm
    b = ((np.arange(rows) + 0.5) - rows / 2.0) * mm
    a, b = np.meshgrid(a, b)
    # Only the sticker counts: what is round it is the charger, or the room.
    inside = (np.abs(a) <= RING_WIDTH / 2.0 + 10.5 * RING_WIDTH / _RING_WIDTH) & \
             (np.abs(b) <= RING_HEIGHT / 2.0 + 10.5 * RING_WIDTH / _RING_WIDTH)
    a, b, template = a[inside], b[inside], template[inside]

    def cost(pose: np.ndarray) -> float:
        right, down = _axes(pose[3])
        points = pose[None, :3] + a[:, None] * right[None, :] + b[:, None] * down[None, :]
        pixels, visible = _pixels(points, calibration, head_angle, pitch)
        u, v = pixels[:, 0], pixels[:, 1]
        valid = visible & (u >= 0) & (v >= 0) & (u <= gray.shape[1] - 1.01) & (v <= gray.shape[0] - 1.01)
        if valid.sum() < 0.8 * len(a):
            return 2.0
        values = marker_detection._bilinear(gray, u[valid], v[valid])
        return 1.0 - _correlation(values, template[valid])

    xn, yn = calibration.undistort(np.array([x]), np.array([y]))
    depth = calibration.fx * RING_WIDTH / width
    centre = camera.camera_to_robot(np.array([xn[0] * depth, yn[0] * depth, depth]), head_angle, pitch)
    best: Optional[Tuple[float, np.ndarray]] = None
    # A marker facing the robot squarely is at heading pi from the way the robot looks at it; one that looks narrower
    # than that is turned from it by the angle the squeeze says, to one side or the other.
    facing = math.atan2(centre[1], centre[0]) + math.pi
    turn = math.acos(min(1.0, squeeze))
    for heading in (facing + turn, facing - turn, facing + 0.5 * turn, facing - 0.5 * turn, facing) if turn > 0.1 else \
            (facing, facing + 0.5, facing - 0.5):
        start = np.array([centre[0], centre[1], centre[2], heading])
        found, value = _minimise(cost, start, np.array([depth * 0.06, depth * 0.06, depth * 0.06, 0.25]))
        if best is None or value < best[0]:
            best = (value, found)
    if best is None or best[0] >= 1.0:
        return None
    # The battery drawn inside the ring is what tells the marker from a cube's, a face, or a texture that is dark and
    # light in the same places: how well it alone matches.
    pose = best[1]
    right, down = _axes(pose[3])
    points = pose[None, :3] + a[:, None] * right[None, :] + b[:, None] * down[None, :]
    pixels, _ = _pixels(points, calibration, head_angle, pitch)
    values = marker_detection._bilinear(gray, np.clip(pixels[:, 0], 0.0, gray.shape[1] - 1.01),
                                        np.clip(pixels[:, 1], 0.0, gray.shape[0] - 1.01))
    mm = RING_WIDTH / _RING_WIDTH
    battery = (np.abs(a) <= _BATTERY[0] * mm) & (np.abs(b) <= _BATTERY[1] * mm)
    return pose, 1.0 - best[0], _correlation(values[battery], template[battery])


def _correlation(values: np.ndarray, template: np.ndarray) -> float:
    """ How alike two sets of values are, as their correlation: 1 for the same up to brightness and contrast. """
    a = values - values.mean()
    b = template - template.mean()
    norm = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / norm) if norm > 1e-9 else 0.0


def _minimise(function: Callable[[np.ndarray], float], start: np.ndarray, steps: np.ndarray,
              iterations: int = 90) -> Tuple[np.ndarray, float]:
    """ The point where a function is least near a start, by Nelder and Mead's way, first steps along each axis. """
    n = len(start)
    simplex = [start.astype(np.float64)]
    for i in range(n):
        point = start.astype(np.float64).copy()
        point[i] += steps[i]
        simplex.append(point)
    values = [function(point) for point in simplex]
    for _ in range(iterations):
        order = np.argsort(values)
        simplex = [simplex[i] for i in order]
        values = [values[i] for i in order]
        if abs(values[-1] - values[0]) < 1e-5 and _ > 20:
            break
        centroid = np.mean(simplex[:-1], axis=0)
        reflected = centroid + (centroid - simplex[-1])
        value = function(reflected)
        if value < values[0]:
            expanded = centroid + 2.0 * (centroid - simplex[-1])
            expanded_value = function(expanded)
            simplex[-1], values[-1] = (expanded, expanded_value) if expanded_value < value else (reflected, value)
        elif value < values[-2]:
            simplex[-1], values[-1] = reflected, value
        else:
            contracted = centroid + 0.5 * (simplex[-1] - centroid)
            contracted_value = function(contracted)
            if contracted_value < values[-1]:
                simplex[-1], values[-1] = contracted, contracted_value
            else:
                simplex = [simplex[0]] + [simplex[0] + 0.5 * (point - simplex[0]) for point in simplex[1:]]
                values = [values[0]] + [function(point) for point in simplex[1:]]
    best = int(np.argmin(values))
    return simplex[best], float(values[best])
