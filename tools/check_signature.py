"""Read sys.0.signature the way the configurator does and print it verbatim."""

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
    """Connect and ask for the signature."""
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

    chooser.serial_connect()
    if not pump(12.0, until=lambda: chooser.port is not None
                and chooser.port.isOpen()):
        print('could not connect')
        return 1
    pump(4.0)
    print('connected')

    for typechar in ('!', '?', ''):
        raw.clear()
        chooser.send_command('sys', 'signature', typechar=typechar)
        pump(2.5, until=lambda: bool(raw))
        print('sys.0.signature%s -> %r' % (typechar, raw[:2]))

    raw.clear()
    chooser.send_command('sys', 'uid', typechar='?')
    pump(2.5, until=lambda: bool(raw))
    print('sys.0.uid? -> %r' % (raw[:1],))

    chooser.disconnect_port()
    chooser._opener.stop_all(500)  # pylint: disable=protected-access
    app.quit()
    return 0


if __name__ == '__main__':
    sys.exit(main())
