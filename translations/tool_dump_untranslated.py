"""Dump the still-untranslated messages of a .ts file, verbatim, as JSON.

Run:  python translations/tool_dump_untranslated.py [ts] [out.json]
"""

import io
import json
import re
import sys

TS_PATH = sys.argv[1] if len(sys.argv) > 1 else 'translations/zh_CN.ts'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'translations/_untranslated.json'

text = io.open(TS_PATH, encoding='utf-8').read()
rows = []

for context in re.findall(r'<context>(.*?)</context>', text, re.S):
    name = re.search(r'<name>([^<]*)</name>', context).group(1)
    for block in re.findall(r'<message>(.*?)</message>', context, re.S):
        src = re.search(r'<source>(.*?)</source>', block, re.S)
        tr = re.search(r'<translation([^>]*)/?>', block, re.S)
        if not src or not tr:
            continue
        if 'unfinished' in (tr.group(1) or ''):
            rows.append({'context': name, 'source': src.group(1)})

io.open(OUT, 'w', encoding='utf-8', newline='\n').write(
    json.dumps(rows, ensure_ascii=False, indent=2))
print('wrote %d untranslated message(s) to %s' % (len(rows), OUT))
