"""

Marker detection in the camera images.

Each side of a Light Cube carries a marker: a symbol inside a dark square frame with rounded corners, on
the cube's white plastic. The robot does not look for them; the application does, as Anki's engine did on
the phone. This finds the frames in an image, places each in space - where its centre is and which way it
faces, in the robot's frame - and tells which of the three cubes' symbols it holds.

A frame is found as a ring of dark pixels. Pixels are dark when they are darker than their surroundings,
so that the frame is found in a dim room as in a bright one. A ring of the right shape - no bigger than
most of the image, and not cut off by its edge - has its outline traced: each of its four sides is fitted
with a straight line through the pixels along the middle of that side, away from the rounded corners, and
the corners are where the lines meet. That puts them at the corners of the square the frame's straight
edges make, to a fraction of a pixel. The square is then straightened out, and only kept if it looks like
a marker: a dark ring along its edge and a light margin just inside.

Where a frame is follows from its corners, the camera's calibration and its size, MARKER_SIZE, 25 mm on the
stickers. Anki's own engine, through its SDK, placed two cubes filmed from head angles of -19 to 0 degrees at
the distances pycozmo gives them with 24.5 mm: within 2%. A cube filmed from four head angles stayed where it
stood with a standard deviation of 0.27 mm. Which way the frame faces is less sure: up to 10 degrees off in
those images.

The symbol is told by comparing the straightened square with Anki's drawings of the three, in the
cube_markers directory, each turned four ways. The stickers are not quite the drawings: the symbol sits
higher, by 4.5% of the side, over a bar that the drawings do not have, and its strokes are thicker. So the
drawings are raised as much, both are blurred a little, and only the middle of the square is compared,
above the bar. Checked against Anki's own engine, through its SDK, which saw a Paperclip and an Anglepoise
Lamp in each of 165 images: each frame found of them came out as the cube Anki saw there, the right way up,
at a likeness of 0.87 or more, and nothing else came closer than 0.45; so did a Deli Slicer in 16 other
images.

"""

import functools
import importlib.resources
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from PIL import Image

from . import camera
from . import protocol_encoder


__all__ = [
    "MARKER_SIZE",
    "CUBE_MARKERS",

    "ObservedMarker",

    "find_frames",
    "rectify",
    "frame_pose",
    "identify",
    "observe_markers",
]


#: Side of the square a cube marker's frame makes, in mm, corner to corner of its straight edges: measured on
#: the stickers. It was 27.0, from where the ground model placed a cube; Anki's engine placed cubes 9% nearer,
#: and the stickers settled it.
MARKER_SIZE = 25.0

#: The symbol on each Light Cube, as Anki named it, by the cube's object type. Their drawings are in the
#: cube_markers directory, as the cube's number: Anki's, cut from its reference sheet for the stickers.
CUBE_MARKERS = {
    protocol_encoder.ObjectType.Block_LIGHTCUBE1: "Paperclip",
    protocol_encoder.ObjectType.Block_LIGHTCUBE2: "Anglepoise Lamp",
    protocol_encoder.ObjectType.Block_LIGHTCUBE3: "Deli Slicer",
}

#: Smallest frame looked for, in pixels. A marker 27 mm wide is 12 pixels wide at about 670 mm.
MIN_FRAME_SIZE = 12

# Neighbourhood a pixel's brightness is compared with, as a radius in pixels, and how much darker than it a
# pixel has to be to count as dark: a fraction of its brightness, and at least a few levels.
_DARK_RADIUS = 12
_DARK_FRACTION = 0.12
_DARK_MIN = 6.0

# Across the straightened frame, where its dark ring and the light margin inside it are, as fractions of
# its side. Anki's frame is about a tenth of the side thick.
_RING = (0.01, 0.07)
_MARGIN = (0.12, 0.18)
# Just outside it, the cube's plastic, as fractions of its side outwards. The frame sits a little above the
# middle of the cube's side, and the bevelled top edge, darker, comes close.
_AROUND = (0.03, 0.09)
_MIN_CONTRAST = 15.0

# The side the square is straightened to for telling the symbol, in pixels; the part of it compared, as
# fractions of the side, away from the margin and the bar under the symbol; how much it and the drawings are
# blurred first, as a standard deviation in those pixels; and how much higher than in the drawings the
# symbol sits on the stickers, as a fraction of the side.
_SYMBOL_SIZE = 32
_SYMBOL = (0.25, 0.72)
_SYMBOL_BLUR = 0.7
_SYMBOL_RAISE = 0.045
# The inside of the frame, as fractions of the side: what is raised.
_INSIDE = (0.14, 0.86)
# How alike the square and the closest drawing have to be, as a correlation, and how much less alike the
# closest drawing of another cube has to be.
_MIN_LIKENESS = 0.5
_MIN_MARGIN = 0.15
# How much the symbol has to stand out, as its standard deviation over the frame's contrast with the margin.
_MIN_SPREAD = 0.1


@dataclass(frozen=True)
class ObservedMarker:
    """ A marker frame seen in a camera image. """

    #: Corners in the image, in pixels, clockwise on the screen from the top left one.
    corners: Tuple[Tuple[float, float], ...]
    #: Centre of the marker in the robot's frame, in mm: ahead of the origin, to its left, up from the ground.
    position: Tuple[float, float, float]
    #: The direction the marker faces, as a unit vector in the robot's frame.
    normal: Tuple[float, float, float]
    #: Distance from the camera, in mm.
    distance: float
    #: The cube whose symbol it holds, one of CUBE_MARKERS; None for a frame that holds none of theirs.
    cube: Optional[protocol_encoder.ObjectType] = None
    #: How the symbol is turned from the way it is drawn: quarter turns, clockwise on the screen. The
    #: Paperclip reads almost the same upside down, so its own can be two off.
    turns: int = 0

    @property
    def facing(self) -> float:
        """
        Which way the marker faces, as a heading in radians in the robot's frame: pi for a marker that faces
        the robot squarely, and less or more as it turns one way or the other.
        """
        return math.atan2(self.normal[1], self.normal[0])


def find_frames(image: np.ndarray, calibration: Optional[camera.CameraCalibration] = None,
                min_size: int = MIN_FRAME_SIZE) -> List[np.ndarray]:
    """
    Find the marker frames in a greyscale image, given as an array of its rows.

    Returns each frame's corners as a 4 x 2 array of pixel coordinates, x and y, clockwise on the screen
    from the top left one. With the camera's calibration, the lens' distortion, which bends the frame's
    straight sides, is taken out before they are fitted.
    """
    gray = np.asarray(image, dtype=np.float64)
    height, width = gray.shape
    mean = _box_mean(gray, _DARK_RADIUS)
    dark = gray < mean - np.maximum(_DARK_MIN, _DARK_FRACTION * mean)
    frames = []
    for runs in _components(dark):
        rows = [row for row, _, _ in runs]
        top, bottom = min(rows), max(rows)
        left = min(start for _, start, _ in runs)
        right = max(end for _, _, end in runs)
        box_width, box_height = right - left + 1, bottom - top + 1
        if box_width < min_size or box_height < min_size or box_width > 0.9 * width or box_height > 0.9 * height:
            continue
        # A frame cut off by the edge of the image does not show its corners.
        if top == 0 or left == 0 or bottom == height - 1 or right == width - 1:
            continue
        if not 0.4 < box_width / box_height < 2.5:
            continue
        # A ring fills part of its box, and not its middle.
        fill = sum(end - start + 1 for _, start, end in runs) / (box_width * box_height)
        if not 0.15 < fill < 0.8:
            continue
        middle_row, middle_column = (top + bottom) // 2, (left + right) // 2
        if any(row == middle_row and start <= middle_column <= end for row, start, end in runs):
            continue
        ends = [(start, row) for row, start, _ in runs] + [(end, row) for row, _, end in runs]
        outline = _quadrilateral(_convex_hull(ends))
        if outline is None:
            continue
        corners = _fit_sides(outline, _outer_boundary(runs, left, top, box_width, box_height))
        if corners is None:
            continue
        corners = _clockwise(corners)
        if not _looks_like_a_marker(gray, corners):
            continue
        frames.append(_refine_sides(gray, corners, calibration))
    return frames


def rectify(image: np.ndarray, corners: np.ndarray, size: int = 32, border: float = 0.0) -> np.ndarray:
    """
    The quadrilateral with these corners, clockwise from the top left, straightened into a square, and a
    border around it, as a fraction of its side.
    """
    gray = np.asarray(image, dtype=np.float64)
    h = _homography(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)), corners)
    t = -border + (np.arange(size) + 0.5) / size * (1.0 + 2.0 * border)
    xs, ys = np.meshgrid(t, t)
    p = h @ np.stack([xs.ravel(), ys.ravel(), np.ones(size * size)])
    square: np.ndarray = _bilinear(gray, p[0] / p[2], p[1] / p[2]).reshape(size, size)
    return square


def frame_pose(corners: np.ndarray, calibration: camera.CameraCalibration,
               size: float = MARKER_SIZE) -> Tuple[np.ndarray, np.ndarray]:
    """
    Where a frame is in the camera's frame - x to the right, y down, z ahead, in mm: the rotation that takes
    the marker's own axes there, and its centre.

    The marker's own axes are x to the right and y down its face, as it shows on the screen from the top left
    corner, and z into it. The corners are clockwise from the top left one.
    """
    x, y = calibration.undistort(corners[:, 0], corners[:, 1])
    half = size / 2.0
    square = ((-half, -half), (half, -half), (half, half), (-half, half))
    h = _homography(square, np.stack([x, y], axis=1))
    h1, h2, h3 = h[:, 0], h[:, 1], h[:, 2]
    scale = 2.0 / (np.linalg.norm(h1) + np.linalg.norm(h2))
    r1, r2, t = scale * h1, scale * h2, scale * h3
    if t[2] < 0:
        r1, r2, t = -r1, -r2, -t
    u, _, vt = np.linalg.svd(np.stack([r1, r2, np.cross(r1, r2)], axis=1))
    return u @ vt, t


def identify(image: np.ndarray, corners: np.ndarray) -> Optional[Tuple[protocol_encoder.ObjectType, int]]:
    """
    Tell which cube's symbol the frame with these corners holds in a greyscale image, and how it is turned:
    quarter turns, clockwise on the screen, from the way it is drawn. None for a frame that holds none of
    theirs, or that looks too much like two of them.
    """
    square = rectify(image, corners, _SYMBOL_SIZE)
    values = _symbol(square)
    # A frame with nothing inside is only noise there, which by chance can look like one of the drawings.
    t = (np.arange(_SYMBOL_SIZE) + 0.5) / _SYMBOL_SIZE
    edge = np.minimum.outer(np.minimum(t, 1.0 - t), np.minimum(t, 1.0 - t))
    contrast = float(np.median(square[(edge > _MARGIN[0]) & (edge < _MARGIN[1])]) -
                     np.median(square[(edge > _RING[0]) & (edge < _RING[1])]))
    if values.std() < _MIN_SPREAD * contrast:
        return None
    vector = _unit(values)
    likeness = {key: float(vector @ drawing) for key, drawing in _drawings().items()}
    (cube, turns), best = max(likeness.items(), key=lambda item: item[1])
    closest_other = max(value for (other, _), value in likeness.items() if other != cube)
    if best < _MIN_LIKENESS or best - closest_other < _MIN_MARGIN:
        return None
    return cube, turns


def observe_markers(image: Image.Image, calibration: Optional[camera.CameraCalibration],
                    head_angle: float, pitch: float = 0.0) -> List[ObservedMarker]:
    """
    Find the marker frames in a camera image, place them in the robot's frame, and tell their cubes.

    head_angle and pitch are in radians, as for camera.ground_points(). Without a calibration, a typical one
    is used.
    """
    gray = np.asarray(image.convert("L"), dtype=np.float64)
    calibration = (calibration or camera.DEFAULT_CALIBRATION).scaled(gray.shape[1], gray.shape[0])
    origin = camera.camera_to_robot(np.zeros(3), head_angle, pitch)
    observed = []
    for corners in find_frames(gray, calibration):
        rotation, centre = frame_pose(corners, calibration)
        position = camera.camera_to_robot(centre, head_angle, pitch)
        # The marker faces out of its face: against its z axis.
        normal = camera.camera_to_robot(-rotation[:, 2], head_angle, pitch) - origin
        cube, turns = identify(gray, corners) or (None, 0)
        observed.append(ObservedMarker(
            corners=tuple((float(u), float(v)) for u, v in corners),
            position=(float(position[0]), float(position[1]), float(position[2])),
            normal=(float(normal[0]), float(normal[1]), float(normal[2])),
            distance=float(np.linalg.norm(centre)),
            cube=cube, turns=turns))
    return observed


@functools.lru_cache(maxsize=None)
def _drawings() -> Dict[Tuple[protocol_encoder.ObjectType, int], np.ndarray]:
    """ Each cube's drawing, turned each way, as compared: see _symbol(). """
    drawings = {}
    for cube in CUBE_MARKERS:
        resource = importlib.resources.files(__package__).joinpath("cube_markers", "{}.png".format(cube.value))
        with resource.open("rb") as f:
            drawing = np.asarray(Image.open(f).convert("L"), dtype=np.float64)
        sticker = Image.fromarray(_raise_symbol(drawing).astype(np.uint8))
        pixels = np.asarray(sticker.resize((_SYMBOL_SIZE, _SYMBOL_SIZE), Image.Resampling.BOX), dtype=np.float64)
        for turns in range(4):
            # rot90() turns anticlockwise as the rows show on the screen.
            drawings[(cube, turns)] = _unit(_symbol(np.rot90(pixels, -turns)))
    return drawings


def _raise_symbol(drawing: np.ndarray) -> np.ndarray:
    """ A drawing as the stickers have it: what is inside the frame moved up, the margin's colour below. """
    side = drawing.shape[0]
    inside = slice(round(_INSIDE[0] * side), round(_INSIDE[1] * side))
    shift = round(_SYMBOL_RAISE * side)
    region = drawing[inside, inside]
    raised = np.full_like(region, float(np.median(region)))
    raised[:-shift] = region[shift:]
    sticker = drawing.copy()
    sticker[inside, inside] = raised
    return sticker


def _symbol(square: np.ndarray) -> np.ndarray:
    """ The middle of a straightened square, blurred, as a vector. """
    blur = _blur_matrix(square.shape[0], _SYMBOL_BLUR)
    t = (np.arange(square.shape[0]) + 0.5) / square.shape[0]
    middle = (t > _SYMBOL[0]) & (t < _SYMBOL[1])
    values: np.ndarray = (blur @ square @ blur.T)[np.ix_(middle, middle)].ravel()
    return values


def _unit(values: np.ndarray) -> np.ndarray:
    """ Values less their mean, scaled to unit length. """
    centred = values - values.mean()
    norm = np.linalg.norm(centred)
    vector: np.ndarray = centred / norm if norm else centred
    return vector


@functools.lru_cache(maxsize=None)
def _blur_matrix(size: int, sigma: float) -> np.ndarray:
    """ The matrix that blurs each column of a square of this side by a Gaussian of this deviation. """
    offsets = np.arange(size)[:, None] - np.arange(size)[None, :]
    weights = np.exp(-0.5 * (offsets / sigma) ** 2)
    matrix: np.ndarray = weights / weights.sum(axis=1, keepdims=True)
    return matrix


def _box_mean(gray: np.ndarray, radius: int) -> np.ndarray:
    """ The mean of each pixel's neighbourhood of (2 radius + 1) squared, through an integral image. """
    padded = np.pad(gray, radius + 1, mode="edge")
    integral = padded.cumsum(axis=0).cumsum(axis=1)
    side = 2 * radius + 1
    total = integral[side:, side:] - integral[:-side, side:] - integral[side:, :-side] + integral[:-side, :-side]
    mean: np.ndarray = total[:gray.shape[0], :gray.shape[1]] / (side * side)
    return mean


Run = Tuple[int, int, int]
Points = Union[Sequence[Sequence[float]], np.ndarray]


def _components(mask: np.ndarray) -> List[List[Run]]:
    """ The 8-connected components of a mask, each as its runs: row, first and last column. """
    padded = np.zeros((mask.shape[0], mask.shape[1] + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    steps = np.diff(padded, axis=1)
    runs: List[Run] = []
    for row in range(mask.shape[0]):
        starts = np.flatnonzero(steps[row] == 1)
        ends = np.flatnonzero(steps[row] == -1) - 1
        runs += [(row, int(start), int(end)) for start, end in zip(starts, ends)]

    parent = list(range(len(runs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    previous: List[int] = []
    current: List[int] = []
    current_row = -1
    for i, (row, start, end) in enumerate(runs):
        if row != current_row:
            previous = current if row == current_row + 1 else []
            current = []
            current_row = row
        for j in previous:
            _, other_start, other_end = runs[j]
            if other_end >= start - 1 and other_start <= end + 1:
                a, b = find(i), find(j)
                if a != b:
                    parent[a] = b
        current.append(i)

    groups: Dict[int, List[Run]] = {}
    for i, run in enumerate(runs):
        groups.setdefault(find(i), []).append(run)
    return list(groups.values())


def _convex_hull(points: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """ The convex hull of points, by Andrew's monotone chain. """
    points = sorted(set(points))
    if len(points) < 3:
        return list(points)

    def cross(o: Tuple[int, int], a: Tuple[int, int], b: Tuple[int, int]) -> int:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: List[Tuple[int, int]] = []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: List[Tuple[int, int]] = []
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _quadrilateral(hull: List[Tuple[int, int]]) -> Optional[np.ndarray]:
    """
    Four points of a convex hull that outline it: the two furthest apart, and on either side of the line
    through them, the furthest from it.
    """
    if len(hull) < 4:
        return None
    points = np.array(hull, dtype=np.float64)
    distances = ((points[:, None, :] - points[None, :, :]) ** 2).sum(axis=-1)
    i, j = np.unravel_index(np.argmax(distances), distances.shape)
    a, b = points[i], points[j]
    side = (b[0] - a[0]) * (points[:, 1] - a[1]) - (b[1] - a[1]) * (points[:, 0] - a[0])
    k, m = int(np.argmax(side)), int(np.argmin(side))
    if side[k] <= 0 or side[m] >= 0:
        return None
    # In the hull's order, so that consecutive points make a side.
    return points[sorted({int(i), int(j), k, m})]


def _outer_boundary(runs: List[Run], left: int, top: int, width: int, height: int) -> np.ndarray:
    """ The pixels of a component that touch the background around it - not its hole - as x, y. """
    inside = np.zeros((height + 2, width + 2), dtype=bool)
    for row, start, end in runs:
        inside[row - top + 1, start - left + 1:end - left + 2] = True
    # The background around it is what can be reached from the edge of its box.
    around = np.zeros_like(inside)
    around[0, :] = around[-1, :] = around[:, 0] = around[:, -1] = True
    around &= ~inside
    while True:
        grown = _neighbours(around) & ~inside
        grown |= around
        if (grown == around).all():
            break
        around = grown
    ys, xs = np.nonzero(inside & _neighbours(around))
    return np.stack([xs + left - 1, ys + top - 1], axis=1).astype(np.float64)


def _neighbours(mask: np.ndarray) -> np.ndarray:
    """ The pixels next to one of a mask's, up, down, left or right. """
    out = np.zeros_like(mask)
    out[1:, :] |= mask[:-1, :]
    out[:-1, :] |= mask[1:, :]
    out[:, 1:] |= mask[:, :-1]
    out[:, :-1] |= mask[:, 1:]
    return out


def _fit_sides(outline: np.ndarray, boundary: np.ndarray) -> Optional[np.ndarray]:
    """
    The corners of the square a frame's straight edges make: each side fitted with a line through the
    boundary pixels along its middle, and the corners where the lines meet.
    """
    lines = []
    for i in range(4):
        a, b = outline[i], outline[(i + 1) % 4]
        length = float(np.hypot(*(b - a)))
        along_unit = (b - a) / length
        across_unit = np.array([-along_unit[1], along_unit[0]])
        # The outline's points sit on the rounded corners, so a line through two of them runs a few pixels
        # off the side. A wide band finds the side, a narrow one around the line through it fits it.
        relative = boundary - a
        along = relative @ along_unit
        near = (along > 0.2 * length) & (along < 0.8 * length) & \
            (np.abs(relative @ across_unit) < max(3.0, 0.12 * length))
        line = _fit_line(boundary[near])
        if line is None:
            return None
        centre, direction = line
        relative = boundary - centre
        normal = np.array([-direction[1], direction[0]])
        near = (np.abs(relative @ normal) < 1.5) & (np.abs(relative @ direction) < 0.35 * length)
        line = _fit_line(boundary[near])
        if line is None:
            return None
        # The line runs through the centres of the last dark pixels. The edge is half a pixel further out,
        # between them and the first light ones: left there, the frame came out a pixel small, and a marker
        # 70 pixels wide 1.4% further away than it was.
        centre, direction = line
        normal = np.array([-direction[1], direction[0]])
        if normal @ (centre - outline.mean(axis=0)) < 0:
            normal = -normal
        lines.append((centre + 0.5 * normal, direction))
    corners = []
    for i in range(4):
        (c1, d1), (c2, d2) = lines[i - 1], lines[i]
        try:
            t = np.linalg.solve(np.array([d1, -d2]).T, c2 - c1)
        except np.linalg.LinAlgError:
            return None
        corners.append(c1 + t[0] * d1)
    return np.array(corners)


def _fit_line(points: np.ndarray) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """ The least squares line through points, as a point on it and its direction. """
    if len(points) < 4:
        return None
    centre = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points - centre)
    return centre, vt[0]


def _looks_like_a_marker(gray: np.ndarray, corners: np.ndarray) -> bool:
    """
    Whether a frame has a dark ring along its edge, a light margin inside it, and light around it: a marker
    sits on a cube's white plastic. Without that last, the dark table around a white card made a ring too.
    """
    size, border = 48, 0.15
    square = rectify(gray, corners, size, border)
    t = -border + (np.arange(size) + 0.5) / size * (1.0 + 2.0 * border)
    xs, ys = np.meshgrid(t, t)
    # How far inside the square each sample is, as a fraction of its side: negative outside it.
    edge = np.minimum(np.minimum(xs, 1.0 - xs), np.minimum(ys, 1.0 - ys))
    ring = square[(edge > _RING[0]) & (edge < _RING[1])]
    margin = square[(edge > _MARGIN[0]) & (edge < _MARGIN[1])]
    around = square[(edge > -_AROUND[1]) & (edge < -_AROUND[0])]
    dark = float(np.median(ring))
    checks = []
    for light, share in ((margin, 0.85), (around, 0.75)):
        threshold = (dark + float(np.median(light))) / 2.0
        checks.append(float(np.median(light)) - dark > _MIN_CONTRAST and
                      (ring < threshold).mean() > 0.85 and (light > threshold).mean() > share)
    return all(checks)


def _refine_sides(gray: np.ndarray, corners: np.ndarray,
                  calibration: Optional[camera.CameraCalibration] = None) -> np.ndarray:
    """
    The corners, to a fraction of a pixel. Across each side, at a dozen places along its middle, brightness
    goes from the dark ring to the light plastic around it, and how much of the way across is dark, summed
    up, is where the side is: whatever the side's position between two pixels, blurring it into both does
    not change that sum. Found from which pixels are dark alone, a side could be half a pixel off, which for
    a marker 20 pixels wide is 2.5% of its distance; where brightness is halfway was up to 0.08 pixels off.
    """
    centre = corners.mean(axis=0)
    side = float(np.mean([np.hypot(*(corners[(i + 1) % 4] - corners[i])) for i in range(4)]))
    # Across the side, no further in than the ring is thick.
    reach = max(1.0, min(2.5, 0.08 * side))
    # Finely: interpolated brightness bends at every pixel's centre, and summed in coarse steps, the bends
    # moved the side by up to 0.08 pixels depending on where they fell.
    offsets = np.linspace(-reach, reach, 101)
    step = offsets[1] - offsets[0]
    lines = []
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        direction = (b - a) / np.hypot(*(b - a))
        normal = np.array([-direction[1], direction[0]])
        if normal @ (a - centre) < 0:
            normal = -normal
        points = []
        for fraction in np.linspace(0.2, 0.8, 12):
            base = a + fraction * (b - a)
            samples = base[None, :] + offsets[:, None] * normal[None, :]
            profile = _bilinear(gray, samples[:, 0], samples[:, 1])
            inside, outside = profile[:10].mean(), profile[-10:].mean()
            if outside - inside < _MIN_CONTRAST:
                continue
            darkness = np.clip((outside - profile) / (outside - inside), 0.0, 1.0)
            # Each sample stands for a step of the profile, centred on it.
            d = offsets[0] - step / 2.0 + step * float(darkness.sum())
            points.append(base + d * normal)
        if len(points) < 6:
            return corners
        found = np.array(points)
        if calibration is not None:
            # The lens bends straight lines; without it, they are straight again.
            found = np.stack(calibration.undistort(found[:, 0], found[:, 1]), axis=1)
        line = _fit_line(found)
        if line is None:
            return corners
        lines.append(line)
    refined = []
    for i in range(4):
        (c1, d1), (c2, d2) = lines[i - 1], lines[i]
        try:
            t = np.linalg.solve(np.array([d1, -d2]).T, c2 - c1)
        except np.linalg.LinAlgError:
            return corners
        refined.append(c1 + t[0] * d1)
    result = np.array(refined)
    if calibration is not None:
        result = np.stack(calibration.distort(result[:, 0], result[:, 1]), axis=1)
    return result


def _bilinear(gray: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """ Brightness at points between pixels, interpolated from the four around each. """
    height, width = gray.shape
    u = np.clip(u, 0.0, width - 1.001)
    v = np.clip(v, 0.0, height - 1.001)
    u0, v0 = np.floor(u).astype(int), np.floor(v).astype(int)
    fu, fv = u - u0, v - v0
    values: np.ndarray = (gray[v0, u0] * (1 - fu) * (1 - fv) + gray[v0, u0 + 1] * fu * (1 - fv)
                          + gray[v0 + 1, u0] * (1 - fu) * fv + gray[v0 + 1, u0 + 1] * fu * fv)
    return values


def _clockwise(corners: np.ndarray) -> np.ndarray:
    """ Corners in clockwise order on the screen, y down, from the top left one. """
    centre = corners.mean(axis=0)
    angles = np.arctan2(corners[:, 1] - centre[1], corners[:, 0] - centre[0])
    corners = corners[np.argsort(angles)]
    return np.roll(corners, -int(np.argmin(corners[:, 0] + corners[:, 1])), axis=0)


def _homography(source: Points, target: Points) -> np.ndarray:
    """ The homography that takes four points onto four others. """
    rows = []
    for (x, y), (u, v) in zip(source, target):
        rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y, -u])
        rows.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y, -v])
    _, _, vt = np.linalg.svd(np.array(rows, dtype=np.float64))
    h: np.ndarray = vt[-1].reshape(3, 3)
    normalized: np.ndarray = h / h[2, 2]
    return normalized
