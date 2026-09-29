"""Integration tests for the configurator's serial tab.

These cover the failure that made the window freeze: the serial tab used to
call ``QSerialPort.open()`` straight from the GUI thread, and on Windows that
call blocks in a kernel IOCTL with no timeout whenever the USB CDC device is
wedged. The tab now hands the open to a worker and collects the result from a
timer, so the tests below check three things:

  * connecting never blocks the calling thread,
  * a device that never answers is reported and does not leave a half-open
    port behind,
  * a successful open is wired into the communication module and the UI.

Run with:  python -m unittest tests.test_serial_ui_connect -v
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import PyQt6.QtCore  # noqa: E402
import PyQt6.QtWidgets  # noqa: E402

import base_ui  # noqa: E402
import safe_serial  # noqa: E402
import serial_comms  # noqa: E402
import serial_ui  # noqa: E402


APP = None


def setUpModule():  # noqa: N802
    """A QApplication is needed for widgets and serial ports."""
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


def open_port():
    """Build a SafeSerialPort that reports itself as open.

    The UI decides everything from ``isOpen()``, so a connected state cannot
    be faked with a real (closed) port object.
    """
    port = safe_serial.make_port()
    port.isOpen = lambda: True  # noqa: E731 (deliberate stub)
    port.write = lambda data: len(data)  # noqa: E731 (deliberate stub)
    return port


class FakeStatusBar:
    """Records what the status bar was asked to show."""

    def __init__(self):
        self.events = []

    def set_connection_status(self, state, text):
        """Remember one status update."""
        self.events.append((state, text))

    def states(self):
        """The sequence of states reported."""
        return [state for state, _ in self.events]


class FakeMain:
    """The slice of MainUi that SerialChooser touches."""

    def __init__(self):
        self.messages = []
        self.wrapper_status_bar = FakeStatusBar()
        self.update_calls = 0
        self._connect_in_progress = False

    def log(self, message):
        """Collect a log line."""
        self.messages.append(message)

    def update_tabs(self):
        """Count tab refreshes."""
        self.update_calls += 1

    def reset_port(self, immediate=False):  # noqa: ARG002
        """Pretend to tear the link down."""
        self.messages.append("reset_port")


class ScriptedOpener:
    """A stand-in for safe_serial.PortOpener with a scripted answer."""

    def __init__(self, results):
        self._results = list(results)
        self.graveyard = []
        self.busy = False
        self.timed_out = False
        self.opened = []

    def reset(self):
        """Nothing to reset in this stub."""
        self.timed_out = False

    def open(self, port_name, baudrate):
        """Accept the request and remember it."""
        self.opened.append((port_name, baudrate))
        self.timed_out = False
        self.busy = False
        return True

    def poll(self):
        """Hand out the next scripted result."""
        if not self._results:
            return None
        return self._results.pop(0)

    def abandon(self, reason="device did not respond"):
        """Report a timeout result."""
        self.busy = False
        result = safe_serial.OpenResult('timeout', None, reason)
        self.graveyard.append(result)
        return result

    def stop_thread(self, timeout_ms=0):  # noqa: ARG002
        """Nothing to stop in this stub."""
        return True

    def stop_all(self, timeout_ms=0):  # noqa: ARG002
        """Nothing to stop in this stub."""
        return True


class FakePortInfo:
    """One entry of the port list."""

    def __init__(self, name):
        self._name = name

    def portName(self):  # noqa: N802 (Qt name)
        """The device path."""
        return self._name

    def description(self):
        """The device description."""
        return "Fake FFBoard"

    def vendorIdentifier(self):  # noqa: N802 (Qt name)
        """The official FFBoard VID."""
        return 0x1209

    def productIdentifier(self):  # noqa: N802 (Qt name)
        """The official FFBoard PID."""
        return 0xFFB0


class SerialChooserHarness:
    """Build a SerialChooser wired to a fake main window."""

    def __init__(self, scripted_results=None, port_name="COM_TEST"):
        self.main = FakeMain()
        self.chooser = serial_ui.SerialChooser.__new__(serial_ui.SerialChooser)
        base_ui.WidgetUI.__init__(self.chooser, None, "serialchooser.ui")
        base_ui.CommunicationHandler.__init__(self.chooser)

        self.chooser.main = self.main
        self.chooser.main_id = None
        self.chooser._classes = []          # pylint: disable=protected-access
        self.chooser._class_ids = {}        # pylint: disable=protected-access
        self.chooser._port = FakePortInfo(port_name)
        self.chooser._ports = [self.chooser._port]  # pylint: disable=protected-access
        self.chooser._logging = False       # pylint: disable=protected-access
        self.chooser._closing = False       # pylint: disable=protected-access
        self.chooser._pending_connect = False  # pylint: disable=protected-access
        self.chooser._serial = None         # pylint: disable=protected-access
        # serial_connect() re-reads the combo box, so it has to contain the fake
        # port, exactly like get_ports() would have left it.
        self.chooser.comboBox_port.addItem(port_name)
        self.chooser.comboBox_port.setCurrentIndex(0)
        self.chooser.select_port(0)
        self.chooser._opener = ScriptedOpener(scripted_results or [])
        self.chooser._connect_timer = PyQt6.QtCore.QTimer()
        self.chooser._connect_timer.setInterval(serial_ui.CONNECT_POLL_MS)
        self.chooser._connect_timer.timeout.connect(self.chooser._poll_connect)

        comms = serial_comms.SerialComms(self.main, None)
        base_ui.CommunicationHandler.comms = comms
        self.comms = comms
        self.chooser.update()


class TestConnectIsNonBlocking(unittest.TestCase):
    """The connect path must return to the caller immediately."""

    def test_serial_connect_returns_at_once(self):
        """Starting a connect does not wait for the port to open."""
        harness = SerialChooserHarness()

        started = time.monotonic()
        result = harness.chooser.serial_connect()
        elapsed = time.monotonic() - started

        self.assertFalse(result, "an attempt in flight is not 'connected'")
        self.assertLess(elapsed, 0.05, "serial_connect blocked the caller")
        self.assertEqual(harness.chooser._opener.opened,  # pylint: disable=protected-access
                         [("COM_TEST", serial_ui.BAUDRATE)])
        self.assertTrue(harness.chooser._connect_timer.isActive())  # pylint: disable=protected-access

    def test_second_connect_while_pending_is_ignored(self):
        """A connect already in flight is not restarted."""
        harness = SerialChooserHarness()
        harness.chooser.serial_connect()
        harness.chooser.serial_connect()
        self.assertEqual(len(harness.chooser._opener.opened), 1)  # pylint: disable=protected-access

    def test_status_bar_reports_connecting(self):
        """The user sees that something is happening."""
        harness = SerialChooserHarness()
        harness.chooser.serial_connect()
        self.assertIn('connecting', harness.main.wrapper_status_bar.states())


class TestSuccessfulConnect(unittest.TestCase):
    """A good open is wired into the rest of the application."""

    def test_port_is_installed_and_comms_follows(self):
        """The new port becomes the live one and the comms module uses it."""
        port = open_port()
        harness = SerialChooserHarness([safe_serial.OpenResult('opened', port, '')])

        harness.chooser.serial_connect()
        pump(1.0, until=lambda: harness.chooser.port is not None)

        self.assertIs(harness.chooser.port, port)
        self.assertIs(harness.comms.serial, port,
                      "the communication module was not rewired to the new port")
        self.assertIn('open', harness.main.wrapper_status_bar.states())

    def test_ui_switches_to_connected(self):
        """The connect button flips to Disconnect."""
        port = open_port()
        harness = SerialChooserHarness([safe_serial.OpenResult('opened', port, '')])

        harness.chooser.serial_connect()
        pump(1.0, until=lambda: harness.chooser.port is not None)

        self.assertEqual(harness.chooser.pushButton_connect.text(), "Disconnect")
        self.assertFalse(harness.chooser.comboBox_port.isEnabled())


class TestFailedConnect(unittest.TestCase):
    """A refused open is explained and leaves nothing behind."""

    def test_refused_port_reports_reason(self):
        """The Qt error string reaches the user."""
        harness = SerialChooserHarness(
            [safe_serial.OpenResult('failed', None, 'Access is denied.')]
        )

        harness.chooser.serial_connect()
        pump(1.0, until=lambda: not harness.chooser._pending_connect)  # pylint: disable=protected-access

        self.assertTrue(any('Access is denied.' in m for m in harness.main.messages))
        self.assertIsNone(harness.chooser.port)
        self.assertIn('failed', harness.main.wrapper_status_bar.states())
        self.assertEqual(harness.chooser.pushButton_connect.text(), "Connect")


class TestStuckDevice(unittest.TestCase):
    """A device that never answers must be reported, not waited on."""

    def test_timeout_is_reported_without_blocking(self):
        """The pending state clears and the port is written off."""
        harness = SerialChooserHarness([])
        harness.chooser._opener.busy = True      # pylint: disable=protected-access
        harness.chooser._opener.timed_out = True  # pylint: disable=protected-access
        harness.chooser._pending_connect = True   # pylint: disable=protected-access

        started = time.monotonic()
        harness.chooser._poll_connect()  # pylint: disable=protected-access
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.05)
        self.assertFalse(harness.chooser._pending_connect)  # pylint: disable=protected-access
        self.assertIsNone(harness.chooser.port)
        self.assertIn('noresponse', harness.main.wrapper_status_bar.states())
        self.assertTrue(any('not responding' in m for m in harness.main.messages))
        self.assertEqual(len(harness.chooser._opener.graveyard), 1)  # pylint: disable=protected-access

    def test_button_is_re_enabled_after_a_timeout(self):
        """The user can try again without restarting the app."""
        harness = SerialChooserHarness([])
        harness.chooser._opener.timed_out = True  # pylint: disable=protected-access
        harness.chooser._pending_connect = True   # pylint: disable=protected-access
        harness.chooser.pushButton_connect.setEnabled(False)

        harness.chooser._poll_connect()  # pylint: disable=protected-access

        self.assertTrue(harness.chooser.pushButton_connect.isEnabled())


if __name__ == "__main__":
    unittest.main()
