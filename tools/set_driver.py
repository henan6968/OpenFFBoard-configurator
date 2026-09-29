"""Leave the board on a given driver so the GUI tab is available.

Run with:  python tools/set_driver.py <drvtype> [COM port]
"""

import os
import sys
import time

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

import PyQt6.QtCore  # noqa: E402
import PyQt6.QtWidgets  # noqa: E402

DRIVER = int(sys.argv[1]) if len(sys.argv) > 1 else 13
PORT = sys.argv[2] if len(sys.argv) > 2 else 'COM3'

import base_ui  # noqa: E402
import main as app_main  # noqa: E402


def main():
    """Set axis.0.drvtype, save it and reboot."""
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

    def ask(cls, cmd):
        """Read one value and return its own raw reply."""
        raw.clear()
        marker = '%s.0.%s' % (cls, cmd)
        chooser.send_command(cls, cmd, instance=0, typechar='?')
        pump(4.0, until=lambda: any(marker in item for item in raw))
        for item in raw:
            if marker in item:
                return item
        return None

    chooser.serial_connect()
    if not pump(12.0, until=connected):
        print('could not connect')
        return 1
    pump(5.0, until=lambda: chooser.main_id is not None)

    def set_and_save(value):
        """Set axis.0.drvtype and wait until the flash write is acknowledged."""
        window.send_value('axis', 'drvtype', value, instance=0)
        pump(1.5)
        raw.clear()
        window.send_value('axis', 'save', 1)
        acknowledged = pump(6.0, until=lambda: any(
            'axis.0.save' in item and 'OK' in item for item in raw))
        print('save acknowledged: %s' % acknowledged)
        if not acknowledged:
            print('  save replies: %r' % (raw[-4:],))
        pump(1.0)

    print('drvtype before: %r' % ask('axis', 'drvtype'))
    set_and_save(DRIVER)
    print('drvtype after set (before reboot): %r' % ask('axis', 'drvtype'))
    window.send_value('sys', 'reboot', 1)
    pump(3.0)
    chooser.disconnect_port()
    pump(3.0)
    chooser.serial_connect()
    if not pump(20.0, until=connected):
        print('no reconnect')
        return 1
    pump(8.0, until=lambda: chooser.main_id is not None)

    print('drvtype now: %r' % ask('axis', 'drvtype'))
    print('lsactive   : %r' % ask('sys', 'lsactive'))
    print('board left on driver %d' % DRIVER)

    chooser.disconnect_port()
    chooser._opener.stop_all(500)  # pylint: disable=protected-access
    app.quit()
    return 0


if __name__ == '__main__':
    sys.exit(main())
