"""VESC over UART configuration tab.

Drives the UART based VescUART driver (class ids 0x8D/0x8E), which lives in
Firmware/FFBoard/UserExtensions/VescUART.cpp. Its CAN sibling, vesc_ui.VescUI,
covers the CAN based VescCAN driver (0x87/0x88).

VescUART was written to expose the same command names as VescCAN so that this tab
could look and behave the same, but three things differ and are handled here:

  1. the command handler name - the UART driver registers as "vescuart", not
     "vesc". Using "vesc" would resolve to the CAN driver, or to nothing at all
     when only the UART driver is instantiated.
  2. offbcanid / vesccanid do not exist on VescUART, so they are never read or
     written.
  3. the CAN settings button is meaningless, so it is removed together with the
     other CAN only rows of the settings grid.

res/vesc.ui is reused unchanged so both tabs stay visually identical. Unlike
VescUI this class is self contained rather than a subclass, so the CAN tab keeps
its exact current behaviour no matter what happens here.

Module : vescuart_conf_ui
"""

import math

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QGridLayout

import main
from base_ui import CommunicationHandler, WidgetUI


class VescUARTUI(WidgetUI, CommunicationHandler):
    """Configuration tab for a VescUART instance (VESC over UART)."""

    # Command handler name registered by VescUART.cpp
    CLSNAME = "vescuart"

    # Widgets in res/vesc.ui that only make sense for a CAN connection. They sit
    # in rows 0-2 of the "VESC Settings" QGridLayout, so removing them from the
    # grid also drops their row and the remaining controls move up.
    CAN_ONLY_WIDGETS = (
        "label_2",                 # "CAN speed"
        "pushButton_cansettings",  # opens the CANPort settings dialog
        "label_5",                 # "OpenFFBoard Axis CAN ID"
        "spinBox_OFFB_can_id",
        "label_11",                # "Vesc CAN ID"
        "spinBox_VESC_can_Id",
    )

    # Read once when the tab is shown, and polled while it is visible
    SETTINGS_CMDS = ["useencoder", "offset"]
    POLL_CMDS = ["vescstate", "errorflags", "voltage", "pos", "encrate", "torque"]

    def __init__(self, main=None, unique=None):
        WidgetUI.__init__(self, main, "vesc.ui")
        CommunicationHandler.__init__(self)
        self.main = main  # type: main.MainUi
        self.prefix = unique

        self._strip_can_controls()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.updateTimer)

        self.pushButton_apply.clicked.connect(self.apply)
        self.pushButton_manualRead.clicked.connect(self.manualEncPosRead)
        self.pushButton_eraseOffset.clicked.connect(self.eraseOffset)
        self.pushButton_refresh.clicked.connect(self.init_ui)

        self.register_callback(
            self.CLSNAME, "useencoder", self.updateEncoderUI, self.prefix, int
        )
        self.register_callback(
            self.CLSNAME, "offset", self.updateOffset, self.prefix, int
        )
        self.register_callback(
            self.CLSNAME, "errorflags", self.errorCb, self.prefix, int
        )
        self.register_callback(
            self.CLSNAME, "encrate", self.label_encoder_rate.setText, self.prefix, str
        )
        self.register_callback(
            self.CLSNAME,
            "voltage",
            lambda mv: self.label_voltage.setText(f"{mv/1000}V"),
            self.prefix,
            int,
        )
        self.register_callback(self.CLSNAME, "pos", self.posCb, self.prefix, int)
        self.register_callback(
            self.CLSNAME, "vescstate", self.stateCb, self.prefix, int
        )
        self.register_callback(self.CLSNAME, "torque", self.torqueCb, self.prefix, int)

        self.init_ui()
    # ---- tab lifecycle --------------------------------------------------

    def showEvent(self, event):
        self.init_ui()
        self.timer.start(500)

    def hideEvent(self, event):
        self.timer.stop()

    # ---- board interaction ----------------------------------------------

    def init_ui(self):
        """Read the settings VescUART implements (no CAN ids over UART)."""
        self.send_commands(self.CLSNAME, self.SETTINGS_CMDS, self.prefix)

    def updateTimer(self):
        self.send_commands(self.CLSNAME, self.POLL_CMDS, self.prefix)

    def apply(self):
        """Write the settings back. Nothing CAN related here."""
        self.send_value(
            self.CLSNAME,
            "useencoder",
            (1 if self.checkBox_useEncoder.isChecked() else 0),
            instance=self.prefix,
        )
        self.init_ui()  # Update UI

    def manualEncPosRead(self):
        self.send_command(self.CLSNAME, "forceposread", instance=self.prefix)

    def eraseOffset(self):
        self.send_value(self.CLSNAME, "offset", 0, instance=self.prefix)
        self.init_ui()

    # ---- callbacks ------------------------------------------------------

    def vescstate(self, i):
        """Same mapping as the CAN tab: values come from VescUARTState."""
        _result = "Invalid state"
        if i == 0:
            _result = "No connection"
        elif i == 1:
            _result = "vesc FW incompatible"
        elif i == 2:
            _result = "Comm ok"
        elif i == 3:
            _result = "vesc compatible"
        elif i == 4:
            _result = "ready"
        elif i == 5:
            _result = "error"

        return _result

    def updateEncoderUI(self, dat):
        self.checkBox_useEncoder.setChecked(dat)
        visible = dat == 1
        for widget in (
            self.label_6,
            self.label_encoder_rate,
            self.label_pos,
            self.label_7,
            self.horizontalSlider_pos,
            self.line,
            self.label_9,
            self.doubleSpinBox_encoderOffset,
            self.pushButton_eraseOffset,
        ):
            widget.setVisible(visible)

    def updateOffset(self, preset):
        self.doubleSpinBox_encoderOffset.setValue(preset / 10000)

    def stateCb(self, state):
        self.label_state.setText(self.vescstate(state))
        if (state == 0) and (self.label_errors.isEnabled()):
            self.label_errors.setEnabled(0)
            self.label_voltage.setEnabled(0)
            self.label_encoder_rate.setEnabled(0)
        elif (state != 0) and (not self.label_errors.isEnabled()):
            self.label_errors.setEnabled(1)
            self.label_voltage.setEnabled(1)
            self.label_encoder_rate.setEnabled(1)

    def torqueCb(self, v):
        vesc_torque = math.ceil(v / 100)
        self.label_torque.setText(str(vesc_torque))
        if vesc_torque >= 0:
            self.progressBar_torqueneg.setValue(0)
            self.progressBar_torquepos.setValue(vesc_torque)
        else:
            self.progressBar_torqueneg.setValue(-vesc_torque)
            self.progressBar_torquepos.setValue(0)

    def posCb(self, v):
        vesc_encoder_position = (360 * v) / 1000000000
        self.label_pos.setText("{:.2f}".format(vesc_encoder_position))
        self.horizontalSlider_pos.setValue(int(round(vesc_encoder_position)))

    def errorCb(self, dat):
        txt = "Ok"
        if dat != 0:
            txt = "Error code " + str(dat)
        self.label_errors.setText(txt)

    # ---- helpers --------------------------------------------------------

    def _strip_can_controls(self):
        """Remove the CAN only rows from the "VESC Settings" grid."""
        grid = self.groupBox.layout()
        for name in self.CAN_ONLY_WIDGETS:
            widget = getattr(self, name, None)
            if widget is None:
                continue
            if isinstance(grid, QGridLayout):
                grid.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
            else:
                # Layout changed in vesc.ui: hiding still gives a correct tab
                widget.setVisible(False)
