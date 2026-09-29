"""Report which strings in a .ts file still need translating."""

import io
import re
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else 'translations/zh_CN.ts'

text = io.open(PATH, encoding='utf-8').read()

total = 0
unfinished = 0
for context in re.findall(r'<context>(.*?)</context>', text, re.S):
    name = re.search(r'<name>([^<]*)</name>', context).group(1)
    rows = []
    for block in re.findall(r'<message>(.*?)</message>', context, re.S):
        src_match = re.search(r'<source>(.*?)</source>', block, re.S)
        if not src_match:
            continue
        total += 1
        tr_match = re.search(r'<translation([^>]*)>(.*?)</translation>',
                             block, re.S)
        if tr_match is None:
            rows.append(src_match.group(1))
            continue
        if 'unfinished' in tr_match.group(1) or not tr_match.group(2).strip():
            unfinished += 1
            rows.append(src_match.group(1))
    if rows:
        print('### %s (%d)' % (name, len(rows)))
        for row in rows:
            print('    ' + row[:120].replace('\n', ' '))

print()
print('TOTAL %d messages, %d untranslated' % (total, unfinished))
