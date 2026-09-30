"""Switch the F407 axis driver and report whether the VescUART class appears.

The driver was seen to come up when switched at runtime but not always after a
reboot with drvtype already set, so this walks 7 -> 13 -> check and prints what
the board reports at each step.

Usage:  python tools/check_driver_switch.py [COM port] [target drvtype]
"""

import os
import sys
import time

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

import PyQt6.QtCore  # noqa: E402
import PyQt6.QtWidgets  # noqa: E402

ARGS = [a for a in sys.argv[1:] if not a.startswith('-')]
PORT = ARGS[0] if ARGS else 'COM3'
TARGET = int(ARGS[1]) if len(ARGS) > 1 else 13

import base_ui  # noqa: E402
import main as app_main  # noqa: E402

APP = PyQt6.QtWidgets.QApplication([])
app_main.app = APP
app_main.translator = PyQt6.QtCore.QTranslator()


class Session:
    """Serial session helpers."""

    def __init__(self):
        from main import MainUi  # noqa: PLC0415

        self.window = MainUi()
        self.window.check_configurator_update = lambda *a, **k: None
        self.chooser = self.window.serialchooser
        self.raw = []
        base_ui.CommunicationHandler.comms.rawReply.connect(self.raw.append)
        self.chooser.get_ports()
        names = [p.portName() for p in self.chooser._ports]  # pylint: disable=protected-access
        if PORT in names:
            self.chooser.comboBox_port.setCurrentIndex(names.index(PORT))

    def pump(self, seconds, until=None):
        """Run the event loop."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            APP.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.01)
        return bool(until is not None and until())

    def connected(self):
        """Serial link up?"""
        port = self.chooser.port
        return port is not None and port.isOpen()

    def connect(self, seconds=15.0):
        """Connect if needed."""
        if self.connected():
            return True
        self.chooser.serial_connect()
        ok = self.pump(seconds, self.connected)
        if ok:
            self.pump(10.0, until=lambda: self.chooser.main_id is not None)
        return ok

    def ask(self, cls, cmd, seconds=5.0):
        """Read one value and return its raw reply."""
        marker = '%s.0.%s' % (cls, cmd)
        self.raw.clear()
        self.chooser.send_command(cls, cmd, instance=0, typechar='?')
        self.pump(seconds, until=lambda: any(marker in item for item in self.raw))
        for item in self.raw:
            if marker in item:
                return item
        return None

    def classes(self):
        """The class list the board reports as active."""
        reply = self.ask('sys', 'lsactive', 8.0)
        return reply or ''


def main():
    """Walk the driver states."""
    session = Session()
    if not session.connect():
        print('could not connect to %s' % PORT)
        return 1

    print('connected')
    print('  drvtype   :', session.ask('axis', 'drvtype'))
    print('  lsactive  :', session.classes().replace('\n', ' | ')[:200])

    print()
    print('--- switching to drvtype %d at runtime ---' % TARGET)
    session.window.send_value('axis', 'drvtype', TARGET, instance=0)
    session.pump(6.0)
    session.chooser.send_command('sys', 'lsactive', instance=0, typechar='?')
    session.pump(5.0)
    print('  drvtype   :', session.ask('axis', 'drvtype'))
    print('  lsactive  :', session.classes().replace('\n', ' | ')[:200])
    for cmd in ('vescstate', 'encrate', 'voltage', 'pos'):
        print('  %-10s: %s' % (cmd, session.ask('vescuart', cmd)))

    print()
    print('--- rebooting and re-checking ---')
    session.window.send_value('sys', 'reboot', 1)
    session.pump(3.0)
    session.chooser.disconnect_port()
    session.pump(3.0)
    if not session.connect(20.0):
        print('  no reconnect')
        return 2
    print('  drvtype   :', session.ask('axis', 'drvtype'))
    print('  lsactive  :', session.classes().replace('\n', ' | ')[:200])

    session.chooser.disconnect_port()
    session.chooser._opener.stop_all(500)  # pylint: disable=protected-access
    APP.quit()
    return 0


if __name__ == '__main__':
    sys.exit(main())
