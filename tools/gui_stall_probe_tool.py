"""Measure the worst GUI-thread stall while the configurator talks to a board.

This is the acceptance test for the "configurator freezes the UI" fix. It
starts the real MainUi window, lets auto-connect do its thing against the
currently plugged board, and measures how long the GUI thread ever goes
without running a heartbeat timer. Any interruption of the event loop shows up
as a gap, which is exactly what the user experienced as a freeze.

The board is NOT required to be alive: a dead board is the interesting case,
because opening its port used to block forever inside ``QSerialPort.open()``.

Every direct port access in this probe itself goes through safe_serial, so the
probe cannot reproduce the very freeze it measures.

Run with:
    python tools/gui_stall_probe.py [seconds] [port]

Defaults to 20 seconds, auto-connect, and a probe of every listed port.
"""

import faulthandler
import os
import sys
import time

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

import PyQt6.QtCore  # noqa: E402
import PyQt6.QtWidgets  # noqa: E402

DURATION_S = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
FORCED_PORT = sys.argv[2] if len(sys.argv) > 2 else None

faulthandler.enable()
faulthandler.dump_traceback_later(DURATION_S + 10, exit=False, file=sys.stderr)

import main as app_main  # noqa: E402
import safe_serial  # noqa: E402

HEARTBEAT_MS = 100
STALL_WARN_MS = 400


SLOW_MS = float(os.environ.get('SLOW_MS', '80'))


def _wrap(obj, name):
    """Time one method, dump the stack if it blocks for a long time."""
    original = getattr(obj, name)

    def timed(*args, **kwargs):
        started = time.monotonic()
        try:
            return original(*args, **kwargs)
        finally:
            took = (time.monotonic() - started) * 1000
            if took > SLOW_MS:
                print("  >> %s.%s took %.0f ms" % (type(obj).__name__, name, took),
                      flush=True)
                if took > 600:
                    import traceback  # noqa: PLC0415 (debug tool)
                    traceback.print_stack()

    setattr(obj, name, timed)


def stack_watchdog(probe, dump_after_ms=500, stop=None):
    """Background thread that dumps the GUI stack when it stops beating.

    faulthandler's own watchdog proved unreliable here, so the frames are read
    directly with sys._current_frames().
    """
    import sys as _sys  # noqa: PLC0415 (debug tool)
    import traceback as _traceback  # noqa: PLC0415 (debug tool)

    import threading as _threading  # noqa: PLC0415 (debug tool)
    main_thread_id = _threading.main_thread().ident
    while not (stop is not None and stop.is_set()):
        time.sleep(0.05)
        silent_ms = (time.monotonic() - probe.last) * 1000
        if silent_ms < dump_after_ms:
            continue
        print('  ~~ main thread silent for %.0f ms, stack:' % silent_ms, flush=True)
        frames = _sys._current_frames()  # pylint: disable=protected-access
        frame = frames.get(main_thread_id)
        if frame is not None:
            _traceback.print_stack(frame)
        time.sleep(1.0)


class StallProbe:
    """Heartbeat timer that records every gap in the event loop.

    A missing heartbeat means the GUI thread did not return to the event
    loop in time, which is what the user sees as a freeze. The probe also
    tracks shorter gaps, because those are equally invisible in a
    configurator that is supposed to stay responsive.
    """

    def __init__(self):
        self.last = time.monotonic()
        self.t0 = self.last
        self.stalls = []
        self.gaps = []
        self.beats = 0
        self.stage = 'startup'
        self.cpu_ms = 0.0

    def beat(self):
        """Called from the GUI thread; records the gap since the last beat."""
        now = time.monotonic()
        gap_ms = (now - self.last) * 1000
        self.last = now
        self.beats += 1
        label = getattr(self, 'stage', '')
        times = os.times()
        cpu_ms = (times.user + times.system) * 1000  # process-wide, incl. threads
        cpu_delta = cpu_ms - getattr(self, 'cpu_ms', cpu_ms)
        self.cpu_ms = cpu_ms
        if gap_ms > HEARTBEAT_MS * 1.5:
            self.gaps.append(gap_ms)
        if gap_ms > STALL_WARN_MS:
            self.stalls.append(gap_ms)
            print("  !! GUI stalled for %.0f ms (t=%.2fs stage=%s cpu_delta=%.0f ms)"
                  % (gap_ms, now - self.t0, label, cpu_delta), flush=True)

    def mark(self, stage):
        """Record what the app is doing now, for the stall report."""
        self.stage = stage
        print("  -- t=%.2fs stage=%s" % (time.monotonic() - self.t0, stage),
              flush=True)

    def worst(self):
        """Worst observed stall, in milliseconds."""
        return max(self.stalls) if self.stalls else 0.0


class PortProbe:
    """Opens each candidate port through safe_serial, off the GUI thread."""

    def __init__(self):
        self.opener = safe_serial.PortOpener()
        self.results = []
        self.pending = None
        self.job = None
        self.started_at = 0.0

    def start(self, names):
        """Kick off a sequential probe of ``names``."""
        self.job = list(names)
        self.pending = None
        self._next()

    def _next(self):
        """Start the next port, or report the results."""
        if not self.job:
            if self.results:
                print("port probe results:")
                for name, outcome in self.results:
                    print("  %-6s %s" % (name, outcome))
            return
        name = self.job.pop(0)
        self.pending = name
        self.started_at = time.monotonic()
        self.opener.reset()
        self.opener.open(name, 115200)

    def poll(self):
        """Collect a probe result. Returns True while more work is pending."""
        if self.pending is None:
            return False
        if self.opener.timed_out:
            result = self.opener.abandon()
            self.results.append((self.pending, "NO RESPONSE (%s)" % result.message))
            self.pending = None
            self._next()
            return self.pending is not None

        result = self.opener.poll()
        if result is None:
            return True

        took = (time.monotonic() - self.started_at) * 1000
        if result.ok:
            self.results.append((self.pending, "open OK in %.0f ms" % took))
            safe_serial.discard_port(result.port)
            self.opener.graveyard.append(result.port)
        else:
            self.results.append(
                (self.pending, "refused in %.0f ms (%s)" % (took, result.message))
            )
        self.pending = None
        self._next()
        return self.pending is not None


def main_probe():
    """Build the real window, let it run, and report the worst stall."""
    app = PyQt6.QtWidgets.QApplication(sys.argv)
    # MainUi expects the app and translator that the __main__ block installs.
    app_main.app = app
    app_main.translator = PyQt6.QtCore.QTranslator()
    window = app_main.MainUi()
    probe = StallProbe()
    probe.mark('window-built')

    timer = PyQt6.QtCore.QTimer()
    timer.setInterval(HEARTBEAT_MS)
    timer.timeout.connect(probe.beat)
    timer.start()

    import threading  # noqa: PLC0415 (debug tool)
    stop_flag = threading.Event()
    if os.environ.get('STACK_WATCHDOG'):
        threading.Thread(target=stack_watchdog, args=(probe, 500, stop_flag),
                         daemon=True).start()

    # Keep the update checker out of the measurement: it does network IO.
    window.check_configurator_update = lambda *a, **k: None

    print("=" * 72)
    print("GUI stall probe: %.0f s, heartbeat %d ms, warn over %d ms"
          % (DURATION_S, HEARTBEAT_MS, STALL_WARN_MS))
    n_ports = window.serialchooser.get_ports()
    names = [p.portName() for p in window.serialchooser._ports]  # pylint: disable=protected-access
    print("compatible FFBoard devices found: %d" % n_ports)
    print("ports present: %s" % ", ".join(names))

    window.show()
    probe.mark('window-shown')

    # Wrap the chatty serial entry points so a stall can be attributed.
    chooser = window.serialchooser
    # Qt slots that get connected/disconnected by identity are left alone:
    # replacing them breaks disconnect() and crashes the app.
    for name in ('get_ports', 'serial_connect', '_poll_connect', '_on_open_finished',
                 'update', 'get_main_classes', 'update_mains'):
        _wrap(chooser, name)
    for name in ('reset_tabs', 'update_tabs', 'autoconnect', 'update_timer'):
        if hasattr(window, name):
            _wrap(window, name)
    bar = getattr(window, 'wrapper_status_bar', None)
    if bar is not None:
        for name in ('set_board_text', 'set_connection_status', 'update_ram_used',
                     'update_temp'):
            if hasattr(bar, name):
                _wrap(bar, name)
    comms = getattr(app_main.base_ui.CommunicationHandler, 'comms', None)
    if comms is not None:
        _wrap(comms, 'serialReceive')

    port_probe = PortProbe()

    probe_timer = PyQt6.QtCore.QTimer()
    probe_timer.setInterval(50)
    probe_timer.timeout.connect(port_probe.poll)
    probe_timer.start()
    if FORCED_PORT is None:
        port_probe.start(names)

    def after_connect():
        """Report the outcome of the auto-connect attempt."""
        chooser = window.serialchooser
        port = chooser.port
        opener = chooser._opener  # pylint: disable=protected-access
        bar = getattr(window, 'wrapper_status_bar', None)
        window_bar = window.statusBar()
        print("status bar message: %r" % (window_bar.currentMessage() if window_bar else None))
        if bar is not None:
            print("board label: %r" % bar.label_board.text())
        print("main id: %r, classes: %d" % (chooser.main_id, len(chooser._classes)))  # pylint: disable=protected-access
        print("connect attempt finished: port=%s open=%s pending=%s busy=%s"
              % (port.portName() if port else None,
                 port.isOpen() if port else False,
                 chooser._pending_connect,  # pylint: disable=protected-access
                 opener.busy))
        print("open failures recorded: %d" % len(opener.graveyard))
        for entry in opener.graveyard:
            print("  %r" % (entry,))
        print("worker threads alive: %d" % opener.thread_count)
        probe.mark('connected')

    def log_times():
        """Dump the process CPU split, to tell busy from blocked."""
        times = os.times()
        print("cpu: user=%.2fs system=%.2fs children_user=%.2fs children_sys=%.2fs"
              % (times.user, times.system, times.children_user, times.children_system),
              flush=True)

    if FORCED_PORT:
        print("forcing a connect attempt on %s" % FORCED_PORT, flush=True)
        if FORCED_PORT in names:
            window.serialchooser.comboBox_port.setCurrentIndex(names.index(FORCED_PORT))
        started = time.monotonic()
        window.serialchooser.serial_connect()
        print("  serial_connect() returned in %.0f ms"
              % ((time.monotonic() - started) * 1000), flush=True)
        log_times()

    def timed_autoconnect():
        """Time the auto-connect attempt too."""
        started = time.monotonic()
        window.autoconnect()
        print("  autoconnect() returned in %.0f ms"
              % ((time.monotonic() - started) * 1000), flush=True)

    PyQt6.QtCore.QTimer.singleShot(1500, timed_autoconnect)
    PyQt6.QtCore.QTimer.singleShot(7000, after_connect)
    PyQt6.QtCore.QTimer.singleShot(int(DURATION_S * 1000), app.quit)

    started = time.monotonic()
    app.exec()
    elapsed = time.monotonic() - started

    print("=" * 72)
    print("ran %.1f s, %d heartbeats, expected about %d"
          % (elapsed, probe.beats, int(elapsed * 1000 / HEARTBEAT_MS)))
    print("stalls over %d ms: %d" % (STALL_WARN_MS, len(probe.stalls)))
    if probe.gaps:
        worst_gaps = sorted(probe.gaps)[-5:]
        print("gaps over %d ms: %d, worst: %s"
              % (int(HEARTBEAT_MS * 1.5), len(probe.gaps),
                 ", ".join("%.0f" % g for g in worst_gaps)))
        print("total time missing from the event loop: %.0f ms"
              % sum(probe.gaps))
    print("worst GUI stall: %.0f ms" % probe.worst())
    log_times()
    stop_flag.set()
    print("RESULT: %s" % ("PASS - the GUI never froze"
                          if probe.worst() < STALL_WARN_MS
                          else "FAIL - the GUI froze"))

    window.serialchooser._opener.stop_all(500)  # pylint: disable=protected-access
    port_probe.opener.stop_all(500)
    return 0 if probe.worst() < STALL_WARN_MS else 1


if __name__ == "__main__":
    sys.exit(main_probe())
