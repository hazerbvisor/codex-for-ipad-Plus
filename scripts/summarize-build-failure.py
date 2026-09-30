#!/usr/bin/env python3
"""Print failing commands and compiler errors from a full build log."""
import argparse
from pathlib import Path
import re


def summarize(content: str) -> str:
    lines = content.splitlines()
    selected = set()
    for index, line in enumerate(lines):
        if line.startswith('FAILED:'):
            selected.update(range(index, min(index + 2, len(lines))))
        if re.search(r'(?:^|\s)(?:fatal )?error:', line, re.I) or re.search(r'^ld: ', line):
            selected.update(range(max(0, index - 2), min(index + 4, len(lines))))
        if line.startswith(('ninja: error:', 'meson.build:')) and 'ERROR' in line.upper():
            selected.add(index)
    if not selected:
        return ('No compiler error or FAILED command found in the captured log.\n'
                'Read the complete log; the failure may be an interrupted process or build tool.\n'
                + '\n'.join(lines[-30:]))
    output = ['Build failure diagnostics (full log retained):']
    previous = -2
    for index in sorted(selected)[:150]:
        if index > previous + 1:
            output.append('...')
        output.append(lines[index][:4000])
        previous = index
    return '\n'.join(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    args = parser.parse_args()
    print(summarize(args.log.read_text(errors='replace')))
