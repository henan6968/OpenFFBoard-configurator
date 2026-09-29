"""Connect and dump exactly what the board reports, with no side effects.

Run with:  python tools/dump_board_classes.py [COM port]
"""

import os
import sys
import time

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

import PyQt6.QtCore  # noqa: E402
import PyQt6.QtWidgets  # noqa: E402

PORT = sys.argv[1] if len(sys.argv) > 1 else 'COM3'

import base_ui  # noqa: E402
import main as app_main  # noqa: E402


def main():
    """Connect, ask for the class list, print everything."""
    app = PyQt6.QtWidgets.QApplication(sys.argv)
    app_main.app = app
    app_main.translator = PyQt6.QtCore.QTranslator()

    from main import MainUi  # noqa: PLC0415

    window = MainUi()
    window.check_configurator_update = lambda *a, **k: None
    chooser = window.serialchooser
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

    # Raw traffic, independent of whether the Serial tab was ever shown.
    base_ui.CommunicationHandler.comms.rawReply.connect(
        lambda txt: print('RAW %r' % (txt,), flush=True)
    )

    chooser.serial_connect()
    if not pump(10.0, until=connected):
        print('could not connect to %s' % PORT)
        return 1

    print('connected on %s' % PORT)
    pump(6.0, until=lambda: chooser.main_id is not None)
    pump(4.0, until=lambda: len(chooser._classes) > 0)  # pylint: disable=protected-access

    print('main id: %r' % chooser.main_id)
    print('classes creatable by the board:')
    for class_id, name, creatable in chooser._classes:  # pylint: disable=protected-access
        print('    0x%03X  %-22s creatable=%s' % (class_id, name, creatable))
    print('classes currently active (id -> name):')
    for class_id, (index, name) in sorted(
            chooser._class_ids.items()):  # pylint: disable=protected-access
        print('    0x%03X  %s' % (class_id, name))

    print('tabs built:')
    for index in range(window.tabWidget_main.count()):
        print('    %r' % window.tabWidget_main.tabText(index))

    print('--- lsmain reply, verbatim ---')
    chooser.send_command('sys', 'lsmain', typechar='?')
    pump(4.0)
    print('--- lsactive reply, verbatim ---')
    chooser.send_command('sys', 'lsactive', typechar='?')
    pump(4.0)
    print('--- main classes, verbatim ---')
    chooser.send_command('sys', 'lsmain', typechar='!')
    pump(4.0)

    chooser.disconnect_port()
    app.quit()
    return 0


if __name__ == '__main__':
    sys.exit(main())
