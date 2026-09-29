"""Open the real VescUART tab against the board and dump what it displays.

Run with:  python tools/check_vescuart_tab.py [COM port]
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

import main as app_main  # noqa: E402


def main():
    """Connect, select the VescUART tab, and print its live readouts."""
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

    original = None
    chooser.serial_connect()
    if not pump(12.0, until=connected):
        print('could not connect')
        return 1
    pump(5.0, until=lambda: chooser.main_id is not None)

    def ask(cls, cmd):
        """Read one integer value."""
        got = {}
        chooser.get_value_async(cls, cmd, lambda v: got.setdefault('v', v), conversion=int)
        pump(3.0, until=lambda: 'v' in got)
        return got.get('v')

    original = ask('axis', 'drvtype')
    print('drvtype before: %r' % original)

    try:
        if original != DRIVER:
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
        if not pump(25.0, until=lambda: any(
                'uart' in window.tabWidget_main.tabText(i).lower()
                for i in range(window.tabWidget_main.count()))):
            print('VescUART tab never appeared')
            print('  drvtype now  : %r' % ask('axis', 'drvtype'))
            print('  tabs         : %r'
                  % [window.tabWidget_main.tabText(i)
                     for i in range(window.tabWidget_main.count())])
            print('  main id      : %r' % chooser.main_id)
            print('  classes      : %r' % chooser._classes)  # pylint: disable=protected-access
            print('  console tail :')
            for line in chooser.serialLogBox.toPlainText().splitlines()[-8:]:
                print('      ' + line)
            return 1

        def find_tab():
            """The live VescUART tab. update_tabs() rebuilds it, so never cache."""
            for i in range(window.tabWidget_main.count()):
                if 'uart' in window.tabWidget_main.tabText(i).lower():
                    return window.tabWidget_main.widget(i)
            return None

        for i in range(window.tabWidget_main.count()):
            if 'uart' in window.tabWidget_main.tabText(i).lower():
                window.tabWidget_main.setCurrentIndex(i)
        tab = find_tab()
        print('tab: %r' % window.tabWidget_main.tabText(
            window.tabWidget_main.currentIndex()))
        print('  group title   : %r' % tab.groupBox.title())
        print('  action button : %r' % tab.pushButton_manualRead.text())
        leftovers = []
        for name in tab.CAN_ONLY_WIDGETS:
            widget = getattr(tab, name, None)
            if widget is None:
                continue
            try:
                if widget.parent() is not None:
                    leftovers.append(name)
            except RuntimeError:
                pass  # C++ object already deleted, which is what we want
        print('  can rows left : %s' % (leftovers or 'none'))

        print('  --- live readouts, 4 s of polling ---')
        for _ in range(4):
            pump(1.0)
            tab = find_tab()
            try:
                print('    state=%-14s voltage=%-8s rate=%-6s pos=%-8s '
                      'offset=%-10s err=%s'
                      % (tab.label_state.text(), tab.label_voltage.text(),
                         tab.label_encoder_rate.text(), tab.label_pos.text(),
                         tab.doubleSpinBox_encoderOffset.value(),
                         tab.label_errors.text()))
            except RuntimeError:
                print('    (tab was rebuilt)')
            print('    raw pos from board: %r' % ask('vescuart', 'pos'))

        print('  --- help text, first lines ---')
        for line in [line for line in tab.textBrowser.toPlainText().splitlines()
                     if line.strip()][:12]:
            print('    ' + line)

        print('  --- force position read button ---')
        tab = find_tab()
        before = tab.label_pos.text()
        tab.pushButton_manualRead.click()
        pump(1.5)
        print('    pos %s -> %s' % (before, tab.label_pos.text()))
    finally:
        if original is not None and original != DRIVER:
            print('restoring drvtype %r' % original)
            window.send_value('axis', 'drvtype', original, instance=0)
            pump(1.0)
            window.send_value('axis', 'save', 1)
            pump(1.5)
        chooser.disconnect_port()
        chooser._opener.stop_all(500)  # pylint: disable=protected-access
        app.quit()

    return 0


if __name__ == '__main__':
    sys.exit(main())
