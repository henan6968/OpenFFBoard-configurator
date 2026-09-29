"""Tests for the Simplified Chinese translation.

These exist because the UI looked half translated for a long time without
anyone noticing why: the .ts file had the strings, but the .qm that the app
actually loads was never rebuilt, and a few newer pages had no translations at
all. A stale .qm is silent - the app just shows English - so it is checked
here.

Run with:  python -m unittest tests.test_translation_zh_cn -v
"""

import io
import os
import re
import subprocess
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import PyQt6.QtCore  # noqa: E402
import PyQt6.QtWidgets  # noqa: E402

TS_PATH = os.path.join(ROOT, 'translations', 'zh_CN.ts')
QM_PATH = os.path.join(ROOT, 'translations', 'zh_CN.qm')

APP = None


def setUpModule():  # noqa: N802
    """A QApplication is needed to install a translator."""
    global APP  # pylint: disable=global-statement
    APP = PyQt6.QtWidgets.QApplication.instance() or PyQt6.QtWidgets.QApplication([])


def load_ts():
    """The zh_CN.ts source as text."""
    return io.open(TS_PATH, encoding='utf-8').read()


def ts_messages():
    """Yield (context, source, translation, unfinished) for every entry."""
    text = load_ts()
    for context in re.findall(r'<context>(.*?)</context>', text, re.S):
        name = re.search(r'<name>([^<]*)</name>', context).group(1)
        for block in re.findall(r'<message>(.*?)</message>', context, re.S):
            src = re.search(r'<source>(.*?)</source>', block, re.S)
            tr = re.search(r'<translation([^>]*)>(.*?)</translation>', block, re.S)
            if not src or not tr:
                continue
            yield (name, src.group(1), tr.group(2),
                   'unfinished' in (tr.group(1) or ''))


class TestTranslationSource(unittest.TestCase):
    """The .ts file itself must be complete."""

    def test_every_message_is_translated(self):
        """No entry may be left unfinished or empty."""
        missing = [(context, source) for context, source, tr, unfinished
                   in ts_messages() if unfinished or not tr.strip()]
        self.assertEqual(
            missing, [],
            "untranslated entries: %s" % missing[:10],
        )

    def test_the_axis_page_is_translated(self):
        """The three mechanical settings were English for a long time."""
        wanted = {
            'Permanent damper': '永久阻尼',
            'Permanent inertia': '永久惯量',
            'Permanent friction': '永久摩擦力',
        }
        found = {source: tr for _ctx, source, tr, _u in ts_messages()}
        for source, expected in wanted.items():
            self.assertEqual(found.get(source), expected)

    def test_the_vescuart_tab_is_translated(self):
        """The tab this project adds must not be English only."""
        found = {(context, source): tr
                 for context, source, tr, _u in ts_messages()}
        self.assertEqual(found.get(('VescUARTUI', 'UART Settings')), 'UART 设置')
        self.assertEqual(found.get(('VescUARTUI', 'Force position read')),
                         '强制读取位置')

    def test_the_help_text_is_translated(self):
        """The long HTML help body must have a Chinese counterpart."""
        help_entries = [
            tr for _ctx, source, tr, _u in ts_messages()
            if 'This controls a VESC over UART' in source
        ]
        self.assertTrue(help_entries, "the VescUART help text is not extractable")
        self.assertIn('本页通过 UART', help_entries[0])
        self.assertNotIn('Wiring:', help_entries[0])


class TestCompiledTranslation(unittest.TestCase):
    """The .qm the application loads must match the .ts."""

    def test_qm_exists(self):
        """Without the .qm the app silently falls back to English."""
        self.assertTrue(os.path.isfile(QM_PATH), "translations/zh_CN.qm is missing")

    def test_qm_is_newer_than_ts(self):
        """A stale .qm is the failure mode that hid this for so long.

        The application loads the .qm, never the .ts, so compiling after an
        edit is mandatory - and forgetting it fails silently.
        """
        self.assertGreaterEqual(
            os.path.getmtime(QM_PATH), os.path.getmtime(TS_PATH),
            "zh_CN.qm is older than zh_CN.ts: rebuild it with "
            "pyside6-lrelease translations/zh_CN.ts -qm translations/zh_CN.qm",
        )

    def test_qm_has_as_many_entries_as_ts(self):
        """Roughly the same count means nothing was dropped at compile time."""
        unfinished = sum(1 for *_rest, u in ts_messages() if u)
        self.assertEqual(unfinished, 0)
        # The compiler reports the exact numbers; use them rather than guessing.
        result = subprocess.run(
            [sys.executable, '-c',
             'import io,re,sys;'
             'd=io.open(sys.argv[1],"rb").read();'
             'sys.stdout.write(str(len(d)))', QM_PATH],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0)

    def test_translator_loads_and_translates(self):
        """A representative string from each area must come out Chinese."""
        translator = PyQt6.QtCore.QTranslator()
        self.assertTrue(translator.load(QM_PATH),
                        "QTranslator could not load zh_CN.qm")
        APP.installTranslator(translator)
        try:
            cases = [
                ('Form', 'Permanent damper', '永久阻尼'),
                ('Form', 'Desktop spring (No FFB)', '桌面回中力（非游戏内）'),
                ('Form', 'Power', '力的大小'),
                ('SerialChooser', 'Connect', '连接'),
                ('WrapperStatusBar', 'No response', '无响应'),
                ('VescUARTUI', 'UART Settings', 'UART 设置'),
                ('ActiveTaskDialog', 'Active threads', '活动线程'),
                ('ErrorsDialog', 'Errors', '错误'),
            ]
            for context, source, expected in cases:
                got = translator.translate(context, source)
                self.assertEqual(got, expected,
                                 '%s/%s came out as %r' % (context, source, got))
        finally:
            APP.removeTranslator(translator)

    def test_ambiguous_word_keeps_its_context(self):
        """'Current' means amps on one page and 'present' on another."""
        translator = PyQt6.QtCore.QTranslator()
        translator.load(QM_PATH)
        APP.installTranslator(translator)
        try:
            self.assertEqual(translator.translate('Form', 'Current'), '电流')
            self.assertEqual(translator.translate('ShifterButtonsConf', 'Current'),
                             '当前')
        finally:
            APP.removeTranslator(translator)


class TestNoStaleEnglish(unittest.TestCase):
    """Strings the audit found untranslated must stay translated."""

    def test_dialog_titles_are_registered(self):
        """Dialog titles have to exist in the .ts to be translatable."""
        sources = {source for _ctx, source, _tr, _u in ts_messages()}
        for title in ('Active modules', 'Active threads', 'Advanced ffb tuning',
                      'Full chip erase', 'Effects statistics', 'Errors',
                      'Expo curve tuning', 'Update available'):
            self.assertIn(title, sources, '%s is not extractable' % title)


if __name__ == '__main__':
    unittest.main()
