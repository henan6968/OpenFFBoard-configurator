"""Tests for the VESC-over-UART configuration tab.

The tab reuses res/vesc.ui, so most of its job is undoing what the CAN wording
assumes. These tests pin the three things that were actually wrong when the tab
first shipped:

  * ``pos`` was converted with the CAN scale (1e9) while VescUART reports turns
    times 1e4, so the readout sat at 0.00 deg forever;
  * the timeout button still said "Test CAN Connection";
  * the whole help text explained a CAN bus, termination resistors and CAN IDs.

Run with:  python -m unittest tests.test_vescuart_ui -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import PyQt6.QtCore  # noqa: E402
import PyQt6.QtWidgets  # noqa: E402

import base_ui  # noqa: E402
import main as app_main  # noqa: E402
import vescuart_conf_ui  # noqa: E402

APP = None


def setUpModule():  # noqa: N802
    """The tab builds real widgets, so an application must exist."""
    global APP  # pylint: disable=global-statement
    APP = PyQt6.QtWidgets.QApplication.instance() or PyQt6.QtWidgets.QApplication([])
    app_main.app = APP
    if not hasattr(app_main, 'translator'):
        app_main.translator = PyQt6.QtCore.QTranslator()


class RecordingComms:
    """Stands in for SerialComms: records what the tab asks the board for."""

    def __init__(self):
        self.sent = []

    def serialWriteRaw(self, cmd):  # noqa: N802 (mirrors SerialComms)
        """Remember one command string."""
        self.sent.append(cmd)


def build_tab(unique=0):
    """Build the tab without a main window.

    Only the widgets themselves are under test here, so a stand-in main and a
    recording comms object are enough; nothing here talks to a board.
    """
    comms = RecordingComms()
    base_ui.CommunicationHandler.comms = comms
    tab = vescuart_conf_ui.VescUARTUI(main=None, unique=unique)
    tab.comms = comms
    return tab


class TestPositionScale(unittest.TestCase):
    """The scale is the difference between a live readout and a dead one."""

    def test_pos_scale_matches_firmware(self):
        """VescUART multiplies turns by 1e4 before replying."""
        self.assertEqual(vescuart_conf_ui.POS_SCALE, 10000.0)

    def test_quarter_turn_reads_90_degrees(self):
        """0.25 turns (2500 raw) must read as 90 degrees, not 0.00."""
        tab = build_tab()
        tab.posCb(2500)
        self.assertEqual(tab.label_pos.text(), "90.00")
        self.assertEqual(tab.horizontalSlider_pos.value(), 90)

    def test_small_angle_is_not_rounded_away(self):
        """The old 1e9 conversion showed 0.00 for anything under a full turn."""
        tab = build_tab()
        tab.posCb(-111)          # about -0.0111 turns
        value = float(tab.label_pos.text())
        self.assertLess(value, 0.0)
        self.assertAlmostEqual(value, -3.996, places=2)

    def test_multi_turn_is_wrapped_for_display(self):
        """A wheel spun past one turn still shows a sane 0-359 readout."""
        tab = build_tab()
        tab.posCb(18750)         # 1.875 turns
        self.assertEqual(tab.label_pos.text(), "315.00")
        self.assertEqual(tab.horizontalSlider_pos.value(), 315)

    def test_slider_stays_in_range(self):
        """The slider cannot be driven outside its range by a wrapped value."""
        tab = build_tab()
        for raw in (0, 9999, 10000, 1_000_000, -1_000_000):
            tab.posCb(raw)
            self.assertGreaterEqual(tab.horizontalSlider_pos.value(), 0)
            self.assertLessEqual(tab.horizontalSlider_pos.value(), 359)

    def test_offset_round_trip(self):
        """What Apply writes is what the readout showed."""
        tab = build_tab()
        tab.updateOffset(18750)
        self.assertAlmostEqual(tab.doubleSpinBox_encoderOffset.value(), 1.875, places=4)


class TestUartWording(unittest.TestCase):
    """Nothing in this tab may still describe a CAN bus."""

    def setUp(self):  # noqa: N802
        self.tab = build_tab()

    def test_timeout_button_is_not_about_can(self):
        """The button asks for a position sample; it never touched CAN."""
        text = self.tab.pushButton_manualRead.text()
        self.assertNotIn("CAN", text.upper())
        self.assertIn("position", text.lower())

    def test_help_text_describes_uart(self):
        """The help must talk about UART, not a CAN bus."""
        help_text = self.tab.textBrowser.toPlainText()
        self.assertIn("UART", help_text)
        self.assertIn("PB10", help_text)
        self.assertIn("Enable Permanent UART", help_text)

    def test_help_text_drops_can_specific_advice(self):
        """Termination resistors and CAN IDs are meaningless here."""
        help_text = self.tab.textBrowser.toPlainText()
        self.assertNotIn("120 Ohm", help_text)
        self.assertNotIn("CAN ID", help_text)
        self.assertNotIn("CAN speed", help_text)

    def test_settings_group_is_renamed(self):
        """The group box says UART, since only UART settings remain."""
        self.assertNotIn("CAN", self.tab.groupBox.title().upper())


class TestCanControlsAreGone(unittest.TestCase):
    """The CAN only rows must not be left behind in the grid."""

    def test_can_only_widgets_are_deleted(self):
        """Every CAN-only widget is detached from the tab."""
        tab = build_tab()
        for name in vescuart_conf_ui.VescUARTUI.CAN_ONLY_WIDGETS:
            widget = getattr(tab, name, None)
            if widget is None:
                continue  # removed from the .ui altogether
            self.assertFalse(
                widget.isVisibleTo(tab),
                "%s is still part of the tab" % name,
            )

    def test_encoder_offset_survives(self):
        """The widgets the UART tab does use are untouched."""
        tab = build_tab()
        for name in ('doubleSpinBox_encoderOffset', 'pushButton_eraseOffset',
                     'pushButton_apply', 'pushButton_refresh',
                     'checkBox_useEncoder', 'label_encoder_rate'):
            self.assertTrue(hasattr(tab, name), "%s is missing" % name)


if __name__ == "__main__":
    unittest.main()
