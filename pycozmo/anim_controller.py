"""

Animation controller for audio, image, and animation playback.

"""

from typing import Any, Deque, Iterable, List, Optional, Tuple
from threading import Thread, Lock
import time
from collections import deque

from .logger import logger
from . import conn
from . import protocol_base
from . import protocol_encoder
from . import util
from . import robot
from . import event
from . import procedural_face
from . import image_encoder


class AnimationQueue:
    """ Synchronized animation queue class. """

    MAXLEN = 4500   # ~2.5 min of frames

    def __init__(self) -> None:
        self.lock = Lock()
        self.audio_queue: Deque[Optional[protocol_encoder.OutputAudio]] = deque(maxlen=self.MAXLEN)
        self.image_queue: Deque[Optional[protocol_encoder.DisplayImage]] = deque(maxlen=self.MAXLEN)
        self.pkt_queue: Deque[Optional[Iterable[protocol_encoder.Packet]]] = deque(maxlen=self.MAXLEN)

    def is_empty(self) -> bool:
        with self.lock:
            return not len(self.audio_queue) and \
                   not len(self.image_queue) and \
                   not len(self.pkt_queue)

    def put_audio(self, pkts: List[protocol_encoder.OutputAudio]) -> None:
        with self.lock:
            self.audio_queue.extend(pkts)

    def put_image(self, pkt: protocol_encoder.DisplayImage) -> None:
        with self.lock:
            self.image_queue.append(pkt)

    def put_anim_frame(
            self,
            audio_pkt: Optional[protocol_encoder.OutputAudio],
            image_pkt: Optional[protocol_encoder.DisplayImage],
            pkts: Optional[Iterable[protocol_encoder.Packet]]) -> None:
        with self.lock:
            self.audio_queue.append(audio_pkt)
            self.image_queue.append(image_pkt)
            self.pkt_queue.append(pkts)

    def get(self) -> Tuple[Optional[protocol_encoder.OutputAudio],
                           Optional[protocol_encoder.DisplayImage],
                           Optional[Iterable[protocol_encoder.Packet]]]:
        with self.lock:
            # Audio
            try:
                audio_pkt = self.audio_queue.popleft()
            except IndexError:
                audio_pkt = None
            # Image
            try:
                image_pkt = self.image_queue.popleft()
            except IndexError:
                image_pkt = None
            # Action
            try:
                pkts = self.pkt_queue.popleft()
            except IndexError:
                pkts = None

        return audio_pkt, image_pkt, pkts

    def clear(self) -> None:
        with self.lock:
            self.audio_queue.clear()
            self.image_queue.clear()
            self.pkt_queue.clear()


class AnimationController:
    """ Animation controller class. """

    def __init__(self, cli):
        self.cli = cli
        self.thread = None
        self.stop_flag = False
        self.queue = AnimationQueue()
        self.num_frames_played = -1
        self.playing_audio = False
        self.playing_animation = False
        # Identifier of the animation whose end is to be reported as a completion. See
        # expect_anim() and _on_animation_ended() .
        self.expected_anim_id: Optional[int] = None
        self.last_image_pkt = protocol_encoder.DisplayImage(image=b"\x3f\x3f")
        # Image believed to be on the robot's screen, and when it was sent. See _send_image() .
        self.displayed_image: Optional[bytes] = None
        self.displayed_time = 0.0
        self.face_generator = iter(procedural_face.ProceduralFaceGenerator())
        self.animations_enabled = False
        self.procedural_face_enabled = False

    def _clear_last_image_pkt(self):
        self.last_image_pkt = protocol_encoder.DisplayImage(image=b"\x3f\x3f")

    def start(self):
        # Nothing is known about the screen of a robot that was just connected to, and no animation
        # is awaited, whatever a previous run left behind.
        self.displayed_image = None
        self.displayed_time = 0.0
        self.expected_anim_id = None
        # __class__ is bound inside a method body; the checker does not model it.
        self.thread = Thread(
            daemon=True, name=__class__.__name__, target=self._run)  # type: ignore[name-defined]
        self.stop_flag = False
        self.thread.start()
        self.cli.add_handler(protocol_encoder.AnimationState, self._on_animation_state)
        self.cli.add_handler(protocol_encoder.Keyframe, self._on_keyframe)
        self.cli.add_handler(protocol_encoder.AnimationStarted, self._on_animation_started)
        self.cli.add_handler(protocol_encoder.AnimationEnded, self._on_animation_ended)
        self.cli.add_handler(event.EvtRobotAnimatingChange, self._on_animating_change)
        self.cli.add_handler(event.EvtRobotAnimBufferFullChange, self._on_anim_buffer_full_change)
        self.cli.add_handler(event.EvtRobotAnimatingIdleChange, self._on_amimating_idle_change)

    def stop(self):
        self.stop_flag = True
        if self.thread:
            self.thread.join()
            self.thread = None

    def _on_animation_state(self, cli: conn.Connection, pkt: protocol_encoder.AnimationState) -> None:
        self.num_audio_frames_played = pkt.num_audio_frames_played

    def _on_keyframe(self, cli: conn.Connection, pkt: protocol_encoder.Keyframe) -> None:
        pass

    def _on_animation_started(self, cli: conn.Connection, pkt: protocol_encoder.AnimationStarted) -> None:
        self.playing_animation = True
        # The robot acknowledges every animation it starts, whoever started it, so its answer is
        # what the end is matched against. An application is free to send StartAnimation itself.
        self.expected_anim_id = pkt.anim_id

    def expect_anim(self, anim_id: int) -> None:
        """
        Note the animation whose end is to be reported as a completion.

        The acknowledgement from the robot sets this as well. Recording it here too means an
        animation still completes on a robot that does not acknowledge having started it.
        """
        self.expected_anim_id = anim_id

    def _on_animation_ended(self, cli: conn.Connection, pkt: protocol_encoder.AnimationEnded) -> None:
        # Whichever animation the robot reports ending, nothing is playing on it any more, and the
        # procedural face is free to take the screen back.
        self.playing_animation = False
        self._clear_last_image_pkt()
        if pkt.anim_id != self.expected_anim_id:
            # The end of an animation that was cancelled or abandoned. EndAnimation carries no
            # identifier, so the robot answers with the one that was actually playing; reporting
            # that as a completion would have whatever plays next take its own animation for
            # already finished.
            logger.debug("Ignoring the end of animation %s, expecting %s.",
                         pkt.anim_id, self.expected_anim_id)
            return
        self.expected_anim_id = None
        self.cli.conn.post_event(event.EvtAnimationCompleted, self.cli)

    # These three are registered for status flag change events, which the client dispatches with
    # itself and the new state. Taking no argument raised TypeError out of the dispatch, which
    # aborted the rest of the robot state handling - the remaining flag changes and the orientation
    # update included. The client is typed Any because importing it here would be circular.
    def _on_animating_change(self, cli: Any, state: bool) -> None:
        pass

    def _on_anim_buffer_full_change(self, cli: Any, state: bool) -> None:
        pass

    def _on_amimating_idle_change(self, cli: Any, state: bool) -> None:
        pass

    def _send_image(self, image_pkt: protocol_encoder.DisplayImage, now: float) -> bool:
        """
        Send a screen image if it has to go out, and say whether it did.

        The robot keeps the last image on its screen and only blanks it after
        DISPLAY_BLANKING_TIME with nothing new, so an image identical to the one already displayed
        goes out only to beat that deadline. The procedural face is redrawn on every frame but only
        comes out different about nine times a second, so two thirds of these packets used to carry
        an image that was already on the screen.
        """
        if image_pkt.image == self.displayed_image and \
                now - self.displayed_time < robot.DISPLAY_REFRESH_TIME:
            return False
        self.cli.conn.send(image_pkt)
        self.displayed_image = image_pkt.image
        self.displayed_time = now
        return True

    def _get_face_image(self):
        im = next(self.face_generator)
        if not im:
            return None
        encoder = image_encoder.ImageEncoder(im)
        buf = bytes(encoder.encode())
        image_pkt = protocol_encoder.DisplayImage(image=buf)
        return image_pkt

    def _run(self):
        logger.debug("Animation controller started...")

        # Enable animation playback and AnimationState events. Requires Enable (0x25).
        pkt: protocol_base.Packet = protocol_encoder.EnableAnimationState()
        self.cli.conn.send(pkt)

        num_frames = 0

        timer = util.FPSTimer(robot.FRAME_RATE)
        while not self.stop_flag:

            audio_pkt, image_pkt, pkts = self.queue.get()

            if self.animations_enabled:
                # Silence stands in for a missing audio frame, so the outgoing packet is not the queued one.
                audio_out: protocol_base.Packet
                if audio_pkt:
                    audio_out = audio_pkt
                    if not self.playing_audio:
                        self.playing_audio = True
                else:
                    audio_out = protocol_encoder.OutputSilence()
                    if self.playing_audio:
                        self.playing_audio = False
                        self.cli.conn.post_event(event.EvtAudioCompleted, self.cli)
                self.cli.conn.send(audio_out)

                if not image_pkt and self.procedural_face_enabled and not self.playing_animation:
                    image_pkt = self._get_face_image()

                if image_pkt:
                    self.last_image_pkt = image_pkt
                self._send_image(self.last_image_pkt, time.perf_counter())

                if pkts:
                    for pkt in pkts:
                        self.cli.conn.send(pkt)

                num_frames += 1

            timer.sleep()

        logger.debug("Animation controller stopped...")

    def play_audio(self, pkts: List[protocol_encoder.OutputAudio]) -> None:
        self.queue.put_audio(pkts)

    def display_image(self, pkt: protocol_encoder.DisplayImage) -> None:
        self.queue.put_image(pkt)

    def play_anim_frame(
            self,
            audio_pkt: Optional[protocol_encoder.OutputAudio],
            image_pkt: Optional[protocol_encoder.DisplayImage],
            pkts: Optional[Iterable[protocol_encoder.Packet]]) -> None:
        self.queue.put_anim_frame(audio_pkt, image_pkt, pkts)

    def cancel_anim(self):
        self.queue.clear()
        # Nothing is to be reported for an animation that is being abandoned.
        self.expected_anim_id = None
        pkt = protocol_encoder.EndAnimation()
        self.cli.conn.send(pkt)

    def enable_animations(self, enabled: bool = True) -> None:
        self.animations_enabled = bool(enabled)

    def enable_procedural_face(self, enabled: bool = True) -> None:
        self.procedural_face_enabled = bool(enabled)
