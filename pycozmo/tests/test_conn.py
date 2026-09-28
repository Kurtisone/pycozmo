
import unittest
import socket
import time
from threading import Event, Thread
from typing import List
from unittest import mock

import pycozmo
from pycozmo.frame import Frame
from pycozmo.protocol_declaration import OOB_SEQ


class TestConnection(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        pycozmo.setup_basic_logging(log_level="DEBUG", protocol_log_level="DEBUG")

    def setUp(self):
        self.s_conn_e = Event()
        self.c_conn_e = Event()
        self.s_e = Event()
        self.c_e = Event()
        self.s = pycozmo.conn.Connection(server=True)
        self.s.add_handler(pycozmo.protocol_encoder.Connect, lambda cli, pkt: self.s_conn_e.set())
        self.c = pycozmo.conn.Connection(("127.0.0.1", 5551))
        self.c.add_handler(pycozmo.protocol_encoder.Connect, lambda cli, pkt: self.c_conn_e.set())

    def start(self):
        self.s.start()
        self.c.start()

    def stop(self):
        self.c.stop()
        self.s.stop()

    def connect(self):
        self.c.connect()
        self.assertTrue(self.s_conn_e.wait(2.0))
        self.assertTrue(self.c_conn_e.wait(2.0))

    def disconnect(self):
        self.c.disconnect()

    def test_connect(self):
        self.start()
        self.connect()
        self.disconnect()
        self.stop()

    def test_ping(self):
        self.s.add_handler(pycozmo.protocol_encoder.Ping, lambda cli, pkt: self.s_e.set())
        self.c.add_handler(pycozmo.protocol_encoder.Ping, lambda cli, pkt: self.c_e.set())
        self.start()
        self.connect()
        self.assertTrue(self.c_e.wait(20000.0))
        self.assertTrue(self.s_e.wait(2.0))
        self.stop()

    def test_send_30(self):
        COUNT = 30
        counts = []

        def on_set_robot_volume(cli, pkt):
            del cli
            counts.append(pkt.level)
            # Wake the main thread only once every packet has been handled. Waking on the value of the last packet
            # would let the assertion below run while a packet was still in flight.
            if len(counts) == COUNT:
                self.s_e.set()

        self.s.add_handler(pycozmo.protocol_encoder.SetRobotVolume, on_set_robot_volume)
        self.start()
        self.connect()
        for i in range(COUNT):
            self.c.send(pycozmo.protocol_encoder.SetRobotVolume(i))
        self.assertTrue(self.s_e.wait(5.0))
        self.assertEqual(counts, list(range(COUNT)))
        self.stop()

    def test_send_spanning_frames(self):
        # A burst large enough to fill a frame must still arrive. The frame has to advertise the sequence of its
        # own last packet: the peer numbers the packets it decodes starting from first_seq and rejects the whole
        # frame when the count does not match, so an off-by-one here loses every packet in the frame silently.
        COUNT = 20
        payload = bytes([0x3f] * 200)
        received = []

        def on_display_image(cli, pkt):
            del cli
            received.append(bytes(pkt.image))
            if len(received) == COUNT:
                self.s_e.set()

        self.s.add_handler(pycozmo.protocol_encoder.DisplayImage, on_display_image)
        self.start()
        self.connect()
        for _ in range(COUNT):
            self.c.send(pycozmo.protocol_encoder.DisplayImage(image=payload))
        self.assertTrue(self.s_e.wait(5.0))
        self.assertEqual(received, [payload] * COUNT)
        self.assertEqual(self.s.recv_thread.discarded_frames, 0)
        self.stop()


class TestSendThread(unittest.TestCase):

    def test_send_without_receiver(self):
        # On the server side the receiver address is unset until a client connects, and reset() clears it again.
        # A frame sent in that window has nowhere to go and must be discarded rather than killing the thread:
        # socket.sendto(data, None) raises TypeError, which the OSError handler does not catch.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        thread = pycozmo.conn.SendThread(sock, None)
        self.assertTrue(thread.server)
        discarded = thread.discarded_frames
        thread._send_raw_frame(b"\x00" * 8)
        self.assertEqual(thread.discarded_frames, discarded + 1)
        self.assertEqual(thread.sent_frames, 0)


class LossyTestCase(unittest.TestCase):
    """ A client and a server that talks like a robot, over a link that can lose frames. """

    def setUp(self):
        self.s = pycozmo.conn.Connection(server=True)
        self.c = pycozmo.conn.Connection(("127.0.0.1", 5551))
        connected = Event()
        self.c.add_handler(pycozmo.protocol_encoder.Connect, lambda cli, pkt: connected.set())
        self.s.start()
        self.c.start()
        self.addCleanup(self.stop)
        self.c.connect()
        self.assertTrue(connected.wait(2.0))
        self.talking = Event()
        self.talker = Thread()

    def stop(self):
        self.talking.clear()
        if self.talker.is_alive():
            self.talker.join()
        self.c.stop()
        self.s.stop()

    def talk(self, client):
        # The robot sends RobotState about 30 times a second: out-of-band packets, in frames that carry an
        # acknowledgement each. A client playing an animation sends a frame of its own as often.
        while self.talking.is_set():
            self.s.send(pycozmo.protocol_encoder.RobotState(cliff_data_raw=(0, 0, 0, 0)))
            if client:
                self.c.send(pycozmo.protocol_encoder.OutputSilence())
            time.sleep(1/30)

    def start_talking(self, client=False):
        self.talking.set()
        self.talker = Thread(target=self.talk, args=(client, ), daemon=True)
        self.talker.start()

    @staticmethod
    def lose_first(recv_thread, level):
        """ Make a receive thread lose the first frame carrying SetRobotVolume at that level. """
        handle_frame = recv_thread.handle_frame
        lost: List[Frame] = []

        def lossy(frame):
            if not lost and any(isinstance(pkt, pycozmo.protocol_encoder.SetRobotVolume) and pkt.level == level
                                for pkt in frame.pkts):
                lost.append(frame)
                return
            handle_frame(frame)

        recv_thread.handle_frame = lossy
        return lost


class TestResend(LossyTestCase):

    def test_a_packet_the_peer_missed_goes_out_again_while_it_keeps_talking(self):
        # The wait for a resend ran from the last frame heard, which a robot that keeps talking resets
        # every 33 ms: what it missed never went out again, and everything after piled up behind it.
        levels = []
        both = Event()

        def on_volume(cli, pkt):
            levels.append(pkt.level)
            if len(levels) == 2:
                both.set()

        self.s.add_handler(pycozmo.protocol_encoder.SetRobotVolume, on_volume)
        lost = self.lose_first(self.s.recv_thread, 2)
        self.start_talking()
        self.c.send(pycozmo.protocol_encoder.SetRobotVolume(1))
        time.sleep(0.2)
        self.c.send(pycozmo.protocol_encoder.SetRobotVolume(2))
        self.assertTrue(both.wait(2.0))
        self.assertEqual(levels, [1, 2])
        self.assertEqual(len(lost), 1)


class TestResendTimer(unittest.TestCase):

    def setUp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        self.thread = pycozmo.conn.SendThread(sock, ("127.0.0.1", 9))
        self.now = 10.0
        patcher = mock.patch("pycozmo.conn.time.perf_counter", lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)

    def put(self):
        with self.thread.lock:
            if self.thread.window.is_empty():
                self.thread.resend_time = self.now
            return self.thread.window.put(pycozmo.protocol_encoder.SetRobotVolume(1))

    def waits(self, until, hear=False):
        """ The time between one resend and the next, from now until then, a step of 10 ms at a time. """
        waits = []
        last = self.now
        while self.now < until:
            self.now += 0.01
            if hear:
                # A frame from the peer every 10 ms, acknowledging nothing new.
                self.thread.ack(OOB_SEQ, OOB_SEQ)
            if self.thread._resend_messages():
                # One step late at most.
                waits.append(round(self.now - last - 0.005, 1))
                last = self.now
        return waits

    def test_nothing_goes_out_again_before_the_peer_is_heard(self):
        self.put()
        self.assertEqual(self.waits(11.0), [])

    def test_each_resend_waits_twice_as_long_as_the_last(self):
        self.thread.ack(OOB_SEQ, OOB_SEQ)
        self.put()
        self.assertEqual(self.waits(14.0), [0.1, 0.2, 0.4, 0.8, 0.8, 0.8, 0.8])

    def test_an_acknowledgement_starts_the_wait_again(self):
        self.thread.ack(OOB_SEQ, OOB_SEQ)
        first = self.put()
        self.put()
        self.assertEqual(self.waits(10.5), [0.1, 0.2])
        self.thread.ack(first, OOB_SEQ)
        self.assertEqual(self.waits(11.0), [0.1, 0.2])

    def test_hearing_the_peer_without_an_acknowledgement_does_not_hold_resends_back(self):
        self.thread.ack(OOB_SEQ, OOB_SEQ)
        self.put()
        self.assertEqual(self.waits(10.5, hear=True), [0.1, 0.2])
