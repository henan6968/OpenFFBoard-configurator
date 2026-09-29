"""Show what the axis and VescUART pages read like with zh_CN installed.

The configurator picks its language from a saved setting, so this installs the
translator directly and prints the widget texts, which is what the user sees.

Run with:  python tools/check_zh_cn_ui.py
"""

import os
import re
import sys

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

import PyQt6.QtCore  # noqa: E402
import PyQt6.QtWidgets  # noqa: E402

APP = PyQt6.QtWidgets.QApplication([])

import main as app_main  # noqa: E402

app_main.app = APP
TRANSLATOR = PyQt6.QtCore.QTranslator()
if not TRANSLATOR.load('translations/zh_CN.qm'):
    print('could not load translations/zh_CN.qm')
    raise SystemExit(1)
APP.installTranslator(TRANSLATOR)
app_main.translator = TRANSLATOR


def texts(widget, kinds=('QLabel', 'QPushButton', 'QCheckBox', 'QGroupBox')):
    """Every visible string of the given widget classes."""
    out = []
    for kind in kinds:
        for child in widget.findChildren(getattr(PyQt6.QtWidgets, kind)):
            text = child.text() if hasattr(child, 'text') else child.title()
            if text and re.search(r'[A-Za-z\u4e00-\u9fff]', text):
                out.append(text.replace('\n', ' '))
    return out


def main():
    """Build the real window and dump the two pages."""
    from main import MainUi  # noqa: PLC0415

    window = MainUi()
    window.check_configurator_update = lambda *a, **k: None

    print('=== menu bar ===')
    for action in window.menubar.actions():
        print('   ', action.text())

    print()
    print('=== tab titles ===')
    names = [window.tabWidget_main.tabText(i)
             for i in range(window.tabWidget_main.count())]
    for name in names:
        print('   ', name)
    if not names:
        print('    (no board tabs until a board is connected)')

    for index, name in enumerate(names):
        if 'axis' not in name.lower():
            continue
        page = window.tabWidget_main.widget(index)
        print()
        print('=== %s page ===' % name)
        for text in texts(page):
            print('   ', text)
        break

    print()
    print('=== VescUART tab strings (from the .ui, translated) ===')
    for source in ('UART Settings', 'Force position read', 'Erase offset',
                   'Refresh', 'Apply', 'Use VESC encoder', 'Encoder offset'):
        print('    %-22s -> %s'
              % (source, TRANSLATOR.translate('VescUARTUI', source)
                 or TRANSLATOR.translate('Form', source)))

    APP.quit()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
