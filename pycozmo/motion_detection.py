"""

Motion detection in the camera images.

The robot does not report motion. Its camera streams images and the application compares them, as
Anki's engine did on the phone. What moved is what behaviors such as PounceOnMotion react to - a
finger or a toy wiggled in front of the robot.

Two consecutive images are compared pixel by pixel, as the ratio of their brightness rather than the
difference, and that ratio is divided by its median over the image. A change of exposure or of the
room's lighting scales every pixel alike and so moves the median rather than the pixels; something
moving changes a few pixels and not the median. Pixels too dark to have a meaningful ratio are left
out, and so are isolated ones, which are JPEG noise far more often than motion.

What is reported:

- the fraction of the image that moved and the centroid of that motion, once enough of it moved;
- motion in three peripheral regions - left, right and top - each through an accumulator that
  motion in the region feeds and that drains a little every image. A region reports once its
  accumulator is full, and for as long as it stays full: with Anki's values, once 4% of the region
  moves in a single image, or a smaller part of it over several in a row. Motion too small to
  outweigh the drain never adds up to anything.

Nothing is detected while the camera itself moves: every pixel would. Images are ignored while the
robot reports any of its motors moving and for a short while after, and whenever its pose or head
angle changed between two images.

Nor is anything detected for the first seconds of a stream. When the camera starts streaming, the
robot's image is often not yet locked to the sensor's frames: the picture scrolls vertically, a
little further with each image, with the left third of it garbled, until it locks. Measured on a
robot, that lasted 1.8 s and 28 images, and every one of them looked like motion across the whole
image. The robot flags nothing about those images, so a stream is taken to start with the first
image and with any image that follows a gap, and its images are ignored for stream_warmup.

The peripheral regions are sized and paced by the MotionDetector section of Anki's
cozmo_resources/config/engine/vision_config.json. Its comments describe what each value does but
not the algorithm, so the one here is PyCozmo's own. One comment is contradicted on purpose: it says
a higher MaxValue triggers peripheral motion sooner, which no accumulator that fills up to it and
drains can do. Here it is the level a region has to reach, so a higher one triggers later.

Motion on the ground is what PounceOnMotion pounces on, and is reported in mm in the robot's frame.
Each pixel is traced back through the lens, from the camera's calibration, and out to the table from
where the head's angle and the robot's tilt put the camera. Pixels above the horizon are not ground,
and neither are those that would land closer than ground_min_distance: with the head down, the
bottom of the image is the lift, seen from above, which looks like ground about 60 mm ahead and is
not. Beyond ground_max_distance a pixel covers too much ground to say much. A tall object moving is
placed a little too far, since only where it meets the table is on the ground.

"""

import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import numpy as np
from PIL import Image

from . import camera
from .json_loader import load_json_file


__all__ = [
    "REGIONS",

    "MotionDetectorConfig",
    "ObservedMotion",
    "MotionDetector",

    "load_motion_detector_config",
]


#: The peripheral regions, in the order they are reported.
REGIONS = ("left", "right", "top")


@dataclass
class MotionDetectorConfig:
    """ Motion detection parameters. The first six are Anki's, the rest PyCozmo's. """

    #: Fraction of the image width, on either side, that the left and right regions cover.
    horizontal_size: float = 0.3
    #: Fraction of the image height, from the top, that the top region covers.
    vertical_size: float = 0.4
    #: How much a region's accumulator gains per image, per unit of the region's moving fraction.
    increase_factor: float = 100.0
    #: How much a region's accumulator loses per image.
    decrease_factor: float = 1.0
    #: The level a region's accumulator has to reach to report motion, and cannot go past.
    max_value: float = 3.0
    #: How much of its previous position a region's centroid keeps with each new image.
    centroid_stability: float = 0.6

    #: How much the images are shrunk before they are compared. Averaging blocks of pixels removes
    #: most of the JPEG noise, and a quarter of the pixels is four times less work.
    downscale: int = 2
    #: Pixels darker than this, on a 0-255 scale, in either image are left out.
    min_brightness: float = 12.0
    #: How much a pixel's brightness has to change, as a ratio against the image's median change,
    #: to count as moving.
    ratio_threshold: float = 1.3
    #: How many of a moving pixel's 3x3 neighbourhood, itself included, have to be moving too.
    min_neighbours: int = 5
    #: Fraction of the image that has to move for the image as a whole to report motion.
    min_area: float = 0.005
    #: How long images are ignored after the robot last reported a motor moving, in seconds. What
    #: the robot reports lags the images a little, and a head coming to rest wobbles.
    settle_time: float = 0.3
    #: How long images are ignored once a stream starts, in seconds, and how long a gap between two
    #: images has to be for the next one to start a stream. See the module's description.
    stream_warmup: float = 2.5
    stream_gap: float = 0.5
    #: Largest change between two images of the robot's position in mm, and of its heading and head
    #: angle in radians, that still counts as a camera at rest.
    max_translation: float = 1.0
    max_rotation: float = 0.01
    #: The nearest and farthest ground that motion is looked for on, in mm from the robot's origin. The
    #: nearest is set by the lift: with the head all the way down, the lift hides the ground up to the
    #: equivalent of 58 mm ahead, measured on a robot.
    ground_min_distance: float = 65.0
    ground_max_distance: float = 400.0


@dataclass
class ObservedMotion:
    """
    Motion seen in one camera image.

    Positions are in pixels of the image as received, x to the right and y down.
    """

    #: Robot timestamp of the image, in ms, when known.
    timestamp: Optional[int]
    #: Size of the image, in pixels.
    width: int
    height: int
    #: Fraction of the whole image that moved, and the centroid of that motion. The centroid is
    #: None when too little moved to be worth reporting.
    area: float
    centroid: Optional[Tuple[float, float]]
    #: Smoothed centroid of the motion in each peripheral region that reports motion.
    regions: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    #: Fraction of the image's ground that moved, and where that motion is on the ground, in mm in the
    #: robot's frame - x ahead, y to its left. The position is None when too little of the ground
    #: moved, or when there is no telling where the ground is.
    ground_area: float = 0.0
    ground_centroid: Optional[Tuple[float, float]] = None

    @property
    def any(self) -> bool:
        """ Whether anything was reported at all. """
        return self.centroid is not None or bool(self.regions) or self.ground_centroid is not None


class MotionDetector:
    """ Compares consecutive camera images and reports what moved. """

    def __init__(self, config: Optional[MotionDetectorConfig] = None,
                 calibration: Optional[camera.CameraCalibration] = None) -> None:
        self.config = config or MotionDetectorConfig()
        #: The camera's calibration. Without one, motion is not placed on the ground.
        self.calibration = calibration
        # Lines of sight of the shrunk image's pixels, which only change with the calibration.
        self._sight_key: Optional[tuple] = None
        self._sight: Tuple[np.ndarray, np.ndarray] = (np.empty(0), np.empty(0))
        self._previous: Optional[np.ndarray] = None
        self._previous_pose: Optional[Tuple[float, float, float, float]] = None
        self._previous_time: Optional[float] = None
        self._settle_until = 0.0
        self._accumulators = {region: 0.0 for region in REGIONS}
        self._centroids: Dict[str, Optional[Tuple[float, float]]] = {region: None for region in REGIONS}

    def reset(self) -> None:
        """ Forget the previous image and any motion accumulated. """
        self._previous = None
        self._previous_pose = None
        self._previous_time = None
        self._settle_until = 0.0
        for region in REGIONS:
            self._accumulators[region] = 0.0
            self._centroids[region] = None

    def process(self, image: Image.Image, now: float,
                pose: Optional[Tuple[float, float, float, float]] = None,
                moving: bool = False,
                timestamp: Optional[int] = None,
                pitch: float = 0.0) -> Optional[ObservedMotion]:
        """
        Compare an image with the previous one.

        pose is the robot's (x, y, heading, head angle) when the image was taken, in mm and radians,
        pitch its tilt in radians, and moving whether it reports any motor moving. Motion is placed on
        the ground only with a pose and a calibration. Returns None when the image could not be
        compared - one taken while the camera moved, or in the first seconds of a stream - and what
        was seen otherwise, which may be nothing.
        """
        config = self.config
        current = self._shrink(image)

        camera_moved = moving or self._pose_changed(pose)
        self._previous_pose = pose
        if camera_moved:
            self._settle_until = max(self._settle_until, now + config.settle_time)
        if self._previous_time is None or now - self._previous_time > config.stream_gap:
            self._settle_until = max(self._settle_until, now + config.stream_warmup)
        self._previous_time = now
        if camera_moved or now < self._settle_until or self._previous is None \
                or self._previous.shape != current.shape:
            # Whatever accumulated at the edges was seen from somewhere else.
            self._previous = current
            for region in REGIONS:
                self._accumulators[region] = 0.0
                self._centroids[region] = None
            return None

        mask = self._moving_pixels(self._previous, current)
        self._previous = current

        width, height = image.size
        # Scale from the shrunk image back to the one received, to the centre of each block. Pixels are
        # centred on whole coordinates, as the calibration has them.
        scale_x = width / mask.shape[1]
        scale_y = height / mask.shape[0]
        rows, cols = np.nonzero(mask)
        xs = cols * scale_x + (scale_x - 1.0) / 2.0
        ys = rows * scale_y + (scale_y - 1.0) / 2.0

        area = len(rows) / mask.size
        centroid: Optional[Tuple[float, float]] = None
        if len(rows) and area >= config.min_area:
            centroid = (float(xs.mean()), float(ys.mean()))

        regions = {}
        for region in REGIONS:
            if region == "left":
                inside = xs < config.horizontal_size * width
                region_area = config.horizontal_size
            elif region == "right":
                inside = xs >= (1.0 - config.horizontal_size) * width
                region_area = config.horizontal_size
            else:
                inside = ys < config.vertical_size * height
                region_area = config.vertical_size
            fraction = int(inside.sum()) / (mask.size * region_area)
            point = self._update_region(region, fraction, xs[inside], ys[inside])
            if point is not None:
                regions[region] = point

        ground_area = 0.0
        ground_centroid: Optional[Tuple[float, float]] = None
        if self.calibration is not None and pose is not None:
            ground_x, ground_y, ground = self._ground(mask.shape, width, height, pose[3], pitch)
            visible = int(ground.sum())
            if visible:
                moved = mask & ground
                ground_area = int(moved.sum()) / visible
                if ground_area and ground_area >= config.min_area:
                    ground_centroid = (float(ground_x[moved].mean()), float(ground_y[moved].mean()))

        return ObservedMotion(timestamp=timestamp, width=width, height=height,
                              area=area, centroid=centroid, regions=regions,
                              ground_area=ground_area, ground_centroid=ground_centroid)

    def _ground(self, shape: Tuple[int, ...], width: int, height: int, head_angle: float,
                pitch: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """ Where each pixel of the shrunk image is on the ground, and which of them count as ground. """
        assert self.calibration is not None
        key = (shape, width, height, self.calibration)
        if key != self._sight_key:
            calibration = self.calibration
            if (calibration.width, calibration.height) != (width, height):
                calibration = calibration.scaled(width, height)
            rows, cols = shape
            scale_x, scale_y = width / cols, height / rows
            u, v = np.meshgrid(np.arange(cols) * scale_x + (scale_x - 1.0) / 2.0,
                               np.arange(rows) * scale_y + (scale_y - 1.0) / 2.0)
            self._sight = calibration.undistort(u, v)
            self._sight_key = key
        ground_x, ground_y, reaches = camera.ground_points(*self._sight, head_angle, pitch)
        config = self.config
        ground = reaches & (ground_x >= config.ground_min_distance) \
            & (np.hypot(ground_x, ground_y) <= config.ground_max_distance)
        return ground_x, ground_y, ground

    def _shrink(self, image: Image.Image) -> np.ndarray:
        """ Greyscale image as floats, averaged over blocks of downscale x downscale pixels. """
        pixels = np.asarray(image.convert("L"), dtype=np.float32)
        factor = max(1, self.config.downscale)
        height = pixels.shape[0] // factor * factor
        width = pixels.shape[1] // factor * factor
        pixels = pixels[:height, :width]
        shrunk: np.ndarray = pixels.reshape(height // factor, factor, width // factor, factor).mean(axis=(1, 3))
        return shrunk

    def _pose_changed(self, pose: Optional[Tuple[float, float, float, float]]) -> bool:
        previous = self._previous_pose
        if pose is None or previous is None:
            return False
        x, y, heading, head = pose
        px, py, pheading, phead = previous
        turned = abs((heading - pheading + np.pi) % (2.0 * np.pi) - np.pi)
        return bool(np.hypot(x - px, y - py) > self.config.max_translation
                    or turned > self.config.max_rotation
                    or abs(head - phead) > self.config.max_rotation)

    def _moving_pixels(self, previous: np.ndarray, current: np.ndarray) -> np.ndarray:
        """ Boolean mask of the pixels that moved between two shrunk images. """
        config = self.config
        lit = (previous >= config.min_brightness) & (current >= config.min_brightness)
        if not lit.any():
            return np.zeros(current.shape, dtype=bool)
        ratio = np.ones(current.shape, dtype=np.float32)
        ratio[lit] = current[lit] / previous[lit]
        # Divide out whatever changed for the whole image - exposure, lighting.
        ratio /= float(np.median(ratio[lit]))
        changed = lit & ((ratio > config.ratio_threshold) | (ratio < 1.0 / config.ratio_threshold))

        # Count each pixel's moving neighbours, itself included, and drop the isolated ones.
        padded = np.pad(changed.astype(np.int32), 1)
        rows, cols = changed.shape
        neighbours = sum(padded[dy:dy + rows, dx:dx + cols] for dy in range(3) for dx in range(3))
        moving: np.ndarray = changed & (neighbours >= config.min_neighbours)
        return moving

    def _update_region(self, region: str, fraction: float,
                       xs: np.ndarray, ys: np.ndarray) -> Optional[Tuple[float, float]]:
        """ Feed a region's accumulator and centroid. Returns the centroid if the region reports. """
        config = self.config
        level = self._accumulators[region] + config.increase_factor * fraction - config.decrease_factor
        level = min(max(level, 0.0), config.max_value)
        self._accumulators[region] = level

        if level <= 0.0:
            self._centroids[region] = None
        elif len(xs):
            point = (float(xs.mean()), float(ys.mean()))
            previous = self._centroids[region]
            if previous is not None:
                keep = config.centroid_stability
                point = (keep * previous[0] + (1.0 - keep) * point[0],
                         keep * previous[1] + (1.0 - keep) * point[1])
            self._centroids[region] = point

        if level >= config.max_value:
            return self._centroids[region]
        return None


def load_motion_detector_config(resource_dir: str) -> MotionDetectorConfig:
    """ Read the motion detection parameters from Anki's vision configuration. """
    filename = os.path.join(resource_dir, 'cozmo_resources', 'config', 'engine', 'vision_config.json')
    config = MotionDetectorConfig()
    section = load_json_file(filename).get('MotionDetector', {})
    for key, name in (('HorizontalSize', 'horizontal_size'),
                      ('VerticalSize', 'vertical_size'),
                      ('IncreaseFactor', 'increase_factor'),
                      ('DecreaseFactor', 'decrease_factor'),
                      ('MaxValue', 'max_value'),
                      ('CentroidStability', 'centroid_stability')):
        if key in section:
            setattr(config, name, float(section[key]))
    return config
