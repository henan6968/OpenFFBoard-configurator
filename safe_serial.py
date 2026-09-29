"""Serial port helpers that cannot freeze the GUI.

Why this module exists
----------------------
On Windows, ``QSerialPort::open()`` issues blocking control transfers while it
brings the port up: ``ClearCommError``, ``GetCommState``, ``SetCommState``,
``SetupComm``, ``PurgeComm`` and ``EscapeCommFunction(SETDTR)``. The Win32 API
gives those IOCTLs no timeout at all, so when a USB CDC device is wedged -- a
crashed or mid-reset microcontroller whose endpoints stall instead of NAKing,
a half-enumerated composite device, or a port already claimed by another
process -- the IOCTL never completes and ``open()`` never returns.

Because the configurator called ``open()`` straight from the GUI thread, the
whole application froze: no repaint, no systray, no log, nothing. The same is
true for ``setDataTerminalReady()`` and ``clear()``, and for ``close()`` on an
already-broken handle.

There is no timeout to pass: once that IOCTL is stuck, the calling thread is
stuck with it, and it cannot be cancelled from the outside. Bounding waits
inside Qt only helps for the waits that do accept a timeout, which is not
where the freeze happens.

What this module does instead
-----------------------------
The open sequence is moved onto a throwaway worker thread. The GUI thread
starts the worker, returns immediately, and collects the result through a Qt
signal once the worker finishes. If the worker never finishes, the GUI thread
is unaffected: it abandons that port object (a "graveyard" reference is kept
so nothing is garbage collected underneath a live native handle), tells the
user the device is not responding, and stays fully usable.

``SafeSerialPort`` remains the port type. Its overrides bound the calls that
*do* accept a timeout -- ``waitForReadyRead`` and ``waitForBytesWritten`` --
so a device that stops answering mid-session degrades into a logged timeout
rather than a hang.

Module : safe_serial
"""

import threading
import time

import PyQt6.QtCore
import PyQt6.QtSerialPort

OPEN_TIMEOUT_MS = 2000
"""How long the GUI waits for an open worker before declaring the device dead."""

CLOSE_TIMEOUT_MS = 1500
"""How long the GUI waits for a worker thread to stop."""

WAIT_SLICE_MS = 150
"""Length of a single wait slice handed to Qt."""

WAIT_TIMEOUT_MS = 1000
"""Ceiling for one waitForReadyRead()/waitForBytesWritten() call."""


class OpenResult:
    """Outcome of one threaded open attempt."""

    def __init__(self, status, port=None, message=""):
        self.status = status
        self.port = port
        self.message = message

    @property
    def ok(self):
        """True when the port is open and ready to be used."""
        return self.status == 'opened'

    @property
    def timed_out(self):
        """True when the device accepted the handle but never came up."""
        return self.status == 'timeout'

    def __repr__(self):
        return 'OpenResult(%r, message=%r)' % (self.status, self.message)


class SafeSerialPort(PyQt6.QtSerialPort.QSerialPort):
    """A ``QSerialPort`` whose blocking waits are always bounded in time.

    The open sequence is handled by :class:`OpenPortWorker` instead; this
    subclass exists so that the waits Qt *does* allow a timeout on cannot
    stall the calling thread for longer than :data:`WAIT_TIMEOUT_MS`.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hang_count = 0
        self.last_hang = None

    def note_hang(self, what):
        """Record that a call had to be cut short."""
        self.hang_count += 1
        self.last_hang = what

    def reset_hang_stats(self):
        """Forget previous watchdog hits (call after a good open)."""
        self.hang_count = 0
        self.last_hang = None

    def waitForReadyRead(self, msecs=WAIT_TIMEOUT_MS):  # noqa: N802 (Qt name)
        """Wait for data, but never for longer than ``msecs`` in total.

        The budget is shared between slices, so a device that ignores each
        slice still cannot make this call exceed the ceiling.
        """
        budget = WAIT_TIMEOUT_MS if msecs is None or msecs < 0 else min(int(msecs), WAIT_TIMEOUT_MS)
        deadline = time.monotonic() + (budget / 1000.0)

        while True:
            try:
                if not self.isOpen():
                    return False
                if self.bytesAvailable() > 0:
                    return True
            except RuntimeError:
                return False  # underlying C++ object is gone

            left = deadline - time.monotonic()
            if left <= 0:
                break

            slice_ms = int(min(WAIT_SLICE_MS, max(1, left * 1000)))
            try:
                if super().waitForReadyRead(slice_ms):
                    return True
            except RuntimeError:
                return False

        return False

    def waitForBytesWritten(self, msecs=WAIT_TIMEOUT_MS):  # noqa: N802 (Qt name)
        """Flush pending output, but never for longer than ``msecs`` in total."""
        budget = WAIT_TIMEOUT_MS if msecs is None or msecs < 0 else min(int(msecs), WAIT_TIMEOUT_MS)
        deadline = time.monotonic() + (budget / 1000.0)

        while True:
            try:
                if not self.isOpen():
                    return False  # match QSerialPort, which fails on a closed port
                if self.bytesToWrite() == 0:
                    return True
            except RuntimeError:
                return False

            left = deadline - time.monotonic()
            if left <= 0:
                break

            slice_ms = int(min(WAIT_SLICE_MS, max(1, left * 1000)))
            try:
                if super().waitForBytesWritten(slice_ms):
                    return True
            except RuntimeError:
                return False

        self.note_hang("device did not accept data")
        return False


def detach_signals(port, slot):
    """Disconnect ``slot`` from ``port``, tolerating a port already gone."""
    if port is None:
        return
    try:
        port.errorOccurred.disconnect(slot)
    except (TypeError, RuntimeError):
        pass


def discard_port(port):
    """Close a port that is no longer wanted.

    Closing can itself block on a broken handle, so callers that must
    not stall keep the object referenced instead of relying on this.
    """
    if port is None:
        return
    try:
        port.close()
    except Exception:  # pylint: disable=broad-except
        pass


def make_port(parent=None):
    """Build a :class:`SafeSerialPort`, falling back to a plain QSerialPort.

    A problem importing or instantiating the subclass must never stop the
    configurator from starting.
    """
    try:
        return SafeSerialPort(parent)
    except Exception:  # pylint: disable=broad-except
        import sys  # noqa: PLC0415 (only needed on the fallback path)
        print("safe_serial: falling back to plain QSerialPort", file=sys.stderr)
        return PyQt6.QtSerialPort.QSerialPort(parent)


class OpenPortWorker(PyQt6.QtCore.QObject):
    """Opens a serial port on a worker thread and reports back by signal.

    The worker creates the port itself and moves it to the GUI thread before
    signalling, so the object that ends up in use belongs to the GUI thread
    and behaves like a locally created ``QSerialPort``.

    The blocking calls are made from ``run()``, which executes on the worker's
    own thread. If one of them never returns, only this thread is lost.
    """

    finished = PyQt6.QtCore.pyqtSignal(object)

    #: Poll interval used to notice an early abort before the stall.
    ABORT_POLL_MS = 25

    def __init__(self, port_name, baudrate):
        super().__init__()
        self._port_name = port_name
        self._baudrate = baudrate
        self._aborted = [False]
        app = PyQt6.QtCore.QCoreApplication.instance()
        self._main_thread = app.thread() if app is not None else PyQt6.QtCore.QThread.currentThread()

    def abort(self):
        """Ask the worker to give up as soon as the port call returns.

        Called from the GUI thread once the open attempt is written
        off. It cannot interrupt a call already inside the kernel, but
        it stops the worker from signalling a stale result later and
        lets its thread finish as soon as the call does return.
        """
        self._aborted[0] = True

    @PyQt6.QtCore.pyqtSlot()
    def run(self):
        """Open the port. Runs on the worker thread."""
        try:
            self._open_and_report()
        finally:
            # Without this the thread keeps running its own event loop
            # forever, leaking a thread per connection attempt.
            thread = PyQt6.QtCore.QThread.currentThread()
            thread.quit()

    def _open_and_report(self):
        """Do the actual work and emit exactly one result."""
        port = make_port()
        try:
            port.setPortName(self._port_name)
            port.setBaudRate(self._baudrate)
            if self._wait_for_clearance():
                return
            opened = port.open(PyQt6.QtCore.QIODevice.OpenModeFlag.ReadWrite)
            if self._aborted[0]:
                # Written off while the open was in flight: do not claim
                # the port, just let go of it quietly.
                discard_port(port)
                return
            if not opened or not port.isOpen():
                reason = port.errorString() or "unknown error"
                self._hand_back(port)
                self.finished.emit(OpenResult('failed', port, reason))
                return

            # Discard whatever the OS buffered before we started listening.
            try:
                port.clear(PyQt6.QtSerialPort.QSerialPort.Direction.AllDirections)
            except Exception:  # pylint: disable=broad-except
                pass
            try:
                port.setDataTerminalReady(True)
            except Exception:  # pylint: disable=broad-except
                port.note_hang("device ignored the DTR request")

            self._hand_back(port)
            self.finished.emit(OpenResult('opened', port, ''))
        except Exception as exc:  # pylint: disable=broad-except
            self._hand_back(port)
            self.finished.emit(OpenResult('failed', port, str(exc)))

    def _wait_for_clearance(self):
        """Wait briefly for a possible early abort. True to bail out.

        Keeps a worker that was abandoned before it even reached the
        kernel from opening a port nobody wants any more.
        """
        deadline = time.monotonic() + (self.ABORT_POLL_MS / 1000.0)
        while time.monotonic() < deadline:
            if self._aborted[0]:
                return True
            time.sleep(0.001)
        return self._aborted[0]

    def _hand_back(self, port):
        """Move the port to the GUI thread so the app can own it."""
        try:
            port.moveToThread(self._main_thread)
        except Exception:  # pylint: disable=broad-except
            pass


class PortOpener(PyQt6.QtCore.QObject):
    """Starts open workers and never blocks the caller.

    Usage from the GUI thread::

        opener = PortOpener()
        opener.open('COM5', 115200)
        ...                 # the event loop keeps running
        result = opener.poll()   # from a QTimer; None while still working

    A worker that never returns leaves the opener ``busy`` forever;
    :meth:`abandon` is then used to give up on it and keep the UI alive.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._result = None
        self._state = 'idle'
        self._thread = None
        self._worker = None
        self.graveyard = []
        self.started_at = None
        # Threads kept alive on purpose: a QThread destroyed while still
        # running aborts the whole process, so an abandoned worker is
        # never dropped on the floor.
        self._threads = []

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------

    @property
    def busy(self):
        """True while a worker has not reported back yet."""
        with self._lock:
            return self._state == 'busy'

    @property
    def elapsed_ms(self):
        """Milliseconds since the current worker was started."""
        with self._lock:
            started = self.started_at
        if started is None:
            return 0
        return (time.monotonic() - started) * 1000

    @property
    def timed_out(self):
        """True when a worker has been running longer than OPEN_TIMEOUT_MS.

        Only meaningful while :attr:`busy`; the worker may still finish,
        but by now the user deserves an explanation and the port is
        written off.
        """
        with self._lock:
            if self._state != 'busy' or self.started_at is None:
                return False
            started = self.started_at
        return (time.monotonic() - started) * 1000 > OPEN_TIMEOUT_MS

    def reset(self):
        """Forget a stale result so a new attempt starts clean."""
        with self._lock:
            self._result = None
            if self._state != 'busy':
                self._state = 'idle'

    # ------------------------------------------------------------------
    # operations
    # ------------------------------------------------------------------

    def open(self, port_name, baudrate):
        """Start opening a port. Returns False if a worker is already running."""
        with self._lock:
            if self._state == 'busy':
                return False
            self._state = 'busy'
            self._result = None
            self.started_at = time.monotonic()

        thread = PyQt6.QtCore.QThread()
        worker = OpenPortWorker(port_name, baudrate)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(lambda result: self._store(result))
        worker.finished.connect(lambda _result: thread.quit())
        thread.finished.connect(lambda: self._forget_thread(thread))
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        with self._lock:
            self._thread = thread
            self._worker = worker
            self._threads.append(thread)
        thread.start()
        return True

    @property
    def thread_count(self):
        """Number of worker threads still alive (diagnostics)."""
        with self._lock:
            return len(self._threads)

    def _forget_thread(self, thread):
        """Drop a worker thread that has run to completion."""
        with self._lock:
            self._threads = [t for t in self._threads if t is not thread]

    def _store(self, result):
        """Called (eventually) from the worker thread when a result is ready."""
        with self._lock:
            self._result = result
            self._state = 'ready'
            self.started_at = None

    def poll(self):
        """Return the pending result, or None. Call this from a QTimer.

        A result is delivered exactly once. ``None`` means "still working".
        """
        with self._lock:
            result = self._result
            self._result = None
            if result is not None:
                self._state = 'idle'
        return result

    def abandon(self, reason="device did not respond"):
        """Give up on a stuck worker and quarantine whatever it holds.

        The worker thread is left to finish on its own: there is no safe way
        to cancel a thread blocked in a kernel IOCTL. The port object it owns
        is kept referenced in :attr:`graveyard` so it is never garbage
        collected underneath a live native handle, and is never used again.
        """
        with self._lock:
            if self._state != 'busy':
                return None
            self._state = 'idle'
            started = self.started_at
            worker = self._worker
            self._worker = None
            self._result = None
            self.started_at = None

        if worker is not None:
            worker.abort()

        waited = (time.monotonic() - started) * 1000 if started else 0
        dead = OpenResult('timeout', None, "%s after %.0f ms" % (reason, waited))
        self.graveyard.append(dead)
        return dead

    def stop_thread(self, timeout_ms=CLOSE_TIMEOUT_MS):
        """Ask the worker to stop and wait a bounded time for it.

        Returns True when the thread really finished. A thread stuck
        in a kernel IOCTL cannot be stopped; it is left alive in
        :attr:`_threads` and never reused. Killing it is not an
        option: destroying a running QThread aborts the process.
        """
        with self._lock:
            thread = self._thread
            worker = self._worker
            self._thread = None
            self._worker = None
        if thread is None:
            return True
        if worker is not None:
            worker.abort()
        try:
            thread.quit()
            return thread.wait(timeout_ms)
        except RuntimeError:
            # The C++ thread object was already deleted (it finished and
            # ran deleteLater), which means it did stop after all.
            return True

    def stop_all(self, timeout_ms=CLOSE_TIMEOUT_MS):
        """Stop every worker thread this opener ever started."""
        stopped = True
        with self._lock:
            threads = list(self._threads)
            self._threads = []
        for thread in threads:
            try:
                thread.quit()
                stopped = thread.wait(timeout_ms) and stopped
            except RuntimeError:
                pass  # already deleted, so already stopped
        return stopped
