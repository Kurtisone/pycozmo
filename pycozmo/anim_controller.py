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

    #: Frames sent ahead of what the robot has played, at most. What is buffered keeps the robot playing
    #: through a hiccup of the link - silences of up to 0.35 s were measured - but everything queued ahead
    #: of an animation delays it: ten frames are a third of a second.
    MAX_FRAMES_AHEAD = 10
    #: Room each message takes in the robot's animation buffer, over its length: an OutputAudio of 744 bytes
    #: "needed 747".
    MESSAGE_OVERHEAD = 3
    #: Bytes sent ahead of what the robot has played, at most. Its animation buffer holds 8 KB: a robot that
    #: ran out of room held 7 591 bytes, exactly the frames it had not played, and had 601 left.
    MAX_BYTES_AHEAD = 8192 - 512
    #: How long the robot may go without playing anything before frames go out however many it holds - never
    #: more than its buffer takes, though. A robot that is not playing is not making room.
    MAX_HOLD = 1.0
    #: How long it may go before what it was sent is taken for played, and frames go out a tick at a time
    #: again. A count gone wrong would otherwise hold every animation up for good.
    MAX_STALL = 5.0

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
        # Image believed to be on the robot's screen, and when it was sent. See _image_due() .
        self.displayed_image: Optional[bytes] = None
        self.displayed_time = 0.0
        self.face_generator = iter(procedural_face.ProceduralFaceGenerator())
        self.animations_enabled = False
        self.procedural_face_enabled = False
        # Packets to go out with the next frame, right after its audio. See cancel_anim() .
        self._pending: List[protocol_base.Packet] = []
        # Whether a StartAnimation has gone out that no EndAnimation has closed yet.
        self._animation_open = False
        self._pending_lock = Lock()
        # Flow control. What the robot last reported having played; None until it reports having played
        # anything, and for as long as it does not, frames go out at the frame rate. See _has_room() .
        self.animation_state: Optional[protocol_encoder.AnimationState] = None
        # The report last counted, its two counts as they came, and the same since this connection, without
        # the wrapping. When the frames count last changed.
        self._counted: Optional[protocol_encoder.AnimationState] = None
        self._reported = (0, 0)
        self._frames_played = 0
        self._bytes_played = 0
        self._played_time = 0.0
        # The same counts for what was sent from here.
        self._frames_sent = 0
        self._bytes_sent = 0
        # Number, messages and bytes counted of each frame sent that the robot has not played yet, oldest first.
        self._unplayed: Deque[Tuple[int, int, int]] = deque()
        self._unplayed_messages = 0
        self._unplayed_bytes = 0
        # A frame taken from the queue that the robot had no room for yet.
        self._held: Optional[List[protocol_base.Packet]] = None

    def _clear_last_image_pkt(self):
        self.last_image_pkt = protocol_encoder.DisplayImage(image=b"\x3f\x3f")

    def start(self):
        # Nothing is known about the screen of a robot that was just connected to, and no animation
        # is awaited, whatever a previous run left behind.
        self.displayed_image = None
        self.displayed_time = 0.0
        self.expected_anim_id = None
        # Nor about what it has played.
        self.animation_state = None
        self._counted = None
        self._reported = (0, 0)
        self._frames_sent = 0
        self._bytes_sent = 0
        self._forget_unplayed()
        self._held = None
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
        # Kept as it comes, both counts together: the frame loop makes sense of it. See _count_played() .
        if not pkt.num_anim_bytes_played and self.animation_state is None:
            # Nothing played yet - or a robot, or an emulator, that does not say.
            return
        self.animation_state = pkt

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

    def _image_due(self, image_pkt: protocol_encoder.DisplayImage, now: float) -> bool:
        """
        Say whether a screen image has to go out, and note it as displayed if it does.

        The robot keeps the last image on its screen and only blanks it after
        DISPLAY_BLANKING_TIME with nothing new, so an image identical to the one already displayed
        goes out only to beat that deadline. The procedural face is redrawn on every frame but only
        comes out different about nine times a second, so two thirds of these packets used to carry
        an image that was already on the screen.
        """
        if image_pkt.image == self.displayed_image and \
                now - self.displayed_time < robot.DISPLAY_REFRESH_TIME:
            return False
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

        timer = util.FPSTimer(robot.FRAME_RATE)
        while not self.stop_flag:
            if self.animations_enabled:
                self._send_frames()
            else:
                # Frames queued while animations are off are dropped, not held back for later.
                self.queue.get()
            timer.sleep()

        logger.debug("Animation controller stopped...")

    def _send_frames(self) -> None:
        """
        Send as many frames as the robot has room for.

        Frames went out 30 times a second whatever the robot made of them. It plays them a little slower,
        29.9 a second, so the frames waiting on it kept piling up - from 8 to 17 in a minute of one
        session - and delayed every animation more. And when a sound started, the silences waiting, a few
        bytes each, gave way to 747-byte audio frames that its 8 KB buffer could not hold: "BufferFull",
        "Clearing Animation buffer", and the frames after it came out corrupt. A frame now goes out only
        once the robot has played enough of the ones before it.

        More than one can go out at a time: a frame a tick kept the robot no further ahead than the frames
        it happened to have, and a sound starting after a short silence had two or three to ride out a
        hiccup of the link with. A robot that does not say what it plays still gets one a tick.
        """
        now = time.perf_counter()
        for _ in range(self.MAX_FRAMES_AHEAD):
            if not self._send_frame(now) or not self._flow_controlled(now):
                break

    def _send_frame(self, now: Optional[float] = None) -> bool:
        """ Send the next frame if the robot has room for it, and say whether it did. """
        if now is None:
            now = time.perf_counter()
        if self._held is None:
            if not self._has_room(0, now):
                return False
            self._held = self._next_frame(now)
        size = sum(len(pkt) + self.MESSAGE_OVERHEAD for pkt in self._held)
        if not self._has_room(size, now):
            return False
        counted = 0
        for pkt in self._held:
            self.cli.conn.send(pkt)
            counted += len(pkt) + 1
        self._unplayed.append((self._frames_sent, len(self._held), counted))
        self._unplayed_messages += len(self._held)
        self._unplayed_bytes += counted
        self._frames_sent += 1
        self._bytes_sent += counted
        self._held = None
        return True

    def _flow_controlled(self, now: float) -> bool:
        """ Say whether frames go out as the robot plays them, rather than one a tick. """
        return self.animation_state is not None and now - self._played_time <= self.MAX_HOLD

    def _has_room(self, size: int, now: float) -> bool:
        """
        Say whether the robot has room for one more frame of this many bytes.

        The robot reports in AnimationState the frames it has played, silences too, and the bytes: every
        message it takes from its buffer counts its length and one more, and what it drops when it clears the
        buffer counts as played. Both matched what was sent, exactly, through the 25 s of a sound. The frames
        tell how far ahead of it this is. The bytes tell how much room there is: the samples of a sound count
        only once the next frame starts, and when that sound ended its last frame counted as played, but not
        its 744 samples. Counting by the frames alone, a robot playing animations ran out of room in most of
        those that followed one with a sound, and seldom in one that followed a cleared buffer: the samples a
        sound leaves uncounted are taken to keep their room until the buffer is cleared.
        """
        if self.animation_state is None:
            return True
        self._count_played(now)
        waited = now - self._played_time
        if waited > self.MAX_STALL:
            self._forget_unplayed()
            return True
        # The count leaves out 2 of the 3 bytes each message takes over its length.
        room = self._bytes_sent - self._bytes_played + (self.MESSAGE_OVERHEAD - 1) * self._unplayed_messages
        if room + size > self.MAX_BYTES_AHEAD:
            if self._unplayed:
                return False
            # With every frame played, what takes the room is the ends of sounds, which only a cleared buffer
            # gives back: waiting would only starve the robot. The frame goes out, and the robot clears its
            # buffer if it has to.
            self._bytes_played = self._bytes_sent
        # A robot that waits for more before it plays anything gets it, as much as its buffer takes.
        return self._frames_sent - self._frames_played < self.MAX_FRAMES_AHEAD or waited > self.MAX_HOLD

    def _count_played(self, now: float) -> None:
        """ Bring what the robot has played up to its last report. """
        state = self.animation_state
        if state is None or state is self._counted:
            return
        self._counted = state
        reported = (state.num_audio_frames_played, state.num_anim_bytes_played)
        frames = self._frames_played + self._change(reported[0], self._reported[0])
        played = self._bytes_played + self._change(reported[1], self._reported[1])
        self._reported = reported
        if frames != self._frames_played:
            self._played_time = now
        if not (self._frames_played <= frames <= self._frames_sent and self._bytes_played <= played):
            # Counting from scratch again, or counting frames that were not sent from here - a count carried
            # over from before this connection: what the robot still holds cannot be told apart any more.
            self._forget_unplayed()
            return
        self._frames_played = frames
        while self._unplayed and self._unplayed[0][0] < frames:
            _, messages, counted = self._unplayed.popleft()
            self._unplayed_messages -= messages
            self._unplayed_bytes -= counted
        # The bytes can run ahead of the frames: a cleared buffer counts as played the ends of sounds that never
        # counted. What is left to play is then what the frames say.
        self._bytes_played = min(played, self._bytes_sent - self._unplayed_bytes)

    @staticmethod
    def _change(count: int, last: int) -> int:
        """ How far a signed 32-bit count, which wraps, has gone since it was last. """
        return (count - last + 2 ** 31) % 2 ** 32 - 2 ** 31

    def _forget_unplayed(self) -> None:
        """ Take everything sent for played. """
        self._frames_played = self._frames_sent
        self._bytes_played = self._bytes_sent
        self._unplayed.clear()
        self._unplayed_messages = 0
        self._unplayed_bytes = 0

    def _next_frame(self, now: float) -> List[protocol_base.Packet]:
        """
        Take the next frame from the queue, as the packets to send.

        The robot reads its animation buffer a frame at a time, and a frame starts with its audio: every
        other animation message has to follow an OutputAudio or an OutputSilence. Anything else where a
        frame should start is reported as "Expecting either audio sample or silence next in animation
        buffer" and the frame is lost.

        EndAnimation also ends the frame it is in: the robot expects audio right after it. So it goes
        out in a frame of its own, silence and EndAnimation, and the queued frames wait a tick.
        """
        with self._pending_lock:
            pending, self._pending = self._pending, []
            if pending:
                self._animation_open = False
            else:
                # Taken together with noting what the frame opens or closes, so that cancel_anim()
                # cannot slip in between and miss a StartAnimation on its way out.
                audio_pkt, image_pkt, pkts = self.queue.get()
                for pkt in pkts or ():
                    if isinstance(pkt, protocol_encoder.StartAnimation):
                        self._animation_open = True
                    elif isinstance(pkt, protocol_encoder.EndAnimation):
                        self._animation_open = False
        if pending:
            return [protocol_encoder.OutputSilence()] + pending

        # Silence stands in for a missing audio frame, so the outgoing packet is not the queued one.
        frame: List[protocol_base.Packet] = []
        if audio_pkt:
            frame.append(audio_pkt)
            if not self.playing_audio:
                self.playing_audio = True
        else:
            frame.append(protocol_encoder.OutputSilence())
            if self.playing_audio:
                self.playing_audio = False
                self.cli.conn.post_event(event.EvtAudioCompleted, self.cli)

        if not image_pkt and self.procedural_face_enabled and not self.playing_animation:
            image_pkt = self._get_face_image()

        if image_pkt:
            self.last_image_pkt = image_pkt
        if self._image_due(self.last_image_pkt, now):
            frame.append(self.last_image_pkt)

        if pkts:
            frame.extend(pkts)
        return frame

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
        # Nothing is to be reported for an animation that is being abandoned.
        self.expected_anim_id = None
        if not (self.animations_enabled and self.thread is not None):
            # No frames are going out, so there is nothing to land in the middle of.
            self.queue.clear()
            self.cli.conn.send(protocol_encoder.EndAnimation())
            return
        with self._pending_lock:
            self.queue.clear()
            # EndAnimation belongs in a frame, after its audio, like any animation message, and ends
            # it. Sent from here, it went out between two of the frame loop's packets - and every
            # animation starts by cancelling the last one - so the robot found it where a frame should
            # start: "Got 0x9a instead", and the frame's image after it gave the same with 0x97. See
            # _next_frame() .
            #
            # And only an animation the robot has started needs ending. One that finished on its own
            # has sent its EndAnimation already, and a second one, for an animation no longer open,
            # is what the robot reported as "Got 0x9a instead" right after a clip ended and the next
            # began. One whose StartAnimation had not gone out yet was never started at all.
            if self._animation_open and not self._pending:
                self._pending.append(protocol_encoder.EndAnimation())

    def enable_animations(self, enabled: bool = True) -> None:
        self.animations_enabled = bool(enabled)

    def enable_procedural_face(self, enabled: bool = True) -> None:
        self.procedural_face_enabled = bool(enabled)
