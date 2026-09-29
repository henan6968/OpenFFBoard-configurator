"""Tests for safe_serial.

The contract these lock in is simple: no call made from the GUI thread may
block for an unbounded time. The original freeze came from a Windows kernel
IOCTL inside ``QSerialPort.open()`` that has no timeout at all, so the fix is
to run the open sequence on a worker thread and give up on it from the GUI
side after a fixed budget.

Run with:  python -m unittest tests.test_safe_serial_opener -v
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import PyQt6.QtCore  # noqa: E402
import PyQt6.QtWidgets  # noqa: E402

import safe_serial  # noqa: E402


APP = None


def setUpModule():  # noqa: N802
    """Qt objects need an application instance."""
    global APP  # pylint: disable=global-statement
    APP = PyQt6.QtWidgets.QApplication.instance() or PyQt6.QtWidgets.QApplication([])


def pump(seconds, until=None, interval=0.01):
    """Run the event loop for up to ``seconds``, or until ``until()`` is true."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        APP.processEvents()
        if until is not None and until():
            return True
        time.sleep(interval)
    APP.processEvents()
    return bool(until is not None and until())


class NeverFinishingWorker(PyQt6.QtCore.QObject):
    """A worker standing in for a thread stuck in a kernel IOCTL.

    It exposes the same signal the real worker has, so ``PortOpener`` cannot
    tell the difference -- which is the point: the opener must stay usable
    even when the worker never reports back.
    """

    finished = PyQt6.QtCore.pyqtSignal(object)

    def __init__(self, port_name, baudrate):
        super().__init__()
        self.port_name = port_name
        self.baudrate = baudrate

    def abort(self):
        """Called when the opener gives up: stop as soon as possible."""
        self._aborted = True

    @PyQt6.QtCore.pyqtSlot()
    def run(self):
        """Simulate a thread stuck in a kernel IOCTL, until abandoned."""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if getattr(self, '_aborted', False):
                return
            time.sleep(0.01)


class TestOpenResult(unittest.TestCase):
    """The result object is what the UI reasons about."""

    def test_ok_and_timed_out_are_mutually_exclusive(self):
        """A result is either a success, a timeout, or a plain failure."""
        opened = safe_serial.OpenResult('opened')
        timeout = safe_serial.OpenResult('timeout')
        failed = safe_serial.OpenResult('failed', None, 'busy')

        self.assertTrue(opened.ok)
        self.assertFalse(opened.timed_out)
        self.assertFalse(timeout.ok)
        self.assertTrue(timeout.timed_out)
        self.assertFalse(failed.ok)
        self.assertFalse(failed.timed_out)


class TestBoundedWaits(unittest.TestCase):
    """The waits that do take a timeout must never overrun it."""

    def test_wait_for_ready_read_on_closed_port(self):
        """Waiting on a closed port returns at once."""
        port = safe_serial.SafeSerialPort()
        started = time.monotonic()
        self.assertFalse(port.waitForReadyRead(1000))
        self.assertLess(time.monotonic() - started, 0.2)

    def test_wait_for_bytes_written_on_closed_port(self):
        """Waiting to write on a closed port returns at once, and fails."""
        port = safe_serial.SafeSerialPort()
        started = time.monotonic()
        self.assertFalse(port.waitForBytesWritten(1000))
        self.assertLess(time.monotonic() - started, 0.2)

    def test_negative_timeout_is_capped(self):
        """A caller asking to wait forever still gets the ceiling."""
        port = safe_serial.SafeSerialPort()
        started = time.monotonic()
        port.waitForReadyRead(-1)
        self.assertLess(
            time.monotonic() - started, safe_serial.WAIT_TIMEOUT_MS / 1000.0 + 0.75
        )


class TestPortOpener(unittest.TestCase):
    """The opener is what keeps the GUI thread free."""

    def setUp(self):  # noqa: N802
        self._real_worker = safe_serial.OpenPortWorker
        self._openers = []

    def tearDown(self):  # noqa: N802
        safe_serial.OpenPortWorker = self._real_worker
        for opener in self._openers:
            opener.stop_all(2000)

    def _opener(self):
        """Build a PortOpener that is guaranteed to be torn down."""
        opener = safe_serial.PortOpener()
        self._openers.append(opener)
        return opener

    def test_returns_immediately_even_if_the_worker_never_finishes(self):
        """Starting an open must not wait for it."""
        safe_serial.OpenPortWorker = NeverFinishingWorker
        opener = self._opener()

        started = time.monotonic()
        self.assertTrue(opener.open("COM_NOT_A_REAL_PORT", 115200))
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.25, "open() blocked the calling thread")
        opener.abandon()

    def test_timeout_is_detected_and_abandoned(self):
        """A worker that never reports is written off after OPEN_TIMEOUT_MS."""
        safe_serial.OpenPortWorker = NeverFinishingWorker
        opener = self._opener()
        opener.open("COM_NOT_A_REAL_PORT", 115200)

        self.assertTrue(opener.busy)
        started = time.monotonic()
        waited = pump(4.0, until=lambda: opener.timed_out)
        elapsed_ms = (time.monotonic() - started) * 1000
        self.assertTrue(waited, "the opener never reported a timeout")
        self.assertGreaterEqual(elapsed_ms + 50, safe_serial.OPEN_TIMEOUT_MS)

        result = opener.abandon()
        self.assertIsNotNone(result)
        self.assertTrue(result.timed_out)
        self.assertFalse(opener.busy)
        self.assertEqual(len(opener.graveyard), 1)

        # The abandoned worker must actually release its thread, or every
        # failed attempt would leak one for the lifetime of the process.
        self.assertTrue(opener.stop_thread(2000), "the worker thread did not stop")
        # The finished signal is delivered through the event loop.
        pump(0.5, until=lambda: opener.thread_count == 0)
        self.assertEqual(opener.thread_count, 0)

    def test_poll_is_none_while_working(self):
        """Polling reports 'still working' rather than inventing a result."""
        safe_serial.OpenPortWorker = NeverFinishingWorker
        opener = self._opener()
        opener.open("COM_NOT_A_REAL_PORT", 115200)
        pump(0.2)
        self.assertIsNone(opener.poll())
        opener.abandon()

    def test_second_open_is_refused_while_busy(self):
        """Two opens cannot run at once."""
        safe_serial.OpenPortWorker = NeverFinishingWorker
        opener = self._opener()
        self.assertTrue(opener.open("COM_NOT_A_REAL_PORT", 115200))
        self.assertFalse(opener.open("COM_OTHER", 115200))
        opener.abandon()

    def test_real_worker_reports_failure_for_missing_port(self):
        """The genuine worker comes back with an error, not a hang."""
        opener = self._opener()
        self.assertTrue(opener.open("COM_NOT_A_REAL_PORT", 115200))

        done = pump(4.0, until=lambda: not opener.busy)
        self.assertTrue(done, "the open worker never reported back")

        result = opener.poll()
        self.assertIsNotNone(result)
        self.assertFalse(result.ok)
        self.assertFalse(result.timed_out)
        self.assertTrue(result.message)

    def test_abandon_is_a_noop_when_idle(self):
        """Abandoning nothing is harmless."""
        opener = self._opener()
        self.assertIsNone(opener.abandon())
        self.assertEqual(opener.graveyard, [])
        self.assertEqual(opener.thread_count, 0)

    def test_finished_worker_thread_is_released(self):
        """A normal failure must not leave its thread behind."""
        opener = self._opener()
        opener.open("COM_NOT_A_REAL_PORT", 115200)
        pump(4.0, until=lambda: not opener.busy)
        opener.poll()
        pump(0.5, until=lambda: opener.thread_count == 0)
        self.assertEqual(opener.thread_count, 0, "worker thread was leaked")


class TestHelpers(unittest.TestCase):
    """Small helpers used by the UI."""

    def test_detach_signals_tolerates_none(self):
        """Detaching from no port at all must not raise."""
        safe_serial.detach_signals(None, lambda _: None)

    def test_discard_port_tolerates_port_that_never_opened(self):
        """Closing a port that never opened must not raise."""
        safe_serial.discard_port(safe_serial.make_port())
        safe_serial.discard_port(None)

    def test_make_port_returns_safe_subclass(self):
        """The factory always yields a SafeSerialPort."""
        self.assertIsInstance(safe_serial.make_port(), safe_serial.SafeSerialPort)


if __name__ == "__main__":
    unittest.main()
