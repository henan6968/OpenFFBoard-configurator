"""Quick manual check: can COM3 be opened right now?"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import PyQt6.QtCore  # noqa: E402
import PyQt6.QtSerialPort  # noqa: E402
import PyQt6.QtWidgets  # noqa: E402

APP = PyQt6.QtWidgets.QApplication([])
APP.setApplicationName('checkport')

import safe_serial  # noqa: E402

PORT = sys.argv[1] if len(sys.argv) > 1 else 'COM3'

print('ports:', [p.portName() for p in PyQt6.QtSerialPort.QSerialPortInfo().availablePorts()])

opener = safe_serial.PortOpener()
started = time.monotonic()
opener.open(PORT, 115200)
result = None
while time.monotonic() - started < 8:
    APP.processEvents()
    result = opener.poll()
    if result is not None:
        break
    if opener.timed_out:
        result = opener.abandon()
        break
    time.sleep(0.02)

print('%s -> %r in %.0f ms' % (PORT, result, (time.monotonic() - started) * 1000))
if result is not None and result.ok:
    port = result.port
    print('isOpen:', port.isOpen(), 'errorString:', port.errorString())
    safe_serial.discard_port(port)
opener.stop_all(500)
