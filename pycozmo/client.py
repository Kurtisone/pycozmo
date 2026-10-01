"""

Cozmo protocol client and high-level API.

"""

from collections import defaultdict
from threading import Event
from typing import Any, Dict, List, Optional, Set, Tuple
import json
import math
import os
import time
import io

import numpy as np
from PIL import Image

from .logger import logger, logger_robot, logger_animation
from . import protocol_base
from . import protocol_encoder
from . import event
from . import camera
from . import object
from . import util
from . import robot
from . import exception
from . import filter
from . import protocol_declaration
from . import conn
from . import lights
from . import image_encoder
from . import anim
from . import cubes
from . import faces
from . import anim_encoder
from . import audio
from . import audiolib
from . import anim_controller
from . import robot_debug


__all__ = [
    "Client",
]


#: Speaker volume set on connection, out of 65535.
DEFAULT_VOLUME = 32767


class Client(event.Dispatcher):
    """ Cozmo protocol client and high-level API class. """

    def __init__(self,
                 robot_addr: Optional[Tuple[str, int]] = None,
                 protocol_log_messages: Optional[list] = None,
                 auto_initialize: bool = True,
                 enable_animations: bool = True,
                 enable_procedural_face: bool = True) -> None:
        super().__init__()
        # Whether to automatically initialize the robot when connection is established.
        self.auto_initialize = bool(auto_initialize)

        self.conn = conn.Connection(robot_addr, protocol_log_messages)
        self.conn.add_child_dispatcher(self)
        self.anim_controller = anim_controller.AnimationController(self)
        self.anim_controller.enable_animations(auto_initialize and enable_animations)
        self.anim_controller.enable_procedural_face(auto_initialize and enable_animations and enable_procedural_face)

        self.serial_number_head: Optional[int] = None
        self.robot_fw_sig: Optional[Dict[str, Any]] = None
        self.serial_number: Optional[int] = None
        self.body_hw_version: Optional[int] = None
        self.body_color: Optional[protocol_encoder.BodyColor] = None
        # Robot state
        # Heading in X-Y plane.
        self.pose_frame_id = 0
        self.pose = util.Pose(0.0, 0.0, 0.0, angle_z=util.Angle(radians=0.0), origin_id=1)
        self.pose_pitch = util.Angle(radians=0.0)
        self.head_angle = util.Angle(radians=robot.MIN_HEAD_ANGLE.radians)
        self.left_wheel_speed = util.Speed(mmps=0.0)
        self.right_wheel_speed = util.Speed(mmps=0.0)
        self.lift_position = robot.LiftPosition(height=robot.MIN_LIFT_HEIGHT)
        self.battery_voltage = 0.0
        self.accel = util.Vector3(0.0, 0.0, 0.0)
        self.gyro = util.Vector3(0.0, 0.0, 0.0)
        self.robot_status = 0
        self.robot_orientation = robot.RobotOrientation.ON_THREADS
        # Orientation being considered, and since when. See _update_orientation() .
        self._candidate_orientation = robot.RobotOrientation.ON_THREADS
        self._candidate_orientation_time = 0.0
        self.robot_picked_up = False
        self.robot_moving = False
        # When motion was last seen on the ground, and where, in mm ahead of and to the left of the
        # robot. The brain reports motion as EvtMotionObserved; behaviors that are not running do not
        # hear events, so the ones that wait for motion ask here.
        self.last_ground_motion: Optional[Tuple[float, Tuple[float, float]]] = None
        # Animation state
        self.num_anim_bytes_played = 0
        self.num_audio_frames_played = 0
        self.enabled_anim_tracks = 0
        self.tag = 0
        self.client_drop_count = 0
        # Camera state
        self.last_image_timestamp: Optional[int] = None
        # Object state
        self.available_objects: Dict[int, object.Object] = dict()
        self.connected_objects: Dict[int, Dict[str, Any]] = dict()
        #: The Light Cubes: connections, lights, and where they were seen.
        self.cubes = cubes.Cubes(self)
        #: The faces the robot sees and knows: see pycozmo.faces. Found only with OpenCV: see pycozmo.face_detection.
        self.faces = faces.Faces(self)
        # Filters
        self.packet_type_filter = filter.Filter()
        self.packet_type_filter.deny_ids({protocol_declaration.PacketType.PING.value})
        self.packet_id_filter = filter.Filter()
        self._reset_partial_state()
        # Animations
        self._clip_metadata: Dict[str, anim_encoder.ClipMetadata] = {}
        self._clips: Dict[str, anim_encoder.AnimClip] = {}
        self._ppclips: Dict[str, anim.PreprocessedClip] = {}
        # The clips whose sound is a song, which a switch picks: kept only until it changes.
        self._singing_clips: Set[str] = set()
        self._next_anim_id = 1
        # The last path's event ID. See execute_path() .
        self._path_event_id = 0
        #: The camera's calibration, once read_camera_calibration() has read it.
        self.camera_calibration: Optional[camera.CameraCalibration] = None
        self.animation_groups: Dict[str, anim.AnimationGroup] = {}
        self.audio_library = audiolib.AudioLibrary()
        # Where the image sequences some animations show are. Set by load_anims().
        self.face_animation_dir: Optional[str] = None

    def start(self) -> None:
        logger.debug("Starting client...")
        self.add_handler(protocol_encoder.HardwareInfo, self._on_hardware_info)
        self.add_handler(protocol_encoder.FirmwareSignature, self._on_firmware_signature)
        self.add_handler(protocol_encoder.BodyInfo, self._on_body_info)
        self.add_handler(protocol_encoder.ImageChunk, self._on_image_chunk)
        self.add_handler(protocol_encoder.RobotState, self._on_robot_state)
        self.add_handler(protocol_encoder.AnimationState, self._on_animation_state)
        self.add_handler(protocol_encoder.ObjectAvailable, self._on_object_available)
        self.add_handler(protocol_encoder.ObjectConnectionState, self._on_object_connection_state)
        self.add_handler(protocol_encoder.ObjectAvailable, self.cubes.on_object_available)
        self.add_handler(protocol_encoder.ObjectConnectionState, self.cubes.on_object_connection_state)
        self.add_handler(protocol_encoder.ObjectMoved, self.cubes.on_object_moved)
        self.add_handler(protocol_encoder.ObjectStoppedMoving, self.cubes.on_object_stopped_moving)
        self.add_handler(protocol_encoder.ObjectTapped, self.cubes.on_object_tapped)
        self.add_handler(protocol_encoder.ObjectUpAxisChanged, self.cubes.on_object_up_axis_changed)
        self.add_handler(protocol_encoder.ObjectPowerLevel, self.cubes.on_object_power_level)
        self.add_handler(protocol_encoder.DebugData, self._on_debug_data)
        self.add_handler(event.EvtRobotPickedUpChange, self._on_robot_picked_up)
        self.add_handler(event.EvtMotionObserved, self._on_motion_observed)
        self.add_handler(event.EvtRobotWheelsMovingChange, self._on_robot_moving)
        self.conn.start()

    def stop(self) -> None:
        logger.debug("Stopping client...")
        self.conn.stop()
        self.anim_controller.stop()
        self.del_all_handlers()

    def connect(self) -> None:
        logger.debug("Connecting...")
        # A new connection to the robot starts with no cube connected.
        self.cubes.reset()
        self.conn.connect()

    def disconnect(self) -> None:
        logger.debug("Disconnecting...")
        self.conn.disconnect()

    def _enable_robot(self):
        # Enable
        pkt = protocol_encoder.Enable()
        self.conn.send(pkt)
        self.conn.send(pkt)  # This repetition seems to trigger BodyInfo

    def _initialize_robot(self):
        # Set world frame origin to (0,0,0), frame ID to 0, and origin ID to 1.
        pkt: protocol_base.Packet = protocol_encoder.SetOrigin()
        self.conn.send(pkt)
        # Give the speaker a volume. The robot comes up silent and nothing else sets one, so
        # animation sound and play_audio() went out to a speaker turned off. The Cozmo application
        # set a volume of its own; half range is a robot you can hear across a desk without it
        # being startling. Applications can call set_volume() for something else.
        self.set_volume(DEFAULT_VOLUME)
        # Set timestamp to 0. Also enables RobotState and ObjectAvailable events. Requires Enable (0x25).
        pkt = protocol_encoder.SyncTime()
        self.conn.send(pkt)

        # TODO: Proper waiting for motor calibration to complete.
        time.sleep(0.5)

        self.anim_controller.start()

        self.dispatch(event.EvtRobotReady, self)

    def _on_hardware_info(self, cli: conn.Connection, pkt: protocol_encoder.HardwareInfo) -> None:
        del cli
        self.serial_number_head = pkt.serial_number_head

    def _on_firmware_signature(self, cli: conn.Connection, pkt: protocol_encoder.FirmwareSignature) -> None:
        del cli
        robot_fw_sig = json.loads(pkt.signature)
        self.robot_fw_sig = robot_fw_sig
        logger.info("Firmware version %s.", robot_fw_sig["version"])
        if robot_fw_sig.get("build") == "FACTORY":
            logger.warning("Factory/recovery firmware detected. Functionality is degraded.")
        elif robot_fw_sig["version"] < protocol_declaration.FIRMWARE_VERSION:
            logger.warning(
                "Old firmware detected. PyCozmo works best with v{}. Functionality may be degraded.".format(
                    protocol_declaration.FIRMWARE_VERSION))
        self._enable_robot()

    def _on_body_info(self, cli: conn.Connection, pkt: protocol_encoder.BodyInfo) -> None:
        del cli
        self.serial_number = pkt.serial_number
        self.body_hw_version = pkt.body_hw_version
        self.body_color = pkt.body_color
        logger.info("Body S/N 0x%08x, HW version %i, color %i.",
                    pkt.serial_number, pkt.body_hw_version, pkt.body_color.value)
        if self.auto_initialize:
            self._initialize_robot()
        self.dispatch(event.EvtRobotFound, self)

    def wait_for_robot(self, timeout: float = 5.0) -> None:
        if not self.robot_fw_sig:
            try:
                self.wait_for(event.EvtRobotFound, timeout=timeout)
            except exception.Timeout as e:
                raise exception.ConnectionTimeout("Failed to connect to Cozmo.") from e

        if not self.serial_number:
            try:
                self.wait_for(event.EvtRobotReady, timeout=timeout)
            except exception.Timeout as e:
                raise exception.ConnectionTimeout("Failed to initialize Cozmo.") from e

    def _reset_partial_state(self) -> None:
        self._partial_image_timestamp: Optional[int] = None
        self._partial_data: Optional[np.ndarray] = None
        self._partial_image_id: Optional[int] = None
        self._partial_invalid = False
        self._partial_size = 0
        self._partial_image_encoding: Optional[protocol_encoder.ImageEncoding] = None
        self._partial_image_resolution: Optional[protocol_encoder.ImageResolution] = None
        self._last_chunk_id = -1

    def _on_image_chunk(self, cli: conn.Connection, pkt: protocol_encoder.ImageChunk) -> None:
        del cli
        if self._partial_image_id is not None and pkt.chunk_id == 0:
            if not self._partial_invalid:
                logger.debug("Lost final chunk of image - discarding.")
            self._partial_image_id = None

        if self._partial_image_id is None:
            if pkt.chunk_id != 0:
                if not self._partial_invalid:
                    logger.debug("Received chunk of broken image.")
                self._partial_invalid = True
                return
            # Discard any previous in-progress image
            self._reset_partial_state()
            self._partial_image_id = pkt.image_id
            self._partial_image_encoding = protocol_encoder.ImageEncoding(pkt.image_encoding)
            self._partial_image_resolution = protocol_encoder.ImageResolution(pkt.image_resolution)

            image_resolution = protocol_encoder.ImageResolution(pkt.image_resolution)
            width, height = camera.RESOLUTIONS[image_resolution]
            max_size = width * height * 3  # 3 bytes per pixel (RGB)
            self._partial_data = np.empty(max_size, dtype=np.uint8)

        if pkt.chunk_id != (self._last_chunk_id + 1) or pkt.image_id != self._partial_image_id:
            logger.debug("Image missing chunks - discarding (last_chunk_id=%d partial_image_id=%s).",
                         self._last_chunk_id, self._partial_image_id)
            self._reset_partial_state()
            self._partial_invalid = True
            return

        # Set together with _partial_image_id, which the guard above has just confirmed.
        assert self._partial_data is not None
        offset = self._partial_size
        self._partial_data[offset:offset + len(pkt.data)] = np.frombuffer(pkt.data, dtype=np.uint8)
        self._partial_size += len(pkt.data)
        self._last_chunk_id = pkt.chunk_id
        # The robot does not populate frame_timestamp in the first chunk of a frame, so keep the value from the
        # most recent chunk instead of the one the frame started with.
        self._partial_image_timestamp = pkt.frame_timestamp

        if pkt.chunk_id == pkt.image_chunk_count - 1:
            self._process_completed_image()
            self._reset_partial_state()

    def _process_completed_image(self) -> None:
        # Only reached once a full chunk sequence has been collected, so the partial state is populated.
        assert self._partial_data is not None
        assert self._partial_image_resolution is not None
        data = self._partial_data[0:self._partial_size]

        # The first byte of the image is whether or not it is in color
        is_color_image = data[0] != 0

        if self._partial_image_encoding == protocol_encoder.ImageEncoding.JPEGMinimizedGray:
            width, height = camera.RESOLUTIONS[self._partial_image_resolution]

            if is_color_image:
                # Color images are half width
                width = width // 2
                data = camera.minicolor_to_jpeg(data, width, height)
            else:
                data = camera.minigray_to_jpeg(data, width, height)

        # data is a numpy array on both paths: the JPEG conversion returns one too. Arrays are only recognised
        # as buffers by the type stubs from Python 3.12 on, so convert explicitly rather than depend on that.
        image = Image.open(io.BytesIO(data.tobytes())).convert('RGB')

        # Color images need to be resized to the proper resolution
        if is_color_image:
            size = camera.RESOLUTIONS[self._partial_image_resolution]
            image = image.resize(size)

        self._latest_image = image
        self.last_image_timestamp = self._partial_image_timestamp
        self.dispatch(event.EvtNewRawCameraImage, self, image)

    def _on_robot_state(self, cli: conn.Connection, pkt: protocol_encoder.RobotState) -> None:
        del cli
        self.pose_frame_id = pkt.pose_frame_id
        self.pose = util.Pose(pkt.pose_x, pkt.pose_y, pkt.pose_z,
                              angle_z=util.Angle(radians=pkt.pose_angle_rad), origin_id=pkt.pose_origin_id)
        self.pose_pitch = util.Angle(radians=pkt.pose_pitch_rad)
        self.head_angle = util.Angle(radians=pkt.head_angle_rad)
        self.left_wheel_speed = util.Speed(mmps=pkt.lwheel_speed_mmps)
        self.right_wheel_speed = util.Speed(mmps=pkt.rwheel_speed_mmps)
        self.lift_position = robot.LiftPosition(angle=util.Angle(radians=pkt.lift_angle_rad))
        self.battery_voltage = pkt.battery_voltage
        self.accel = util.Vector3(pkt.accel_x, pkt.accel_y, pkt.accel_z)
        self.gyro = util.Vector3(pkt.gyro_x, pkt.gyro_y, pkt.gyro_z)
        old_status = self.robot_status
        self.robot_status = pkt.status
        self.dispatch(event.EvtRobotStateUpdated, self)
        # Dispatch status flag change events.
        for flag, evt in event.STATUS_EVENTS.items():
            if (old_status & flag) != (pkt.status & flag):
                state = (pkt.status & flag) != 0
                logger.debug("%s: %i", robot.RobotStatusFlagNames[flag], state)
                self.dispatch(evt, self, state)
        # Orientation. The lateral axis used to be read from pose_angle_rad, which is the heading
        # in the world frame, so every turn of more than 23 degrees reported the robot as lying on
        # a side. Only the accelerometer tells a roll apart from a turn.
        self._update_orientation(robot.get_orientation(self.accel, pkt.pose_pitch_rad))

    def _update_orientation(self, orientation: robot.RobotOrientation) -> None:
        """ Accept a new orientation once it has held for long enough, and announce it. """
        now = time.perf_counter()
        if orientation != self._candidate_orientation:
            self._candidate_orientation = orientation
            self._candidate_orientation_time = now
        elif orientation != self.robot_orientation and \
                now - self._candidate_orientation_time >= robot.ORIENTATION_HOLD_TIME:
            self.robot_orientation = orientation
            self.dispatch(event.EvtRobotOrientationChange, self, orientation)

    def _on_motion_observed(self, cli: Any, motion: Any) -> None:
        del cli
        if motion.ground_centroid is not None:
            self.last_ground_motion = (time.perf_counter(), motion.ground_centroid)

    def _on_robot_picked_up(self, cli, state):
        del cli
        if state:
            self.robot_picked_up = True
        else:
            # Robot put down - reset world frame origin.
            self.robot_picked_up = False
            pkt = protocol_encoder.SetOrigin(
                pose_frame_id=self.pose_frame_id + 1, pose_origin_id=self.pose.origin_id + 1)
            self.conn.send(pkt)

    def _on_robot_moving(self, cli, state):
        self.robot_moving = state

    def _on_animation_state(self, cli: conn.Connection, pkt: protocol_encoder.AnimationState) -> None:
        del cli
        self.num_anim_bytes_played = pkt.num_anim_bytes_played
        self.num_audio_frames_played = pkt.num_audio_frames_played
        self.enabled_anim_tracks = pkt.enabled_anim_tracks
        self.tag = pkt.tag
        self.client_drop_count = pkt.client_drop_count

    def _on_object_available(self, cli: conn.Connection, pkt: protocol_encoder.ObjectAvailable) -> None:
        del cli
        factory_id = pkt.factory_id
        object_type = protocol_encoder.ObjectType(pkt.object_type)
        obj = object.Object(factory_id=factory_id, object_type=object_type)
        if factory_id not in self.available_objects:
            self.available_objects[factory_id] = obj
            logger.debug("Object of type %s with S/N 0x%08x available.", str(obj.object_type), obj.factory_id)

    def _on_object_connection_state(self, cli: conn.Connection, pkt: protocol_encoder.ObjectConnectionState) -> None:
        del cli
        if pkt.connected:
            # Connected
            self.connected_objects[pkt.object_id] = {
                "factory_id": pkt.factory_id,
                "object_type": pkt.object_type,
            }
        else:
            # Disconnected
            if pkt.object_id in self.connected_objects:
                del self.connected_objects[pkt.object_id]

    def _on_debug_data(self, cli: conn.Connection, pkt: protocol_encoder.DebugData) -> None:
        del cli
        msg = robot_debug.get_debug_message(pkt.name_id, pkt.format_id, pkt.args)
        logger_robot.log(robot_debug.get_log_level(pkt.level), msg)

    def set_head_angle(self, angle: float, accel: float = 10.0, max_speed: float = 10.0,
                       duration: float = 0.0) -> None:
        pkt = protocol_encoder.SetHeadAngle(angle_rad=angle, accel_rad_per_sec2=accel,
                                            max_speed_rad_per_sec=max_speed, duration_sec=duration)
        self.conn.send(pkt)

    def move_head(self, speed: float) -> None:
        pkt = protocol_encoder.MoveHead(speed_rad_per_sec=speed)
        self.conn.send(pkt)

    def set_lift_height(self, height: float, accel: float = 10.0, max_speed: float = 10.0,
                        duration: float = 0.0) -> None:
        pkt = protocol_encoder.SetLiftHeight(height_mm=height, accel_rad_per_sec2=accel,
                                             max_speed_rad_per_sec=max_speed, duration_sec=duration)
        self.conn.send(pkt)

    def move_lift(self, speed: float) -> None:
        pkt = protocol_encoder.MoveLift(speed_rad_per_sec=speed)
        self.conn.send(pkt)

    def drive_wheels(self, lwheel_speed: float, rwheel_speed: float,
                     lwheel_acc: Optional[float] = 0.0, rwheel_acc: Optional[float] = 0.0,
                     duration: Optional[float] = None) -> None:
        pkt = protocol_encoder.DriveWheels(lwheel_speed_mmps=lwheel_speed, rwheel_speed_mmps=rwheel_speed,
                                           lwheel_accel_mmps2=lwheel_acc, rwheel_accel_mmps2=rwheel_acc)
        self.conn.send(pkt)
        if duration is not None:
            time.sleep(duration)
            self.stop_all_motors()

    def stop_all_motors(self) -> None:
        pkt = protocol_encoder.StopAllMotors()
        self.conn.send(pkt)

    def go_to_pose(self, pose: util.Pose, relative_to_robot: bool = False, wait: bool = True,
                   timeout: Optional[float] = None) -> bool:
        """
        Move to a pose - a position, then a heading - in a straight line, and say whether the robot got there.
        See execute_path() for waiting.
        """
        if relative_to_robot:
            pose = util.Pose(self.pose.position.x, self.pose.position.y,
                             self.pose.position.z, angle_z=self.pose.rotation.angle_z).define_pose_relative_this(pose)
        segments: List[protocol_base.Packet] = [
            protocol_encoder.AppendPathSegLine(
                from_x=self.pose.position.x, from_y=self.pose.position.y,
                to_x=pose.position.x, to_y=pose.position.y,
                speed_mmps=robot.PATH_SPEED, accel_mmps2=robot.PATH_ACCEL, decel_mmps2=robot.PATH_DECEL),
            self._point_turn(pose.position.x, pose.position.y, pose.rotation.angle_z.radians)]
        if timeout is None:
            distance = math.hypot(pose.position.x - self.pose.position.x, pose.position.y - self.pose.position.y)
            timeout = self._path_time(distance, robot.PATH_SPEED) + self._path_time(math.pi, robot.POINT_TURN_SPEED)
        return self.execute_path(segments, wait, timeout)

    def turn_in_place(self, angle: util.Angle, speed: float = robot.POINT_TURN_SPEED, wait: bool = True,
                      timeout: Optional[float] = None) -> bool:
        """
        Turn by an angle, left for positive, at a speed in rad/s, and say whether the robot got there. See
        execute_path() for waiting.
        """
        heading = self.pose.rotation.angle_z.radians + angle.radians
        segment = self._point_turn(self.pose.position.x, self.pose.position.y, heading, speed)
        if timeout is None:
            timeout = self._path_time(abs(angle.radians), speed)
        return self.execute_path([segment], wait, timeout)

    def drive_straight(self, distance: util.Distance, speed: float = robot.PATH_SPEED, wait: bool = True,
                       timeout: Optional[float] = None) -> bool:
        """
        Drive straight ahead, or back for a negative distance, at a speed in mm/s, and say whether the robot got
        there. See execute_path() for waiting.
        """
        x, y = self.pose.position.x, self.pose.position.y
        heading = self.pose.rotation.angle_z.radians
        segment = protocol_encoder.AppendPathSegLine(
            from_x=x, from_y=y,
            to_x=x + distance.mm * math.cos(heading), to_y=y + distance.mm * math.sin(heading),
            # A negative speed drives backwards, as Anki's engine did.
            speed_mmps=math.copysign(abs(speed), distance.mm), accel_mmps2=robot.PATH_ACCEL,
            decel_mmps2=robot.PATH_DECEL)
        if timeout is None:
            timeout = self._path_time(abs(distance.mm), abs(speed))
        return self.execute_path([segment], wait, timeout)

    def execute_path(self, segments: List[protocol_base.Packet], wait: bool = True,
                     timeout: Optional[float] = None) -> bool:
        """
        Have the robot follow path segments - AppendPathSegLine, AppendPathSegArc, AppendPathSegPointTurn - and
        say whether it got to the end.

        Without waiting, it says True as soon as the path has gone out. Waiting, it returns once the robot reports
        the path completed, True, or interrupted - a cliff, say - or once the timeout, in seconds, has run out,
        False. The report comes through the client's own dispatching, so this must not wait from a handler of
        the client's events: the brain's behaviors run on a thread of their own and can.
        """
        self._path_event_id = self._path_event_id % 0xffff + 1
        event_id = self._path_event_id
        result: List[bool] = []
        done = Event()

        def on_path_event(cli: conn.Connection, pkt: protocol_encoder.PathFollowingEvent) -> None:
            if pkt.event_id != event_id or pkt.event_type == protocol_encoder.PathEventType.PATH_STARTED:
                return
            result.append(pkt.event_type == protocol_encoder.PathEventType.PATH_COMPLETED)
            done.set()

        handler = self.add_handler(protocol_encoder.PathFollowingEvent, on_path_event) if wait else None
        try:
            for segment in segments:
                self.conn.send(segment)
            self.conn.send(protocol_encoder.ExecutePath(event_id=event_id))
            if handler is None:
                return True
            return done.wait(timeout) and result[0]
        finally:
            if handler is not None:
                self.del_handler(protocol_encoder.PathFollowingEvent, handler)

    @staticmethod
    def _point_turn(x: float, y: float, heading: float,
                    speed: float = robot.POINT_TURN_SPEED) -> protocol_encoder.AppendPathSegPointTurn:
        # Its speed and acceleration are angular, in rad/s and rad/s2, whatever the fields are named: Anki's
        # engine gave point turns in its path motion profile so. A speed of 40, as go_to_pose() used to send, the
        # robot turned at its 300 degrees a second.
        return protocol_encoder.AppendPathSegPointTurn(
            x=x, y=y, angle_rad=heading, angle_tolerance_rad=robot.POINT_TURN_TOLERANCE,
            speed_mmps=speed, accel_mmps2=robot.POINT_TURN_ACCEL, decel_mmps2=robot.POINT_TURN_ACCEL)

    @staticmethod
    def _path_time(amount: float, speed: float) -> float:
        """ A generous time for the robot to cover an amount of mm, or radians, at a speed. """
        return 2.0 * amount / max(abs(speed), 1e-3) + 3.0

    def enable_stop_on_cliff(self, enable: bool = True) -> None:
        """ Have the robot stop by itself at a cliff, as the Cozmo application had it. """
        self.conn.send(protocol_encoder.EnableStopOnCliff(enable=enable))

    def set_camera_exposure(self, exposure_ms: int, gain: float) -> None:
        """ Set the camera's exposure, in ms, and gain, and turn automatic exposure off. """
        self.conn.send(protocol_encoder.SetCameraParams(gain=gain, exposure_ms=exposure_ms,
                                                        auto_exposure_enabled=False))

    def enable_auto_exposure(self) -> None:
        """ Let the camera set its exposure itself again, after set_camera_exposure(). """
        self.conn.send(protocol_encoder.SetCameraParams(gain=0.0, exposure_ms=0, auto_exposure_enabled=True))

    def set_backpack_lights(self,
                            left_light: protocol_encoder.LightState,
                            front_light: protocol_encoder.LightState,
                            center_light: protocol_encoder.LightState,
                            rear_light: protocol_encoder.LightState,
                            right_light: protocol_encoder.LightState) -> None:
        pkt: protocol_base.Packet = protocol_encoder.LightStateCenter(states=(front_light, center_light, rear_light))
        self.conn.send(pkt)
        pkt = protocol_encoder.LightStateSide(states=(left_light, right_light))
        self.conn.send(pkt)

    def set_center_backpack_lights(self, light: protocol_encoder.LightState) -> None:
        self.set_backpack_lights(lights.off_light, light, light, light, lights.off_light)

    def set_all_backpack_lights(self, light: protocol_encoder.LightState) -> None:
        self.set_backpack_lights(light, light, light, light, light)

    def set_backpack_lights_off(self) -> None:
        self.set_backpack_lights(lights.off_light, lights.off_light, lights.off_light,
                                 lights.off_light, lights.off_light)

    def set_head_light(self, enable: bool) -> None:
        pkt = protocol_encoder.SetHeadLight(enable=enable)
        self.conn.send(pkt)

    def enable_camera(self, enable: bool = True, color: bool = False) -> None:
        """ Enable or disable camera image streaming in color or grayscale. """
        image_send_mode = protocol_encoder.ImageSendMode.Stream if enable else protocol_encoder.ImageSendMode.Off
        pkt: protocol_base.Packet = protocol_encoder.EnableCamera(image_send_mode=image_send_mode)
        self.conn.send(pkt)
        pkt = protocol_encoder.EnableColorImages(enable=color)
        self.conn.send(pkt)

    def read_camera_calibration(self, timeout: float = 5.0) -> Optional[camera.CameraCalibration]:
        """
        Read the camera's factory calibration from the robot's NV storage.

        Returns None if the robot has none or does not answer in time. Blocks until then, so it must not
        be called from an event handler: the answer arrives through the same dispatching.
        """
        tag = protocol_encoder.NvEntryTag.NVEntry_CameraCalib
        chunks: List[bytes] = []
        results: List[protocol_encoder.NvResult] = []
        done = Event()

        def on_result(cli: conn.Connection, pkt: protocol_encoder.NvStorageOpResult) -> None:
            del cli
            if pkt.tag != tag:
                return
            chunks.append(bytes(pkt.data))
            if pkt.result != protocol_encoder.NvResult.NV_MORE:
                results.append(protocol_encoder.NvResult(pkt.result))
                done.set()

        handler = self.add_handler(protocol_encoder.NvStorageOpResult, on_result)
        try:
            self.conn.send(protocol_encoder.NvStorageOp(tag=tag, length=1, op=protocol_encoder.NvOperation.NVOP_READ))
            done.wait(timeout)
        finally:
            self.del_handler(protocol_encoder.NvStorageOpResult, handler)
        if not results or results[0] != protocol_encoder.NvResult.NV_OKAY:
            return None
        try:
            self.camera_calibration = camera.CameraCalibration.from_nv(b"".join(chunks))
            return self.camera_calibration
        except ValueError as e:
            logger.warning("Unreadable camera calibration: %s", e)
            return None

    def clear_screen(self) -> None:
        pkt = protocol_encoder.DisplayImage(image=b"\x3f\x3f")
        self.anim_controller.display_image(pkt)

    def display_image(self, im: Image.Image, duration: Optional[float] = None) -> None:
        encoder = image_encoder.ImageEncoder(im)
        buf = bytes(encoder.encode())
        pkt = protocol_encoder.DisplayImage(image=buf)
        self.anim_controller.display_image(pkt)
        if duration is not None:
            time.sleep(duration)
            self.clear_screen()

    def _load_clips(self, fspec: str) -> None:

        start_time = time.perf_counter()

        if fspec.endswith(".bin"):
            clips = anim_encoder.AnimClips.from_fb_file(fspec)
        elif fspec.endswith(".json"):
            clips = anim_encoder.AnimClips.from_json_file(fspec)
        else:
            raise ValueError("Unsupported animation file format.")
        for clip in clips.clips:
            self._clips[clip.name] = clip

        logger.debug("Loaded {} in {:.02f} s.".format(fspec, time.perf_counter() - start_time))

    def play_anim_ppclip(self, ppclip: anim.PreprocessedClip) -> None:

        # Ensure no other animation is playing. Cancelling drops the expectation, so the end of
        # the animation being cancelled cannot be taken for the end of this one.
        self.cancel_anim()

        # Start animation. The identifier is what tells the two apart; it is a uint8 on the wire.
        anim_id = self._next_anim_id
        self._next_anim_id = self._next_anim_id % 255 + 1
        self.anim_controller.expect_anim(anim_id)
        pkt: protocol_base.Packet = protocol_encoder.StartAnimation(anim_id=anim_id)
        self.anim_controller.play_anim_frame(None, None, (pkt, ))

        # Send frames to the animation controller. The robot plays one frame every
        # robot.FRAME_MS, so a keyframe belongs to the frame its time falls on, and a frame no
        # keyframe falls on is sent empty. 97 % of the keyframes in the resources sit exactly on
        # that grid; the rest are rounded to the nearest frame, and the few that then land on the
        # same frame share it - the robot has one slot per frame - rather than being spread over
        # consecutive ones, which would stretch the animation.
        frames: Dict[int, List[protocol_encoder.Packet]] = defaultdict(list)
        for keyframes in (ppclip.keyframes,
                          ppclip.motion_keyframes(self.head_angle.degrees, self.lift_position.height.mm)):
            for time_ms in sorted(keyframes.keys()):
                frames[round(time_ms / robot.FRAME_MS)] += keyframes[time_ms]
        num_frames = max(frames) + 1 if frames else 0

        for i in range(num_frames):
            audio_pkt = None
            image_pkt = None
            pkts = []
            # A frame has one speaker and one screen, so the last of each wins; everything else
            # goes out together.
            for action in frames[i]:
                if isinstance(action, protocol_encoder.OutputAudio):
                    audio_pkt = action
                elif isinstance(action, protocol_encoder.DisplayImage):
                    image_pkt = action
                elif isinstance(action, protocol_encoder.Packet):
                    pkts.append(action)
            self.anim_controller.play_anim_frame(audio_pkt, image_pkt, pkts)

        # End animation.
        pkt = protocol_encoder.EndAnimation()
        self.anim_controller.play_anim_frame(None, None, (pkt, ))

    def play_anim(self, name: str) -> None:
        if not self._clip_metadata:
            raise ValueError("Animations not loaded.")
        elif name not in self._clip_metadata:
            raise ValueError("Unknown clip name.")

        if name not in self._ppclips:
            if name not in self._clips:
                self._load_clips(self._clip_metadata[name].fspec)
            clip = self._clips[name]
            self._ppclips[name] = anim.PreprocessedClip.from_anim_clip(clip, self.audio_library,
                                                                       self.face_animation_dir)
            if any(isinstance(keyframe, anim_encoder.AnimRobotAudio) and
                   any(self.audio_library.is_song(e) for e in keyframe.audio_event_ids)
                   for keyframe in clip.keyframes):
                self._singing_clips.add(name)

        ppclip = self._ppclips[name]
        self.play_anim_ppclip(ppclip)

    def cancel_anim(self) -> None:
        self.anim_controller.cancel_anim()

    def set_audio_switch(self, group: str, value: str) -> None:
        """
        Set a WWise switch group to a value: which song the singing animations sing, for one - the group
        Cozmo_Sings_100Bpm to Cozmo_Sings_Pop_Goes_The_Weasel, say. See AudioLibrary.set_switch().
        """
        self.audio_library.set_switch(group, value)
        for name in self._singing_clips:
            self._ppclips.pop(name, None)
        self._singing_clips.clear()

    def play_anim_group(self, anim_group_name: str) -> None:
        logger_animation.info("Playing animation group {}".format(anim_group_name))
        animation_group = self.animation_groups.get(anim_group_name)
        if not animation_group:
            logger_animation.error("Failed to find animation group {}.".format(anim_group_name))
            return
        member = animation_group.choose_member(self.head_angle)
        logger_animation.info("Playing animation {}".format(member.name))
        self.play_anim(member.name)

    def load_anims(self) -> None:
        util.check_assets()
        anim_dir = str(util.get_cozmo_anim_dir())
        self._clip_metadata = anim_encoder.get_clip_metadata(anim_dir)
        self._clips = {}
        resource_dir = str(util.get_cozmo_asset_dir())
        self.animation_groups = anim.load_animation_groups(resource_dir)
        self.audio_library = audiolib.load_audio_library(resource_dir)
        self.face_animation_dir = os.path.join(resource_dir, 'cozmo_resources', 'assets', 'faceAnimations')

    def get_anim_names(self) -> set:
        return set(self._clip_metadata.keys())

    @property
    def anim_names(self) -> set:
        return self.get_anim_names()

    def set_volume(self, level: int) -> None:
        """ Set audio output volume to a level in the range 0-65535. """
        pkt = protocol_encoder.SetRobotVolume(level=level)
        self.conn.send(pkt)

    def play_audio(self, fspec: str) -> None:
        pkts = audio.load_wav(fspec)
        self.anim_controller.play_audio(pkts)

    def activate_behavior(self, behavior: Any, dispatcher: Optional[event.Dispatcher] = None) -> None:
        """
        Put a behavior on the robot. It gets its events from the dispatcher given - the client itself by
        default, which dispatches them on the thread that handles everything the robot sends.
        """
        (dispatcher or self).add_child_dispatcher(behavior)
        behavior.deactivated = False
        behavior.activate()

    def deactivate_behavior(self, behavior: Any, dispatcher: Optional[event.Dispatcher] = None) -> None:
        (dispatcher or self).del_child_dispatcher(behavior)
        # Set before deactivating, so that anything the behavior is still waiting on cannot report
        # it done and end whatever takes its place.
        behavior.deactivated = True
        behavior.deactivate()

    def enable_animations(self, enabled: bool = True) -> None:
        self.anim_controller.enable_animations(enabled)

    def enable_procedural_face(self, enabled: bool = True) -> None:
        self.anim_controller.enable_procedural_face(enabled)
