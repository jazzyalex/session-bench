#!/usr/bin/env python3
"""Assemble the post from front matter, generated data and the body, then build the site."""
import subprocess, sys, pathlib
P = pathlib.Path('/private/tmp/claude-501/post')
front = (P / 'front.yml').read_text()
data = subprocess.run([sys.executable, str(P / 'gen_data.py')], capture_output=True, text=True, check=True).stdout
body = (P / 'body.md').read_text()
figs = (P / 'figures.html').read_text()
style, rest = figs.split('</style>', 1)
blocks = {}
for block in rest.split('<figure')[1:]:
    block = '<figure' + block.rstrip()
    name = block.split('id="', 1)[1].split('"', 1)[0]
    blocks[name] = block
body = body.replace('{{STYLE}}', style.strip() + '\n</style>')
for name, block in blocks.items():
    body = body.replace('{{FIG:%s}}' % name, block)
assert '{{FIG:' not in body and '{{STYLE}}' not in body
out = pathlib.Path('/Users/alexm/Repository/Codex-History/docs/_posts/2026-10-08-session-bench-v1.md')
out.write_text('---\n' + front + data + '---\n\n' + body)
print('wrote', out, len(out.read_text()))
