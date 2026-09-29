"""Switch to the VescUART driver and dump everything the board says about it.

Run with:  python tools/switch_and_dump.py [COM port]
"""

import os
import sys
import time

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

import PyQt6.QtCore  # noqa: E402
import PyQt6.QtWidgets  # noqa: E402

PORT = sys.argv[1] if len(sys.argv) > 1 else 'COM3'
DRIVER = 13

import base_ui  # noqa: E402
import main as app_main  # noqa: E402


def main():
    """Set the driver, reboot, then dump the raw replies."""
    app = PyQt6.QtWidgets.QApplication(sys.argv)
    app_main.app = app
    app_main.translator = PyQt6.QtCore.QTranslator()

    from main import MainUi  # noqa: PLC0415

    window = MainUi()
    window.check_configurator_update = lambda *a, **k: None
    chooser = window.serialchooser
    raw = []
    base_ui.CommunicationHandler.comms.rawReply.connect(raw.append)
    chooser.get_ports()
    names = [p.portName() for p in chooser._ports]  # pylint: disable=protected-access
    if PORT in names:
        chooser.comboBox_port.setCurrentIndex(names.index(PORT))

    def pump(seconds, until=None):
        """Run the event loop."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.01)
        app.processEvents()
        return bool(until is not None and until())

    def connected():
        """Serial link up?"""
        port = chooser.port
        return port is not None and port.isOpen()

    def send(cls, cmd):
        """Send a raw read command and return its own reply."""
        raw.clear()
        marker = '%s.%d.%s' % (cls, 0, cmd)
        chooser.send_command(cls, cmd, instance=0, typechar='?')
        pump(3.0, until=lambda: any(marker in item for item in raw))
        for item in raw:
            if marker in item:
                return item
        return None

    chooser.serial_connect()
    if not pump(12.0, until=connected):
        print('could not connect')
        return 1
    pump(5.0, until=lambda: chooser.main_id is not None)
    print('drvtype before: %r' % send('axis', 'drvtype'))

    try:
        print('setting drvtype = %d (VescUART) and rebooting' % DRIVER)
        window.send_value('axis', 'drvtype', DRIVER, instance=0)
        pump(1.0)
        window.send_value('axis', 'save', 1)
        pump(1.5)
        window.send_value('sys', 'reboot', 1)
        pump(3.0)
        chooser.disconnect_port()
        pump(3.0)
        chooser.serial_connect()
        if not pump(20.0, until=connected):
            print('no reconnect')
            return 1
        pump(8.0, until=lambda: chooser.main_id is not None)

        print('drvtype after reboot: %r' % send('axis', 'drvtype'))
        print('sys.lsactive: %r' % send('sys', 'lsactive'))
        print('--- vescuart.0.* raw replies ---')
        for cmd in ('vescstate', 'fwversion', 'voltage', 'encrate', 'pos',
                    'errorflags', 'crcerrors', 'uarterrors', 'txcount', 'rxcount',
                    'protostats'):
            print('  %-11s %r' % (cmd, send('vescuart', cmd)))
    finally:
        print('restoring drvtype')
        window.send_value('axis', 'drvtype', 7, instance=0)
        pump(1.0)
        window.send_value('axis', 'save', 1)
        pump(1.5)
        chooser.disconnect_port()
        chooser._opener.stop_all(500)  # pylint: disable=protected-access
        app.quit()

    return 0


if __name__ == '__main__':
    sys.exit(main())
