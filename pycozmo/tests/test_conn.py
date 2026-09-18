
import unittest
import socket
from threading import Event

import pycozmo


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
