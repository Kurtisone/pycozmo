import unittest
import threading
import time

import pycozmo


class TestWaitFor(unittest.TestCase):

    def setUp(self):
        self.dispatcher = pycozmo.event.Dispatcher()

    def _dispatch_later(self, *args):
        def run():
            time.sleep(0.05)
            self.dispatcher.dispatch(*args)
        t = threading.Thread(target=run)
        t.start()
        self.addCleanup(t.join)

    def test_no_arguments(self):
        self._dispatch_later(pycozmo.event.EvtRobotReady)
        self.dispatcher.wait_for(pycozmo.event.EvtRobotReady, timeout=2.0)

    def test_one_argument(self):
        self._dispatch_later(pycozmo.event.EvtRobotReady, self.dispatcher)
        self.dispatcher.wait_for(pycozmo.event.EvtRobotReady, timeout=2.0)

    def test_extra_arguments(self):
        # Events carrying a payload are dispatched with more than one argument. A handler registered by wait_for
        # has to accept them, or the waiter times out while the dispatching thread raises TypeError.
        self._dispatch_later(pycozmo.event.EvtNewRawCameraImage, self.dispatcher, "image")
        self.dispatcher.wait_for(pycozmo.event.EvtNewRawCameraImage, timeout=2.0)

    def test_timeout(self):
        with self.assertRaises(pycozmo.exception.Timeout):
            self.dispatcher.wait_for(pycozmo.event.EvtRobotReady, timeout=0.05)

    def test_timeout_names_the_event(self):
        with self.assertRaises(pycozmo.exception.Timeout) as ctx:
            self.dispatcher.wait_for(pycozmo.event.EvtRobotReady, timeout=0.05)
        self.assertIn("EvtRobotReady", str(ctx.exception))

    def test_dispatch_to_child(self):
        child = pycozmo.event.Dispatcher()
        self.dispatcher.add_child_dispatcher(child)
        self._dispatch_later(pycozmo.event.EvtNewRawCameraImage, self.dispatcher, "image")
        child.wait_for(pycozmo.event.EvtNewRawCameraImage, timeout=2.0)


class TestClientWaitFor(unittest.TestCase):

    def setUp(self):
        self.cli = pycozmo.Client()
        # The client opens no connection here, but its socket object is created in the constructor.
        self.addCleanup(self.cli.conn.sock.close)

    def test_client_does_not_override_wait_for(self):
        # Client used to carry its own copy of wait_for whose handler accepted a single argument, so waiting for any
        # event with a payload timed out. It has to keep using the one from Dispatcher.
        self.assertIs(pycozmo.Client.wait_for, pycozmo.event.Dispatcher.wait_for)

    def test_wait_for_event_with_payload(self):
        # EvtNewRawCameraImage is dispatched with the client and the image. Waiting for it has to return rather
        # than time out, and the dispatching thread must not raise.
        raised = []

        def dispatch():
            time.sleep(0.05)
            try:
                self.cli.dispatch(pycozmo.event.EvtNewRawCameraImage, self.cli, "image")
            except BaseException as e:      # noqa: B902 - the point is that nothing escapes
                raised.append(e)

        t = threading.Thread(target=dispatch)
        t.start()
        self.addCleanup(t.join)
        self.cli.wait_for(pycozmo.event.EvtNewRawCameraImage, timeout=2.0)
        t.join()
        self.assertEqual(raised, [])
