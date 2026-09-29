"""Fill in the missing zh_CN translations in translations/zh_CN.ts.

The upstream zh_CN.ts predates the Axis page, the remote CAN/RMD pages, the
signature dialogs and the VescUART tab, so those strings were still English in
the UI. This maps every source string that is not a finished translation and
rewrites the <translation> element in place.

Run from the repository root:
    python translations/tool_fill_zh_cn.py [--force]

``--force`` also rewrites entries that are already translated, which is how a
wording mistake is corrected.

Then recompile:
    pyside6-lrelease translations/zh_CN.ts -qm translations/zh_CN.qm
"""

import io
import re
import sys

TS_PATH = 'translations/zh_CN.ts'

FORCE = '--force' in sys.argv

# source string -> Simplified Chinese
TRANSLATIONS = {
    # --- About / signature dialogs -----------------------------------------
    'Signature: ': '签名：',
    'Missing': '缺失',
    'UID: Unknown': 'UID：未知',
    'Signature: Unknown': '签名：未知',
    'Enter signature': '输入签名',
    'Enter signature and click "submit" to write to chip. THIS CAN NOT BE UNDONE':
        '输入签名后点「提交」写入芯片。此操作不可撤销！',

    # --- Axis page: mechanical settings ------------------------------------
    'Torque curve...': '力矩曲线…',
    'Permanent damper': '永久阻尼',
    'Permanent inertia': '永久惯量',
    'Permanent friction': '永久摩擦力',
    'Exponential torque curve correction (Gamma function)':
        '力矩曲线指数修正（Gamma 函数）',
    'Exponent': '指数',
    'Reset to 1 (Off)': '重置为 1（关闭）',
    'Stores and reloads absolute encoder offset at startup for centering':
        '开机时存储并重新载入绝对编码器偏移，用于回中',
    'Store encoder offset': '存储编码器偏移',

    # --- Remote CAN source page --------------------------------------------
    'Remote CAN source': '远程 CAN 信号源',
    'Update interval': '更新间隔',
    'Digital CAN ID': '数字量 CAN ID',
    'Analog CAN ID': '模拟量 CAN ID',
    'Apply IDs': '应用 ID',
    'Data output': '数据输出',
    'Digital values:': '数字量：',
    'Analog values:': '模拟量：',

    # --- ODrive page --------------------------------------------------------
    'Model:': '型号：',
    'Not available': '不可用',

    # --- RMD motor page -----------------------------------------------------
    'Active requests/High speed position mode': '主动上报/高速位置模式',
    'Test mode': '测试模式',

    # --- TMC4671 openloop test dialog ---------------------------------------
    'Openloop test': '开环测试',
    'PWM/Current': 'PWM/电流',
    'Speed': '速度',
    'Current': '电流',
    'Reverse': '反转',
    'Yes i know what i am doing. Enable openloop mode.':
        '我知道自己在做什么，启用开环模式。',

    # --- VescUART tab -------------------------------------------------------
    'UART Settings': 'UART 设置',
    'Force position read': '强制读取位置',
    'Ask the VESC for one angle sample now, to check the link.':
        '立刻向 VESC 取一次角度，用来确认链路是否正常。',

    # --- status bar connection state ----------------------------------------
    'Connecting': '连接中',
    'No response': '无响应',
    'Connection failed': '连接失败',
    'Connected': '已连接',

    # --- dialogs and group boxes --------------------------------------------
    'Active modules': '活动模块',
    'Active threads': '活动线程',
    'Advanced ffb tuning': '高级力反馈调校',
    'Advanced encoder tuning': '高级编码器调校',
    'Full chip erase': '全片擦除',
    'WARNING': '警告',
    'Effects graphics': '效果曲线图',
    'Effects statistics': '效果统计',
    'Encoder settings': '编码器设置',
    'Local Encoder': '本地编码器',
    'SPI Settings': 'SPI 设置',
    'BISS Settings': 'BISS 设置',
    'SSI Settings': 'SSI 设置',
    'Errors': '错误',
    'Expo curve tuning': 'Expo 曲线调校',
    'Driver (not connected)': '驱动器（未连接）',
    'Update available': '有可用更新',
}

#: Context specific overrides, because one English word can mean different
#: things on different pages, and pylupdate6 recycles a translation across
#: contexts.
CONTEXT_TRANSLATIONS = {
    # On the shifter button page "Current" means "present", not amps.
    ('ShifterButtonsConf', 'Current'): '当前',
}

#: Long HTML help bodies are matched by a distinctive fragment, because
#: repeating them verbatim in source is impractical.
HTML_TRANSLATIONS = [
    (
        'This controls a VESC over UART',
        '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" '
        '"http://www.w3.org/TR/REC-html40/strict.dtd">\n'
        '<html><head><meta name="qrichtext" content="1" /><style type="text/css">\n'
        'p, li { white-space: pre-wrap; }\n'
        '</style></head><body style="font-family:\'MS Shell Dlg 2\'; '
        'font-size:8.25pt;">\n'
        '<p><b>本页通过 UART（串口）控制 VESC，并从 VESC 读取轮子位置。</b>'
        '它是 CAN 版「VESC」页的串口对应版本，两者驱动同样的电机，只是接线不同。</p>\n'
        '<p><span style="text-decoration: underline;">接线：</span></p>\n'
        '<p>OpenFFBoard USART3：<b>PB10 = TX，PB11 = RX</b>，GND 接 GND。</p>\n'
        '<p>与 VESC <b>交叉</b>连接：F407 的 PB10(TX) 接 VESC 的 RX，'
        'F407 的 PB11(RX) 接 VESC 的 TX。</p>\n'
        '<p>VESC 侧两个插座都可以，协议相同：</p>\n'
        '<p>&nbsp;&nbsp;&bull; <b>「UART2」</b>插座（PC10/PC11）——固定 115200。'
        '必须在 VESC Tool 里打开 <b>Enable Permanent UART</b>，否则 VESC 会把这两个'
        '引脚当作输入，完全不回应。</p>\n'
        '<p>&nbsp;&nbsp;&bull; <b>COMM</b> 排针（USART3，PB10/PB11）——速率就是 '
        'VESC Tool 里 <i>App Settings &rarr; General &rarr; UART Baudrate</i> '
        '显示的值。</p>\n'
        '<p>两端必须用<b>相同的波特率</b>；本固件目前用 115200。</p>\n'
        '<p><span style="text-decoration: underline;">VESC Tool 设置：</span></p>\n'
        '<p>1. 打开 <b>Enable Permanent UART</b>（App Settings &rarr; General）。</p>\n'
        '<p>2. 在 <i>Motor Settings &rarr; General &rarr; Current &rarr; '
        'Motor Current Max</i> 设置电机电流上限。这个值就是本页所发力矩的满量程，'
        '务必确认——设得太大可能伤人或者损坏硬件。</p>\n'
        '<p>3. 用 OpenFFBoard 时不要让 VESC Tool 占着同一个串口：一个 COM 口同时'
        '只能被一个程序打开。</p>\n'
        '<p><span style="text-decoration: underline;">本页控件：</span></p>\n'
        '<p><b>使用VESC编码器</b>——让 OpenFFBoard 直接用 VESC 的角度，而不是本地'
        '编码器。如果你在轴上接了真实编码器并在「Axis」页配置好了，就取消勾选。</p>\n'
        '<p><b>编码器偏移</b>——从上报角度里减去的圈数偏移。先擦除偏移重新对中，'
        '再把轮子转到你想要的中心位置，然后点「应用」。</p>\n'
        '<p><b>强制读取位置</b>——立刻取一次角度采样。用来确认链路是否正常：'
        '上面的「编码器速率」应该有变化。</p>\n'
        '<p><span style="text-decoration: underline;">读数说明：</span></p>\n'
        '<p><b>轴状态</b>——无连接 / 固件不兼容 / 通信正常 / 兼容 / 就绪 / 错误。'
        '只要不是「就绪」，就说明驱动还没和 VESC 通上话。</p>\n'
        '<p><b>编码器速率</b>——每秒收到的角度回复数。驱动是主动向 VESC 轮询角度，'
        '而不是被动接收，所以这就是真实链路速率；本硬件上约 300 Hz。</p>\n'
        '<p><b>输入电压</b>——VESC 自己测到的输入电压。</p>\n'
        '<p><span style="text-decoration: underline;">版权：</span></p>\n'
        '<p>VESC 是 Benjamin Vedder 的开源项目，GNU GPL 3，见 '
        'www.vesc-project.com。本固件没有使用任何 VESC 源码。</p>\n'
        '</body></html>',
    ),
    (
        'Remote CAN source',
        '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" '
        '"http://www.w3.org/TR/REC-html40/strict.dtd">\n'
        '<html><head><meta name="qrichtext" content="1" /><meta charset="utf-8" />'
        '<style type="text/css">\n'
        'p, li { white-space: pre-wrap; }\n'
        'hr { height: 1px; border-width: 0; }\n'
        'li.unchecked::marker { content: "\\2610"; }\n'
        'li.checked::marker { content: "\\2612"; }\n'
        '</style></head><body style=" font-family:\'Segoe UI\'; font-size:9pt; '
        'font-weight:400; font-style:normal;">\n'
        '<p><span style=" text-decoration: underline;">远程 CAN 信号源：</span></p>\n'
        '<p>用于把按键和模拟量通过 CAN 发送给另一台兼容设备，由对方的 CAN 模拟量/'
        '数字量信号源接收。</p>\n'
        '<p><br /></p>\n'
        '<p>两端的 CAN ID 和速率必须一致。在<span style=" text-decoration: '
        'underline;">接收端</span>启用 CAN 模拟量和/或数字量信号源，并填入与本页'
        '相同的 ID 和按键/轴数量。</p>\n'
        '<p><br /></p>\n'
        '<p>如果 CAN 速率较低或总线是共享的，请把更新间隔调大。</p>\n'
        '<p><br /></p>\n'
        '<p>64 个数字按键在一帧 8 字节 CAN 报文里发送。</p>\n'
        '<p>模拟量按 16 位发送，每帧 8 字节放 4 个值，ID 依次递增。<br />'
        '例如 CAN ID 为 110、要发 8 个值：前 4 个轴用 110 帧，后 4 个值用 '
        '111 帧。</p></body></html>',
    ),
    (
        'This controls a MyActuator RMD motor via CAN bus.',
        '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" '
        '"http://www.w3.org/TR/REC-html40/strict.dtd">\n'
        '<html><head><meta name="qrichtext" content="1" /><style type="text/css">\n'
        'p, li { white-space: pre-wrap; }\n'
        '</style></head><body style=" font-family:\'MS Shell Dlg 2\'; '
        'font-size:8.25pt; font-weight:400; font-style:normal;">\n'
        '<p>本页通过 CAN 总线控制 MyActuator RMD 电机。</p>\n'
        '<p>由于该电机驱动的报文速率较低，建议 FFB 更新率不要超过 250Hz。</p>\n'
        '<p><br /></p>\n'
        '<p><span style=" text-decoration: underline;">设置：</span></p>\n'
        '<p>在电机里启用故障状态上报，并设置 CAN ID 与 CAN 速率。</p>\n'
        '<p>推荐 1M 波特率。</p>\n'
        '<p><br /></p>\n'
        '<p><span style=" text-decoration: underline;">主动上报与插值：</span></p>\n'
        '<p>电机可以按固定 10ms 间隔上报位置（关闭主动请求），也可以由 FFBoard '
        '主动请求位置并用低分辨率力矩回复做插值。</p>\n'
        '<p><br /></p>\n'
        '<p><span style=" text-decoration: underline;">关闭主动请求（高分辨率）'
        '：</span></p>\n'
        '<p>电机用主动回复功能每 10ms 发一次位置。</p>\n'
        '<p>开销和抖动更小。<br />推荐用于多电机系统和共享 CAN 总线。</p>\n'
        '<p>无法获取状态信息。主动回复模式会关闭所有回复。</p>\n'
        '<p><br /></p>\n'
        '<p><span style=" text-decoration: underline;">开启主动请求（低分辨率）'
        '：</span></p>\n'
        '<p>FFBoard 主动请求位置，并用 1° 分辨率的力矩状态回复做插值。可以获取'
        '状态信息。</p>\n'
        '<p>时间和位置可能有抖动，总线流量明显增加。</p>\n'
        '<p><br /></p>\n'
        '<p><span style=" text-decoration: underline;">力矩：</span></p>\n'
        '<p>填入电机的最大电流，用来标定可用范围。</p>\n'
        '<p><br /></p>\n'
        '<p><span style=" text-decoration: underline;">CAN 速率：</span></p>\n'
        '<p>推荐 1Mbit/s，且必须与电机内的设置一致。</p></body></html>',
    ),
]

MESSAGE_RE = re.compile(r'<message>(.*?)</message>', re.S)
# The opening tag, however the attributes are ordered. The body is replaced
# wholesale, because an unfinished entry may already carry recycled text.
TRANSLATION_OPEN_RE = re.compile(r'<translation\b[^>]*>')
TRANSLATION_SELF_CLOSING_RE = re.compile(r'<translation\b[^>]*/>')
SOURCE_RE = re.compile(r'<source>(.*?)</source>', re.S)
CONTEXT_RE = re.compile(r'<context>(.*?)</context>', re.S)
NAME_RE = re.compile(r'<name>([^<]*)</name>')
HAS_TEXT_RE = re.compile(r'<translation\b[^>]*>\s*\S')


def escape(text):
    """Escape a translated string the way a .ts file stores it."""
    return (text.replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def translation_for(context, source):
    """The Chinese text for one entry, or None when there is none."""
    specific = CONTEXT_TRANSLATIONS.get((context, source))
    if specific is not None:
        return specific
    exact = TRANSLATIONS.get(source)
    if exact is not None:
        return exact
    for needle, translated in HTML_TRANSLATIONS:
        if needle in source:
            return translated
    return None


def main():
    """Rewrite the unfinished entries of zh_CN.ts."""
    text = io.open(TS_PATH, encoding='utf-8', newline='').read()
    state = {'filled': 0, 'unmatched': []}

    def patch_context(context_match):
        """Fill one <context> block."""
        block = context_match.group(0)
        name_match = NAME_RE.search(block)
        context = name_match.group(1) if name_match else ''

        def patch_message(message_match):
            """Fill one <message> block."""
            body = message_match.group(0)
            source_match = SOURCE_RE.search(body)
            if not source_match:
                return body
            unfinished = 'unfinished' in body
            has_text = bool(HAS_TEXT_RE.search(body))
            if not unfinished and has_text and not FORCE:
                return body

            replacement = translation_for(context, source_match.group(1))
            if replacement is None:
                if not has_text:
                    state['unmatched'].append(
                        (context, source_match.group(1)[:70]))
                return body

            element = '<translation>%s</translation>' % escape(replacement)
            if TRANSLATION_SELF_CLOSING_RE.search(body):
                new_body, count = TRANSLATION_SELF_CLOSING_RE.subn(
                    lambda _m: element, body, count=1)
            else:
                opening = TRANSLATION_OPEN_RE.search(body)
                if opening is None:
                    return body
                start = opening.start()
                end_marker = body.find('</translation>', opening.end())
                if end_marker == -1:
                    return body
                new_body = body[:start] + element + body[end_marker + len('</translation>'):]
                count = 1
            if count:
                state['filled'] += 1
            return new_body

        return MESSAGE_RE.sub(patch_message, block)

    text = CONTEXT_RE.sub(patch_context, text)
    io.open(TS_PATH, 'w', encoding='utf-8', newline='\n').write(text)

    print('filled %d translation(s) in %s' % (state['filled'], TS_PATH))
    if state['unmatched']:
        print('still without a translation (%d):' % len(state['unmatched']))
        for context, source in state['unmatched']:
            print('  [%s] %r' % (context, source))
    return 0


if __name__ == '__main__':
    sys.exit(main())
