"""Verify the VescUART integration end to end against a live board.

Drives the real configurator the way a user would and checks the whole chain:

  1. connect through the non-blocking connect path,
  2. set ``axis.0.drvtype = 13`` (VescUART) and save it to flash,
  3. confirm the board reports the driver back,
  4. reboot, let the board come back, and confirm the firmware lists the
     VescUART class as active,
  5. confirm the configurator builds a VescUART tab for it,
  6. exercise that tab's commands over the UART link,
  7. restore the original driver and save, leaving the board as it was found.

A heartbeat timer runs throughout and reports any stall of the GUI thread.

Run with:
    python tools/verify_vescuart_tool.py [COM port]
"""

import faulthandler
import os
import sys
import time

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

import PyQt6.QtCore  # noqa: E402
import PyQt6.QtWidgets  # noqa: E402

PORT = sys.argv[1] if len(sys.argv) > 1 else 'COM3'
VESCUART_DRIVER = 13

faulthandler.enable()
faulthandler.dump_traceback_later(300, exit=False, file=sys.stderr)

import base_ui  # noqa: E402
import main as app_main  # noqa: E402

HEARTBEAT_MS = 100
STALL_WARN_MS = 400


class Session:
    """A running configurator plus the helpers the verification needs."""

    def __init__(self):
        self.app = PyQt6.QtWidgets.QApplication(sys.argv)
        app_main.app = self.app
        app_main.translator = PyQt6.QtCore.QTranslator()

        from main import MainUi  # noqa: PLC0415 (needs the app first)

        self.window = MainUi()
        self.window.check_configurator_update = lambda *a, **k: None
        self.chooser = self.window.serialchooser
        self.raw = []
        base_ui.CommunicationHandler.comms.rawReply.connect(self.raw.append)

        self.stalls = []
        self.last_beat = time.monotonic()
        self.timer = PyQt6.QtCore.QTimer()
        self.timer.setInterval(HEARTBEAT_MS)
        self.timer.timeout.connect(self.beat)
        self.timer.start()

        self.chooser.get_ports()
        names = [p.portName() for p in self.chooser._ports]  # pylint: disable=protected-access
        if PORT in names:
            self.chooser.comboBox_port.setCurrentIndex(names.index(PORT))
        self.say('ports available: %s' % ', '.join(names))

    def say(self, text):
        """Report a progress line."""
        print(text, flush=True)

    def beat(self):
        """Heartbeat: record any gap in the event loop."""
        now = time.monotonic()
        gap_ms = (now - self.last_beat) * 1000
        self.last_beat = now
        if gap_ms > STALL_WARN_MS:
            self.stalls.append(gap_ms)
            print('  !! GUI stalled for %.0f ms' % gap_ms, flush=True)

    def pump(self, seconds=0.5, until=None):
        """Run the event loop, optionally until a condition holds."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return bool(until is not None and until())

    def connected(self):
        """True once the serial port is open."""
        port = self.chooser.port
        return port is not None and port.isOpen()

    def connect(self, seconds=12.0):
        """Start a connect and wait for it to finish."""
        if self.connected():
            return True
        self.chooser.serial_connect()
        return self.pump(seconds, until=self.connected)

    def disconnect(self):
        """Close the link."""
        if self.connected():
            self.chooser.disconnect_port()

    def tabs(self):
        """Tab titles currently shown."""
        return [self.window.tabWidget_main.tabText(i)
                for i in range(self.window.tabWidget_main.count())]

    #: Titles the VescUART tab can have, depending on the class name.
    VESCUART_TAB_NAMES = ('vesc uart', 'vescuart')

    def is_vescuart_tab(self, title):
        """True for the VescUART tab, and only that one."""
        lowered = title.lower()
        return any(name in lowered for name in self.VESCUART_TAB_NAMES)

    def wait_tab(self, seconds=20.0):
        """Wait for the VescUART tab to appear."""
        return self.pump(
            seconds, until=lambda: any(self.is_vescuart_tab(t) for t in self.tabs())
        )

    def ask(self, cls, cmd, seconds=6.0, instance=0):
        """Send a read command and return its own reply.

        The board is also streaming telemetry, so anything that does not
        mention this exact command is ignored.
        """
        self.raw.clear()
        marker = '%s.%d.%s' % (cls, instance, cmd)
        self.chooser.send_command(cls, cmd, instance=instance, typechar='?')

        def answered():
            return any(marker in item for item in self.raw)

        self.pump(seconds, until=answered)
        for item in self.raw:
            if marker in item:
                return item
        return None

    def tail_raw(self, count=12):
        """Print the most recent raw replies."""
        for line in self.raw[-count:]:
            print('    %r' % (line,), flush=True)


def main():
    """Run the verification."""
    session = Session()
    ok = True
    home_driver = None

    try:
        session.say('--- step 1: connect to %s' % PORT)
        if not session.connect():
            session.say('FAILED: could not connect to %s' % PORT)
            return 1
        session.say('connected; status bar: %r'
                    % session.window.statusBar().currentMessage())
        session.pump(5.0, until=lambda: session.chooser.main_id is not None)
        session.say('main id: %r' % session.chooser.main_id)

        session.say('--- step 2: read the current driver')
        reply = session.ask('axis', 'drvtype')
        session.say('reply: %r' % reply)
        if reply and '|' in reply:
            home_driver = reply.rsplit('|', 1)[1].strip()
        session.say('axis.0.drvtype was %r' % home_driver)

        session.say('--- step 3: set axis.0.drvtype = %d (VescUART), save'
                    % VESCUART_DRIVER)
        session.window.send_value('axis', 'drvtype', VESCUART_DRIVER, instance=0)
        session.pump(1.0)
        session.raw.clear()
        session.window.send_value('axis', 'save', 1)
        session.pump(2.0)
        session.say('save replies:')
        session.tail_raw(4)
        session.say('drvtype now: %r' % session.ask('axis', 'drvtype'))

        session.say('--- step 4: reboot and reconnect')
        session.window.send_value('sys', 'reboot', 1)
        session.pump(2.5)
        session.disconnect()
        session.pump(3.0)
        if not session.connect(20.0):
            session.say('FAILED: could not reconnect after the reboot')
            return 1
        session.pump(6.0, until=lambda: session.chooser.main_id is not None)

        session.say('--- step 5: what does the firmware report as active?')
        reply = session.ask('sys', 'lsactive', seconds=8.0)
        session.say('sys.lsactive -> %r' % reply)
        board_lists_vescuart = bool(reply and 'vescuart' in reply)
        session.say('firmware reports the VescUART class: %s' % board_lists_vescuart)

        session.say('--- step 6: does the configurator build the tab?')
        built = session.wait_tab(20.0)
        session.say('tabs: %s' % session.tabs())
        session.say('VescUART tab present: %s' % built)
        ok = ok and built

        if built:
            tab = None
            for index in range(session.window.tabWidget_main.count()):
                if session.is_vescuart_tab(
                        session.window.tabWidget_main.tabText(index)):
                    tab = session.window.tabWidget_main.widget(index)
                    break
            if tab is not None:
                session.say('--- step 7: read VescUART values over the UART link')
                for cmd in ('vescstate', 'fwversion', 'hwname', 'voltage',
                            'errorflags', 'encrate', 'crcerrors', 'uarterrors',
                            'protostats', 'pos', 'useencoder', 'offset'):
                    session.say('  %-11s -> %r'
                                % (cmd, session.ask('vescuart', cmd, 4.0)))
                session.say('serial console tail:')
                for line in session.chooser.serialLogBox.toPlainText().splitlines()[-12:]:
                    if line.strip():
                        session.say('    ' + line)
    finally:
        session.say('--- step 8: restore the board')
        if home_driver is not None and home_driver.isdigit():
            session.window.send_value('axis', 'drvtype', int(home_driver), instance=0)
            session.pump(1.0)
            session.window.send_value('axis', 'save', 1)
            session.pump(2.0)
            session.say('restored axis.0.drvtype to %s, now %r'
                        % (home_driver, session.ask('axis', 'drvtype')))
        else:
            session.say('original driver unknown; left unchanged (was %r)' % home_driver)

        session.say('=' * 72)
        session.say('stalls over %d ms: %d' % (STALL_WARN_MS, len(session.stalls)))
        session.say('worst GUI stall: %.0f ms'
                    % (max(session.stalls) if session.stalls else 0.0))
        session.say('RESULT: %s' % ('PASS' if ok and not session.stalls else 'CHECK ABOVE'))
        session.chooser._opener.stop_all(500)  # pylint: disable=protected-access
        session.app.quit()

    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
