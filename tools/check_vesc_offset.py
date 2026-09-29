"""Check the Erase-offset and Apply flow on the VescUART tab.

Erase offset writes 0, Apply writes the spin box value. Both go over UART.

Run with:  python tools/check_vesc_offset.py [COM port]
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
HOME = 7

import main as app_main  # noqa: E402


def main():
    """Switch to VescUART, exercise the offset controls, restore the driver."""
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

    def ask(cls, cmd):
        """Read one integer value."""
        got = {}
        chooser.get_value_async(cls, cmd, lambda v: got.setdefault('v', v), conversion=int)
        pump(3.0, until=lambda: 'v' in got)
        return got.get('v')

    def find_tab():
        """The live VescUART tab."""
        for i in range(window.tabWidget_main.count()):
            if 'uart' in window.tabWidget_main.tabText(i).lower():
                return window.tabWidget_main.widget(i)
        return None

    chooser.serial_connect()
    if not pump(12.0, until=connected):
        print('could not connect')
        return 1
    pump(5.0, until=lambda: chooser.main_id is not None)

    try:
        window.send_value('axis', 'drvtype', DRIVER, instance=0)
        pump(1.0)
        window.send_value('axis', 'save', 1)
        pump(1.5)
        window.send_value('sys', 'reboot', 1)
        pump(2.5)
        chooser.disconnect_port()
        pump(2.5)
        chooser.serial_connect()
        if not pump(20.0, until=connected):
            print('no reconnect')
            return 1
        pump(6.0, until=lambda: chooser.main_id is not None)
        if not pump(25.0, until=lambda: find_tab() is not None):
            print('VescUART tab never appeared')
            return 1
        pump(6.0, until=lambda: ask('vescuart', 'vescstate') == 4)

        tab = find_tab()
        print('state=%r' % tab.label_state.text())
        print('offset before      : board=%r spinbox=%.4f'
              % (ask('vescuart', 'offset'),
                 tab.doubleSpinBox_encoderOffset.value()))

        print('--- erase offset ---')
        tab.pushButton_eraseOffset.click()
        pump(2.5)
        tab = find_tab()
        board_after_erase = ask('vescuart', 'offset')
        print('offset after erase : board=%r spinbox=%.4f'
              % (board_after_erase, tab.doubleSpinBox_encoderOffset.value()))

        print('--- apply a deliberate offset of 0.25 turns ---')
        tab.doubleSpinBox_encoderOffset.setValue(0.25)
        tab.pushButton_apply.click()
        pump(2.5)
        board_after_apply = ask('vescuart', 'offset')
        print('offset after apply : board=%r (expected 2500)'
              % board_after_apply)

        print('--- erase again, leave the board centred ---')
        tab = find_tab()
        tab.pushButton_eraseOffset.click()
        pump(2.5)
        print('offset final       : board=%r' % ask('vescuart', 'offset'))

        ok = (board_after_erase == 0 and board_after_apply == 2500)
        print('RESULT: %s' % ('PASS' if ok else 'FAIL'))
        return 0 if ok else 1
    finally:
        print('restoring drvtype %d' % HOME)
        window.send_value('axis', 'drvtype', HOME, instance=0)
        pump(1.0)
        window.send_value('axis', 'save', 1)
        pump(1.5)
        chooser.disconnect_port()
        chooser._opener.stop_all(500)  # pylint: disable=protected-access
        app.quit()


if __name__ == '__main__':
    sys.exit(main())
