"""Check the VescUART link: position movement, rate, and the torque watchdog.

Sequence:
  1. connect and switch the axis to the VescUART driver,
  2. sample ``pos`` a few times to see whether the angle is live,
  3. apply a small torque for a moment and confirm the VESC accepts it,
  4. stop the stream and confirm the F407 drops torque to zero.

Nothing large is commanded: the torque step is deliberately small and short.
Run with:  python tools/verify_vesc_link.py [COM port]
"""

import os
import statistics
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


class Link:
    """A running configurator plus raw-reply helpers."""

    def __init__(self):
        self.app = PyQt6.QtWidgets.QApplication(sys.argv)
        app_main.app = self.app
        app_main.translator = PyQt6.QtCore.QTranslator()

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

    def say(self, text):
        """Report a line."""
        print(text, flush=True)

    def pump(self, seconds=0.5, until=None):
        """Run the event loop."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return bool(until is not None and until())

    def connected(self):
        """Serial link up?"""
        port = self.chooser.port
        return port is not None and port.isOpen()

    def ask(self, cls, cmd, seconds=4.0, instance=0):
        """Read one value and return it as an int, or None."""
        self.raw.clear()
        marker = '%s.%d.%s' % (cls, instance, cmd)
        self.chooser.send_command(cls, cmd, instance=instance, typechar='?')
        self.pump(seconds, until=lambda: any(marker in item for item in self.raw))
        for item in self.raw:
            if marker in item and '|' in item:
                try:
                    return int(item.rsplit('|', 1)[1])
                except ValueError:
                    return None
        return None


def main():
    """Run the link checks."""
    link = Link()

    if not link.connected():
        link.chooser.serial_connect()
        if not link.pump(12.0, until=link.connected):
            link.say('FAILED: could not connect to %s' % PORT)
            return 1
    link.say('connected')
    link.pump(5.0, until=lambda: link.chooser.main_id is not None)

    original = link.ask('axis', 'drvtype')
    link.say('axis.0.drvtype before: %r' % original)
    link.window.send_value('axis', 'drvtype', DRIVER, instance=0)
    link.pump(1.0)
    link.window.send_value('axis', 'save', 1)
    link.pump(1.5)
    link.say('axis.0.drvtype now: %r' % link.ask('axis', 'drvtype'))

    link.say('reconnecting to bring the driver up cleanly')
    link.window.send_value('sys', 'reboot', 1)
    link.pump(2.5)
    link.chooser.disconnect_port()
    link.pump(2.5)
    link.chooser.serial_connect()
    if not link.pump(20.0, until=link.connected):
        link.say('FAILED: no reconnect after reboot')
        return 1
    link.pump(6.0, until=lambda: link.chooser.main_id is not None)

    try:
        link.say('=== link state ===')
        for cmd in ('vescstate', 'fwversion', 'encrate', 'crcerrors', 'uarterrors',
                    'errorflags', 'voltage'):
            link.say('  %-11s = %r' % (cmd, link.ask('vescuart', cmd)))

        link.say('=== position samples (any real value means the angle is live) ===')
        samples = []
        for _ in range(5):
            value = link.ask('vescuart', 'pos', 3.0)
            samples.append(value)
            link.say('  pos = %r' % value)
            link.pump(0.5)
        real = [value for value in samples if value is not None]
        link.say('  %d/5 replies, spread = %s'
                 % (len(real), (max(real) - min(real)) if real else 'n/a'))

        link.say('=== torque path (small, short) ===')
        link.say('  useencoder = %r' % link.ask('vescuart', 'useencoder'))
        torque = link.ask('vescuart', 'torque')
        link.say('  torque before = %r' % torque)

        # torque is read-only over the command interface: the axis applies it
        # through the driver. axis.0.force is the axis-level torque input,
        # scaled to +-0x7fff, so 160 is about 0.5 %% of full scale.
        link.say('  applying axis.0.force = 160 for 0.6 s')
        for _ in range(12):
            link.window.send_value('axis', 'force', 160, instance=0)
            link.pump(0.05)
        link.say('  torque during = %r' % link.ask('vescuart', 'torque'))
        link.say('  errorflags during = %r' % link.ask('vescuart', 'errorflags'))

        link.say('  releasing torque, then waiting out the VESC watchdog')
        for _ in range(12):
            link.window.send_value('axis', 'force', 0, instance=0)
            link.pump(0.05)
        link.pump(1.5)
        link.say('  torque after = %r' % link.ask('vescuart', 'torque'))
        link.say('  drvtype after = %r' % link.ask('axis', 'drvtype'))
        link.say('  errorflags after = %r' % link.ask('vescuart', 'errorflags'))
        link.say('  pos after = %r' % link.ask('vescuart', 'pos'))
    finally:
        link.say('=== restoring the original driver ===')
        if original is not None:
            link.window.send_value('axis', 'drvtype', original, instance=0)
            link.pump(1.0)
            link.window.send_value('axis', 'save', 1)
            link.pump(1.5)
            link.say('axis.0.drvtype restored to %r' % link.ask('axis', 'drvtype'))
        link.chooser.disconnect_port()
        link.chooser._opener.stop_all(500)  # pylint: disable=protected-access
        link.app.quit()

    return 0


if __name__ == '__main__':
    sys.exit(main())
