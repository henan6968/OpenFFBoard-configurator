"""Serial UI module.

Regroup all required classes to manage the Serial Connection UI
and the link with the communication module.

Module : serial_ui
Authors : yannick
"""
import PyQt6.QtCore
import PyQt6.QtGui
import PyQt6.QtSerialPort
import PyQt6.QtWidgets
import base_ui
import helper
import main
import safe_serial

BAUDRATE = 115200
"""Baud rate used to talk to the board."""

CONNECT_POLL_MS = 100
"""How often the connect worker is polled."""


class SerialChooser(base_ui.WidgetUI, base_ui.CommunicationHandler):
    """This classe is the main Serial Chooser manager.

    *) Display the UI
    *) Manage the user interraction : connect/disconnect
    *) Manage the serial port status
    """

    OFFICIAL_VID_PID = [(0x1209, 0xFFB0)]  # Highlighted in serial selector
    connected = PyQt6.QtCore.pyqtSignal(bool)
    shown = PyQt6.QtCore.pyqtSignal()
    hidden = PyQt6.QtCore.pyqtSignal()
    visible = PyQt6.QtCore.pyqtSignal(bool)

    def __init__(self, serial: PyQt6.QtSerialPort.QSerialPort, main_ui: main.MainUi):
        """Initialize the manager with the QSerialPort for serial commmunication and the mainUi."""
        base_ui.WidgetUI.__init__(self, main_ui, "serialchooser.ui")
        base_ui.CommunicationHandler.__init__(self)
        self.main = main_ui
        self.main_id = None
        self._classes = []
        self._class_ids = {}
        self._port = None
        self._ports = []
        self._logging = False
        self._closing = False
        self._pending_connect = False

        # The serial device is not opened on the GUI thread: a wedged USB CDC
        # board makes QSerialPort.open() block in a kernel IOCTL that has no
        # timeout, which used to freeze the whole application. Opening happens
        # on a worker thread instead (see safe_serial.py).
        self._opener = safe_serial.PortOpener(self)
        self._serial = serial
        if self._serial is None:
            self._serial = safe_serial.make_port()
        self._serial.errorOccurred.connect(self.serial_error)

        self._connect_timer = PyQt6.QtCore.QTimer(self)
        self._connect_timer.setInterval(CONNECT_POLL_MS)
        self._connect_timer.timeout.connect(self._poll_connect)

        self.pushButton_refresh.clicked.connect(self.get_ports)
        self.pushButton_connect.clicked.connect(self.serial_connect_button)
        self.pushButton_send.clicked.connect(self.send_line)
        self.lineEdit_cmd.returnPressed.connect(self.send_line)
        self.pushButton_ok.clicked.connect(self.main_btn)

        self.update()

    # ------------------------------------------------------------------
    # port ownership
    # ------------------------------------------------------------------

    @property
    def port(self):
        """The QSerialPort currently in use, or None when disconnected."""
        return self._serial

    def _status(self, state, text):
        """Show connection progress in the status bar, if there is one."""
        bar = getattr(self.main, 'wrapper_status_bar', None)
        if bar is not None:
            bar.set_connection_status(state, text)

    @port.setter
    def port(self, new_port):
        """Swap in a freshly opened port and rewire the signals."""
        old = self._serial
        if old is not new_port:
            safe_serial.detach_signals(old, self.serial_error)
        self._serial = new_port
        if new_port is None:
            return
        new_port.errorOccurred.connect(self.serial_error)

        comms = getattr(base_ui.CommunicationHandler, 'comms', None)
        if comms is not None and getattr(comms, 'serial', None) is not new_port:
            comms.attach(new_port)

    def _discard_port(self, port):
        """Stop using a port, keeping it referenced but never used again."""
        if port is None:
            return
        safe_serial.detach_signals(port, self.serial_error)
        safe_serial.discard_port(port)
        self._opener.graveyard.append(port)
        if self._serial is port:
            self._serial = None

    # ------------------------------------------------------------------
    # UI events
    # ------------------------------------------------------------------

    def showEvent(self, event): # pylint: disable=unused-argument, invalid-name
        """On show event, init the param.

        Connect the communication module with the history widget to load the board response.
        """
        if not self._logging:
            self.get_raw_reply().connect(self.serial_log)
            self._logging = True
        self.shown.emit()

    # Tab is hidden
    def hideEvent(self, event): # pylint: disable=unused-argument, invalid-name
        """On hide event, disconnect the event.

        Disconnect the communication module with the history widget
        to stop to log the board response.
        """
        if self._logging:
            self.get_raw_reply().disconnect(self.serial_log)
            self._logging = False
        self.hidden.emit()

    def serial_log(self, txt):
        """Add a new text in the history widget."""
        if isinstance(txt, list):
            txt = "\n".join(txt)
        else:
            txt = str(txt)
        self.serialLogBox.append(txt)

    def send_line(self):
        """Read the command input text, display it in history widget and send it to the board."""
        cmd = self.lineEdit_cmd.text() + "\n"
        self.serial_log(">" + cmd)
        self.serial_write_raw(cmd)

    def write(self, data):
        """Write data to the serial port."""
        port = self._serial
        if port is not None and port.isOpen():
            port.write(data)

    def update(self):
        """Update the UI when a connection is successfull.

        Disable connection button, dropbox, etc.
        Emit for all the UI the [connected] event.
        """
        port = self._serial
        if port is not None and port.isOpen():
            self.pushButton_connect.setText(self.tr("Disconnect"))
            self.comboBox_port.setEnabled(False)
            self.pushButton_refresh.setEnabled(False)
            self.pushButton_send.setEnabled(True)
            self.lineEdit_cmd.setEnabled(True)
            self.connected.emit(True)
            self.get_main_classes()
        else:
            self.pushButton_connect.setText(self.tr("Connect"))
            self.comboBox_port.setEnabled(True)
            self.pushButton_refresh.setEnabled(True)
            self.pushButton_send.setEnabled(False)
            self.lineEdit_cmd.setEnabled(False)
            self.connected.emit(False)
            self.groupBox_system.setEnabled(False)

    # ------------------------------------------------------------------
    # connect / disconnect
    # ------------------------------------------------------------------

    def serial_connect_button(self):
        """Connect when disconnected, disconnect when connected."""
        port = self._serial
        if port is not None and port.isOpen():
            self.disconnect_port()
        else:
            self.serial_connect()

        self.update()

    def serial_connect(self):
        """Start opening the selected port, without blocking the GUI thread.

        Returns True when a port is already open, False while an attempt is
        in flight. The outcome arrives later, through :meth:`_poll_connect`.
        """
        if self._pending_connect or self._opener.busy:
            return False

        self.select_port(self.comboBox_port.currentIndex())
        if self._port is None:
            self.main.log("No serial port selected")
            return False

        port = self._serial
        if port is not None and port.isOpen():
            return True

        self._pending_connect = True
        self.main.log("Connecting to %s..." % self._port.portName())
        self._status('connecting', self._port.portName())
        self.pushButton_connect.setEnabled(False)
        self._opener.reset()
        self._opener.open(self._port.portName(), BAUDRATE)
        self._connect_timer.start()
        return False

    def try_auto_connect(self):
        """Start connecting once, without any dialog.

        Returns True when the link is already up. Auto-connect treats a False
        return as "retry later" and never blocks on it.
        """
        if self.serial_connect():
            return True
        port = self._serial
        return port is not None and port.isOpen()

    def _poll_connect(self):
        """Collect the result of the open worker. Runs on the GUI thread."""
        if self._opener.timed_out:
            self._connect_timer.stop()
            self._pending_connect = False
            self.pushButton_connect.setEnabled(True)
            result = self._opener.abandon()
            self._on_open_finished(result)
            return

        result = self._opener.poll()
        if result is None:
            return

        self._connect_timer.stop()
        self._pending_connect = False
        self.pushButton_connect.setEnabled(True)
        self._on_open_finished(result)

    def _on_open_finished(self, result):
        """Finish a connection attempt using the worker's result."""
        if result is None:
            return
        if result.timed_out:
            portname = self._port.portName() if self._port is not None else "?"
            self.main.log(
                "Port %s is not responding after %d ms: the device is probably "
                "crashed or stuck mid-reset." % (portname, safe_serial.OPEN_TIMEOUT_MS)
            )
            self._status('noresponse',
                         "%s did not answer within %d ms"
                         % (portname, safe_serial.OPEN_TIMEOUT_MS))
            self.main.log(
                "Unplug and replug the board (or check that no other program "
                "holds the port), then press Connect again."
            )
            self.update()
            return

        if not result.ok:
            self._report_open_failure(result)
            self._discard_port(result.port)
            self.update()
            return

        self.port = result.port
        self.main.log("Port %s open" % self._port.portName())
        self._status('open', self._port.portName())
        self.update()

    def _report_open_failure(self, result):
        """Explain why the port could not be opened."""
        portname = self._port.portName() if self._port is not None else "?"
        self.main.log("Can not open port %s (%s)" % (portname, result.message))
        self._status('failed', "%s: %s" % (portname, result.message))
        self.main.log(
            "If another program is using the port, close it first. "
            "If the board is crashed or still resetting, unplug and replug it."
        )

    def disconnect_port(self):
        """Close the current port and let the board go."""
        self._discard_port(self._serial)
        self._opener.stop_thread(CONNECT_POLL_MS)
        self._pending_connect = False
        self._connect_timer.stop()
        self.pushButton_connect.setEnabled(True)

    def serial_error(self, error):
        """Close the port when the device reports an unrecoverable error."""
        errors = PyQt6.QtSerialPort.QSerialPort.SerialPortError
        if error in (errors.NoError, errors.NotOpenError) or self._closing:
            return

        port = self._serial
        if port is None:
            return

        self.main.log("Serial error: " + port.errorString())
        if error in (errors.ResourceError, errors.DeviceNotFoundError, errors.PermissionError) and port.isOpen():
            self._closing = True
            self.main.reset_port(immediate=True)
            self._closing = False

    def select_port(self, port_id):
        """Change the selected port."""
        if port_id != -1 and len(self._ports) != 0:
            self._port = self._ports[port_id]
        else:
            self._port = None

    def get_ports(self):
        """Get all the serial port available on the computer.

        If the VID.VIP is compatible with openFFBoard color the text in green,
        else put it in red.
        """
        oldport = self._port if self._port else None

        self._ports = PyQt6.QtSerialPort.QSerialPortInfo().availablePorts()
        self.comboBox_port.clear()
        sel_idx = 0
        nb_compatible_device = 0
        for i, port in enumerate(self._ports):
            supported_vid_pid = (
                port.vendorIdentifier(),
                port.productIdentifier(),
            ) in self.OFFICIAL_VID_PID
            name = port.portName() + " : " + port.description()

            if supported_vid_pid and not name.startswith("cu."):
                name += " (FFBoard device)"
            else:
                name += " (Unsupported device)"
            self.comboBox_port.addItem(name)

            if supported_vid_pid and not name.startswith("cu."):
                sel_idx = i
                nb_compatible_device = nb_compatible_device + 1
                self.comboBox_port.setItemData(
                    i,
                    PyQt6.QtGui.QColor("green"),
                    PyQt6.QtCore.Qt.ItemDataRole.ForegroundRole,
                )
            else:
                self.comboBox_port.setItemData(
                    i,
                    PyQt6.QtGui.QColor("red"),
                    PyQt6.QtCore.Qt.ItemDataRole.ForegroundRole,
                )

        plist = [p.portName() for p in self._ports]
        if (
            (oldport is not None)
            and (
                (oldport.vendorIdentifier(), oldport.productIdentifier())
                in self.OFFICIAL_VID_PID
            )
            and (oldport.portName() in plist)
        ):
            self.comboBox_port.setCurrentIndex(plist.index(oldport.portName()))
        else:
            self.comboBox_port.setCurrentIndex(sel_idx)  # preselect found entry

        self.select_port(self.comboBox_port.currentIndex())
        self.update()

        return nb_compatible_device

    def auto_connect(self, nb_compatible_device):
        """Connect straight away when there is exactly one board."""
        if nb_compatible_device == 1:
            self.serial_connect_button()

    def update_mains(self, dat):
        """Parse the list of main classes received from board, and update the combobox."""
        self.comboBox_main.clear()
        self._class_ids, self._classes = helper.classlistToIds(dat)

        if self.main_id is None:
            # self.main.resetPort()
            self.groupBox_system.setEnabled(False)
            return
        self.groupBox_system.setEnabled(True)

        helper.updateClassComboBox(
            self.comboBox_main, self._class_ids, self._classes, self.main_id
        )

        self.main.log("Detected mode: " + self.comboBox_main.currentText())
        self.main.update_tabs()

    def get_main_classes(self):
        """Get the main classes available from the board in Async."""

        def fct(i):
            """Store the main currently selected to refresh the UI."""
            self.main_id = i

        self.get_value_async("main", "id", fct, conversion=int, delete=True)
        self.get_value_async("sys", "lsmain", self.update_mains, delete=True)

    def main_btn(self):
        """Read the select main class in the combobox.

        Push it to the board and display the reload warning.
        """
        index = self._classes[self.comboBox_main.currentIndex()][0]
        self.send_value("sys", "main", index)
        self.main.reconnect()
        msg = PyQt6.QtWidgets.QMessageBox(
            PyQt6.QtWidgets.QMessageBox.Icon.Information,
            "Main class changed",
            "Chip is rebooting. Please reconnect.",
        )
        msg.exec()
