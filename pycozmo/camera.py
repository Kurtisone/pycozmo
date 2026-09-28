"""

Camera image decoding, and the camera's geometry.

"""

import math
import struct
from dataclasses import dataclass
from typing import Tuple

import numpy as np

from . import protocol_encoder
from . import robot


__all__ = [
    "RESOLUTIONS",

    "CameraCalibration",
    "DEFAULT_CALIBRATION",

    "minigray_to_jpeg",
    "minicolor_to_jpeg",
    "ground_points",
    "camera_to_robot",
]


#: Camera resolutions.
RESOLUTIONS = {
    protocol_encoder.ImageResolution.VerificationSnapshot: (16, 16),
    protocol_encoder.ImageResolution.QQQQVGA: (40, 30),
    protocol_encoder.ImageResolution.QQQVGA: (80, 60),
    protocol_encoder.ImageResolution.QQVGA: (160, 120),
    protocol_encoder.ImageResolution.QVGA: (320, 240),
    protocol_encoder.ImageResolution.CVGA: (400, 296),
    protocol_encoder.ImageResolution.VGA: (640, 480),
    protocol_encoder.ImageResolution.SVGA: (800, 600),
    protocol_encoder.ImageResolution.XGA: (1024, 768),
    protocol_encoder.ImageResolution.SXGA: (1280, 960),
    protocol_encoder.ImageResolution.UXGA: (1600, 1200),
    protocol_encoder.ImageResolution.QXGA: (2048, 1536),
    protocol_encoder.ImageResolution.QUXGA: (3200, 2400)
}


@dataclass(frozen=True)
class CameraCalibration:
    """
    A camera's intrinsic calibration: focal lengths and optical centre in pixels, and lens distortion.

    Every robot was calibrated in the factory and keeps the result in its NV storage, under
    NVEntry_CameraCalib. The distortion coefficients follow OpenCV's model - k1, k2, p1, p2, k3 - of
    which the robot stores eight, the last three zero.
    """

    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int
    distortion: Tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0)
    skew: float = 0.0

    #: Layout of NVEntry_CameraCalib: fx, fy, cx, cy, skew, then the number of rows and columns, then eight
    #: distortion coefficients.
    NV_FORMAT = "<5f2H8f"

    @classmethod
    def from_nv(cls, data: bytes) -> "CameraCalibration":
        """ Read a calibration as the robot stores it. """
        if len(data) != struct.calcsize(cls.NV_FORMAT):
            raise ValueError("A camera calibration is {} bytes, not {}.".format(
                struct.calcsize(cls.NV_FORMAT), len(data)))
        fx, fy, cx, cy, skew, rows, cols, *distortion = struct.unpack(cls.NV_FORMAT, data)
        return cls(fx=fx, fy=fy, cx=cx, cy=cy, width=cols, height=rows, distortion=tuple(distortion), skew=skew)

    def scaled(self, width: int, height: int) -> "CameraCalibration":
        """ The same calibration for images of another resolution. Distortion does not depend on it. """
        sx, sy = width / self.width, height / self.height
        return CameraCalibration(fx=self.fx * sx, fy=self.fy * sy, cx=self.cx * sx, cy=self.cy * sy,
                                 width=width, height=height, distortion=self.distortion, skew=self.skew * sx)

    def undistort(self, u: np.ndarray, v: np.ndarray, iterations: int = 5) -> Tuple[np.ndarray, np.ndarray]:
        """
        Where pixels would be without the lens' distortion, in normalized coordinates - the tangents of
        the angles to the optical axis, x to the right and y down.

        OpenCV's distortion model has no closed form inverse. The model only holds so far out: past a
        radius, as with the robot's calibration, it folds back, and the corners of the image lie beyond
        what it reaches at all. The fixed point iteration this used diverged there - pixel (0, 239) came
        out at the optical centre. Now the radius is found first, by bisection along the pixel's direction
        up to where the model folds, and Newton's method, in steps held short and within that radius,
        adds the small tangential part. Pixels beyond the model's reach come out where it folds.
        """
        k1, k2, p1, p2, k3 = (tuple(self.distortion) + (0.0,) * 5)[:5]
        yd = (np.asarray(v, dtype=np.float64) - self.cy) / self.fy
        xd = (np.asarray(u, dtype=np.float64) - self.cx - self.skew * yd) / self.fx

        def radial(r2: np.ndarray) -> np.ndarray:
            factor: np.ndarray = 1.0 + r2 * (k1 + r2 * (k2 + r2 * k3))
            return factor

        # Where r (1 + k1 r^2 + k2 r^4 + k3 r^6) stops growing: the smallest positive root of its derivative,
        # 1 + 3 k1 s + 5 k2 s^2 + 7 k3 s^3 in s = r^2.
        roots = np.roots([7.0 * k3, 5.0 * k2, 3.0 * k1, 1.0]) if (k1, k2, k3) != (0.0, 0.0, 0.0) \
            else np.zeros(0, dtype=complex)
        folds = [float(root.real) for root in np.atleast_1d(roots)
                 if abs(root.imag) < 1e-12 and root.real > 0.0]
        max_r = math.sqrt(min(folds)) if folds else np.inf

        rd = np.hypot(xd, yd)
        low = np.zeros_like(rd)
        high = np.full_like(rd, max_r) if folds else np.maximum(2.0 * rd, 1.0)
        for _ in range(32):
            middle = (low + high) / 2.0
            below = middle * radial(middle * middle) < rd
            low = np.where(below, middle, low)
            high = np.where(below, high, middle)
        scale = np.where(rd > 0.0, low / np.where(rd > 0.0, rd, 1.0), 1.0)
        x, y = xd * scale, yd * scale

        for _ in range(iterations):
            r2 = x * x + y * y
            factor = radial(r2)
            slope = k1 + r2 * (2.0 * k2 + 3.0 * k3 * r2)
            fx = x * factor + 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x) - xd
            fy = y * factor + p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y - yd
            # The Jacobian of the distortion.
            a = factor + 2.0 * x * x * slope + 2.0 * p1 * y + 6.0 * p2 * x
            b = 2.0 * x * y * slope + 2.0 * p1 * x + 2.0 * p2 * y
            d = factor + 2.0 * y * y * slope + 6.0 * p1 * y + 2.0 * p2 * x
            determinant = a * d - b * b
            safe = np.abs(determinant) > 1e-9
            step_x = np.where(safe, (d * fx - b * fy) / np.where(safe, determinant, 1.0), 0.0)
            step_y = np.where(safe, (a * fy - b * fx) / np.where(safe, determinant, 1.0), 0.0)
            length = np.hypot(step_x, step_y)
            shorten = np.minimum(1.0, 0.01 / np.maximum(length, 1e-300))
            x, y = x - step_x * shorten, y - step_y * shorten
            r = np.hypot(x, y)
            inside = np.minimum(1.0, max_r / np.maximum(r, 1e-300))
            x, y = x * inside, y * inside
        return x, y

    def distort(self, x: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """ Where the lens puts points given in normalized coordinates, in pixels. The inverse of undistort(). """
        k1, k2, p1, p2, k3 = (tuple(self.distortion) + (0.0,) * 5)[:5]
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        r2 = x * x + y * y
        radial = 1.0 + r2 * (k1 + r2 * (k2 + r2 * k3))
        xd = x * radial + 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x)
        yd = y * radial + p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y
        return self.cx + self.fx * xd + self.skew * yd, self.cy + self.fy * yd


#: The factory calibration of one robot, for when a robot's own cannot be read. Others differ by a few
#: pixels, which moves a point on the ground by a few millimetres at the distances that matter.
DEFAULT_CALIBRATION = CameraCalibration(
    fx=296.7423, fy=293.6618, cx=167.5319, cy=111.4804, width=320, height=240,
    distortion=(-0.068499, 0.886507, -0.001815, -0.002262, -2.047920, 0.0, 0.0, 0.0))


def ground_points(x: np.ndarray, y: np.ndarray, head_angle: float,
                  pitch: float = 0.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Where on the ground the camera sees, for points in normalized coordinates - see
    CameraCalibration.undistort() .

    head_angle and pitch are in radians, up positive: the head's angle and the robot's own tilt, which
    the robot reports as pose_pitch_rad. Returns the points' positions in mm in the robot's frame - x
    ahead of the origin, y to its left - and a mask of the points whose line of sight reaches the
    ground at all. Whatever stands between the camera and the ground, the lift included, is not
    accounted for.
    """
    angle = head_angle + pitch
    c, s = math.cos(angle), math.sin(angle)
    origin_x, origin_z = _camera_position(angle)
    # Line of sight in the head's frame is (1, -x, -y): ahead, to the left, up.
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    ahead = c + y * s
    up = s - y * c
    reaches = up < 0.0
    distance = np.where(reaches, -origin_z / np.where(reaches, up, -1.0), 0.0)
    return origin_x + distance * ahead, distance * -x, reaches


def camera_to_robot(points: np.ndarray, head_angle: float, pitch: float = 0.0) -> np.ndarray:
    """
    Points in the camera's frame - x to the right, y down, z ahead along the optical axis, in mm - in the
    robot's: x ahead of the origin, y to its left, z up from the ground. head_angle and pitch are as for
    ground_points().

    With no translation, as for a direction rather than a point, subtract camera_to_robot() of the origin.
    """
    angle = head_angle + pitch
    c, s = math.cos(angle), math.sin(angle)
    origin_x, origin_z = _camera_position(angle)
    points = np.asarray(points, dtype=np.float64)
    ahead, left, up = points[..., 2], -points[..., 0], -points[..., 1]
    return np.stack([origin_x + c * ahead - s * up, left, origin_z + s * ahead + c * up], axis=-1)


def _camera_position(angle: float) -> Tuple[float, float]:
    """ Where the camera is, ahead of the robot's origin and up from the ground, for the head and tilt angle. """
    c, s = math.cos(angle), math.sin(angle)
    neck_x, neck_z = robot.NECK_JOINT_POSITION
    cam_x, cam_z = robot.HEAD_CAMERA_POSITION
    return neck_x + cam_x * c - cam_z * s, neck_z + cam_x * s + cam_z * c


def minigray_to_jpeg(minigray: np.ndarray, width: int, height: int) -> np.ndarray:
    """ Converts miniGrayToJpeg format to normal JPEG format. """
    header50 = np.array([
        0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01,
        0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43, 0x00, 0x10, 0x0B, 0x0C, 0x0E, 0x0C, 0x0A, 0x10,
        # // 0x19 = QTable
        0x0E, 0x0D, 0x0E, 0x12, 0x11, 0x10, 0x13, 0x18, 0x28, 0x1A, 0x18, 0x16, 0x16, 0x18, 0x31, 0x23,
        0x25, 0x1D, 0x28, 0x3A, 0x33, 0x3D, 0x3C, 0x39, 0x33, 0x38, 0x37, 0x40, 0x48, 0x5C, 0x4E, 0x40,
        0x44, 0x57, 0x45, 0x37, 0x38, 0x50, 0x6D, 0x51, 0x57, 0x5F, 0x62, 0x67, 0x68, 0x67, 0x3E, 0x4D,

        # //0x71, 0x79, 0x70, 0x64, 0x78, 0x5C, 0x65, 0x67, 0x63, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0xF0,
        0x71, 0x79, 0x70, 0x64, 0x78, 0x5C, 0x65, 0x67, 0x63, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x01, 0x28,
        # // 0x5E = Height x Width

        # //0x01, 0x40, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4, 0x00, 0xD2, 0x00, 0x00, 0x01, 0x05, 0x01, 0x01,
        0x01, 0x90, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4, 0x00, 0xD2, 0x00, 0x00, 0x01, 0x05, 0x01, 0x01,

        0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04,
        0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x10, 0x00, 0x02, 0x01, 0x03, 0x03, 0x02, 0x04, 0x03,
        0x05, 0x05, 0x04, 0x04, 0x00, 0x00, 0x01, 0x7D, 0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12,
        0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xA1, 0x08,
        0x23, 0x42, 0xB1, 0xC1, 0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0A, 0x16,
        0x17, 0x18, 0x19, 0x1A, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39,
        0x3A, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
        0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79,
        0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98,
        0x99, 0x9A, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6,
        0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xD2, 0xD3, 0xD4,
        0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA,
        0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA, 0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01,
        0x00, 0x00, 0x3F, 0x00
    ], dtype=np.uint8)

    return mini_to_jpeg_helper(minigray, width, height, header50)


def minicolor_to_jpeg(minicolor: np.ndarray, width: int, height: int) -> np.ndarray:
    """ Converts miniColorToJpeg format to normal JPEG format. """
    header = np.array([
        0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01,
        0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43, 0x00, 0x10, 0x0B, 0x0C, 0x0E, 0x0C, 0x0A, 0x10,
        # 0x19 = QTable
        0x0E, 0x0D, 0x0E, 0x12, 0x11, 0x10, 0x13, 0x18, 0x28, 0x1A, 0x18, 0x16, 0x16, 0x18, 0x31, 0x23,
        0x25, 0x1D, 0x28, 0x3A, 0x33, 0x3D, 0x3C, 0x39, 0x33, 0x38, 0x37, 0x40, 0x48, 0x5C, 0x4E, 0x40,
        0x44, 0x57, 0x45, 0x37, 0x38, 0x50, 0x6D, 0x51, 0x57, 0x5F, 0x62, 0x67, 0x68, 0x67, 0x3E, 0x4D,
        0x71, 0x79, 0x70, 0x64, 0x78, 0x5C, 0x65, 0x67, 0x63, 0xFF, 0xC0, 0x00, 17,  # 8+3*components
        0x08, 0x00, 0xF0,  # 0x5E = Height x Width
        0x01, 0x40,
        0x03,  # 3 components
        0x01, 0x21, 0x00,  # Y 2x1 res
        0x02, 0x11, 0x00,  # Cb
        0x03, 0x11, 0x00,  # Cr
        0xFF, 0xC4, 0x00, 0xD2, 0x00, 0x00, 0x01, 0x05, 0x01, 0x01,
        0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04,
        0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x10, 0x00, 0x02, 0x01, 0x03, 0x03, 0x02, 0x04, 0x03,
        0x05, 0x05, 0x04, 0x04, 0x00, 0x00, 0x01, 0x7D, 0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12,
        0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xA1, 0x08,
        0x23, 0x42, 0xB1, 0xC1, 0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0A, 0x16,
        0x17, 0x18, 0x19, 0x1A, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39,
        0x3A, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
        0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79,
        0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98,
        0x99, 0x9A, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6,
        0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xD2, 0xD3, 0xD4,
        0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA,
        0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA,
        0xFF, 0xDA, 0x00, 12,
        0x03,  # 3 components
        0x01, 0x00,  # Y
        0x02, 0x00,  # Cb same AC/DC
        0x03, 0x00,  # Cr same AC/DC
        0x00, 0x3F, 0x00
    ], dtype=np.uint8)

    return mini_to_jpeg_helper(minicolor, width, height, header)


def mini_to_jpeg_helper(mini: np.ndarray, width: int, height: int, header: np.ndarray) -> np.ndarray:
    """ Low-level mini*ToJpeg format to normal JPEG format conversion. """
    buffer_in = mini.tolist()
    curr_len = len(mini)

    header_length = len(header)
    # For worst case expansion
    buffer_out = np.array([0] * (curr_len * 2 + header_length), dtype=np.uint8)

    for i in range(header_length):
        buffer_out[i] = header[i]

    buffer_out[0x5e] = height >> 8
    buffer_out[0x5f] = height & 0xff
    buffer_out[0x60] = width >> 8
    buffer_out[0x61] = width & 0xff
    # Remove padding at the end
    while buffer_in[curr_len - 1] == 0xff:
        curr_len -= 1

    off = header_length
    for i in range(curr_len - 1):
        buffer_out[off] = buffer_in[i + 1]
        off += 1
        if buffer_in[i + 1] == 0xff:
            buffer_out[off] = 0
            off += 1

    buffer_out[off] = 0xff
    off += 1
    buffer_out[off] = 0xD9

    return np.asarray(buffer_out)
