"""VESC over UART configuration tab.

Drives the UART based VescUART driver (class ids 0x8D/0x8E), which lives in
Firmware/FFBoard/UserExtensions/VescUART.cpp. Its CAN sibling, vesc_ui.VescUI,
covers the CAN based VescCAN driver (0x87/0x88).

VescUART was written to expose the same command names as VescCAN so that this tab
could look and behave the same, but a few things differ and are handled here:

  1. the command handler name - the UART driver registers as "vescuart", not
     "vesc". Using "vesc" would resolve to the CAN driver, or to nothing at all
     when only the UART driver is instantiated.
  2. offbcanid / vesccanid do not exist on VescUART, so they are never read or
     written, and the CAN only rows of the settings grid are removed.
  3. the position scale is different: VescUART reports turns scaled by 1e4,
     VescCAN by 1e9. See POS_SCALE.
  4. everything in the tab that talks about CAN - the timeout button and the
     whole help text - is rewritten for UART.

res/vesc.ui is reused unchanged so both tabs stay visually identical; this class
edits its own copy of the widgets at construction time. Unlike VescUI this class
is self contained rather than a subclass, so the CAN tab keeps its exact current
behaviour no matter what happens here.

Module : vescuart_conf_ui
"""

import math

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QGridLayout

import main
from base_ui import CommunicationHandler, WidgetUI

#: VescUART reports ``pos`` as turns * 1e4 (VescUART.cpp, VescUART_commands::pos).
#: VescCAN uses 1e9 for the same quantity, which is why this tab cannot simply
#: copy vesc_ui's conversion - doing so displayed 0.00 deg forever.
POS_SCALE = 10000.0

HELP_HTML = """
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" "http://www.w3.org/TR/REC-html40/strict.dtd">
<html><head><meta name="qrichtext" content="1" /><style type="text/css">
p, li { white-space: pre-wrap; }
</style></head><body style="font-family:'MS Shell Dlg 2'; font-size:8.25pt;">

<p><b>This controls a VESC over UART (serial), and reads the wheel position
from the VESC.</b> It is the serial counterpart of the CAN based "VESC" tab;
both drive the same kind of motor, only the wire differs.</p>

<p><span style="text-decoration: underline;">Wiring:</span></p>
<p>OpenFFBoard USART3: <b>PB10 = TX, PB11 = RX</b>, GND to GND.</p>
<p>Connect them <b>crossed</b> to the VESC: F407 PB10 (TX) to the VESC RX,
F407 PB11 (RX) to the VESC TX.</p>
<p>On the VESC side either socket works, both speak the same protocol:</p>
<p>&nbsp;&nbsp;&bull; the <b>"UART2"</b> socket (PC10/PC11) - fixed 115200 baud.
It only works when <b>Enable Permanent UART</b> is turned on in VESC Tool;
otherwise the VESC parks those pins as inputs and stays silent.</p>
<p>&nbsp;&nbsp;&bull; the <b>COMM</b> header (USART3, PB10/PB11) - the speed is
whatever VESC Tool shows under <i>App Settings &rarr; General &rarr; UART
Baudrate</i>.</p>
<p>Both ends must use the <b>same baud rate</b>; this firmware currently
targets 115200.</p>

<p><span style="text-decoration: underline;">VESC Tool setup:</span></p>
<p>1. Turn on <b>Enable Permanent UART</b> (App Settings &rarr; General).</p>
<p>2. Set the motor current limit under <i>Motor Settings &rarr; General &rarr;
Current &rarr; Motor Current Max</i>. That value is the full scale for the
torque this tab sends, so check it - too high can injure you or damage the
hardware.</p>
<p>3. Do not leave VESC Tool connected on the same port while using the
OpenFFBoard: only one program can hold a COM port.</p>

<p><span style="text-decoration: underline;">This tab:</span></p>
<p><b>Use VESC encoder</b> - let the OpenFFBoard take the wheel angle from the
VESC instead of a local encoder. Uncheck it if you wired a real encoder to the
axis and set that up in the Axis tab.</p>
<p><b>Encoder offset</b> - a turn offset subtracted from the reported angle.
Erase it to re-centre, then turn the wheel where you want the centre and press
Apply.</p>
<p><b>Force position read</b> - ask for one angle sample right now. Useful to
check the link is alive: the "Encoder rate" readout above should move.</p>

<p><span style="text-decoration: underline;">Readouts:</span></p>
<p><b>Axis state</b> - No connection / FW incompatible / Comm ok / compatible /
ready / error. Anything but "ready" means the driver is not talking to the
VESC yet.</p>
<p><b>Encoder rate</b> - angle replies per second. The driver polls the VESC
for a fresh angle instead of streaming, so this is the real link rate; around
300 Hz on this hardware.</p>
<p><b>Voltage</b> - the VESC input voltage as the VESC itself measures it.</p>

<p><span style="text-decoration: underline;">Copyright:</span></p>
<p>VESC is an OpenSource project by Benjamin Vedder, GNU GPL 3, see
www.vesc-project.com. No VESC source code is used in this firmware.</p>
</body></html>
"""


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
        self._retarget_texts()

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
        # The offset is an editable field, so Apply has to write it too - over
        # CAN it was display only, but here "Erase offset" then Apply is the
        # documented way to re-centre the wheel.
        self.send_value(
            self.CLSNAME,
            "offset",
            int(round(self.doubleSpinBox_encoderOffset.value() * POS_SCALE)),
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
        self.doubleSpinBox_encoderOffset.setValue(preset / POS_SCALE)

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
        """Show the wheel angle in degrees.

        VescUART reports turns scaled by POS_SCALE, so this is percent of a
        full turn, not the 1e9 scale VescCAN uses.
        """
        turns = v / POS_SCALE
        degrees = 360.0 * turns
        # Displayed inside a full turn, like the Axis tab, so the readout is
        # readable after the wheel has been spun; the sign is kept.
        wrapped = math.fmod(degrees, 360.0)
        self.label_pos.setText("{:.2f}".format(wrapped))
        self.horizontalSlider_pos.setValue(int(round(wrapped)) % 360)

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

    def _retarget_texts(self):
        """Replace the CAN wording that res/vesc.ui ships with.

        res/vesc.ui is shared with the CAN tab, so this only ever touches this
        instance's widgets.
        """
        group = getattr(self, "groupBox", None)
        if group is not None:
            group.setTitle(self.tr("UART Settings"))

        button = getattr(self, "pushButton_manualRead", None)
        if button is not None:
            button.setText(self.tr("Force position read"))
            button.setToolTip(
                self.tr("Ask the VESC for one angle sample now, to check the link.")
            )

        slider = getattr(self, "horizontalSlider_pos", None)
        if slider is not None:
            slider.setRange(0, 359)

        help_box = getattr(self, "textBrowser", None)
        if help_box is not None:
            help_box.setHtml(HELP_HTML)
