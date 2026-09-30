#!/usr/bin/env python3
"""Emit a standalone C guest fixture using the checked-in A64 encodings.

Compile the output as a static AArch64 Linux executable, then run it natively,
under QEMU, or inside ios-linuxkit. It prints a failure count and exits nonzero
on any wrong lane, multi-register transfer or base-register writeback.
"""
import argparse
from pathlib import Path

from test_arm64_halfword_lanes import fixtures


def build_source():
    blocks = []
    for word, op, regs, lane, suffix in fixtures():
        count = len(regs)
        assembly = ['mov x5, %[ptr]', f'mov x7, #{2 * count}']
        for index, reg in enumerate(regs):
            assembly.append(f'movi v{reg}.16b, #0xa5' if op == 'ld' else
                            f'ldr q{reg}, [%[vec], #{16 * index}]')
        assembly.append(f'.inst 0x{word}')
        if op == 'ld':
            assembly.extend(f'str q{reg}, [%[vec], #{16 * index}]'
                            for index, reg in enumerate(regs))
        assembly.append('mov %[wb], x5')
        clobbers = ', '.join(f'"v{reg}"' for reg in regs)
        writeback = 2 * count if suffix else 0
        checks = f'if (wb != (uintptr_t)mem + {writeback}) bad = 1;\n'
        if op == 'ld':
            checks += (f'for (int r = 0; r < {count}; r++) for (int l = 0; l < 8; l++)\n'
                       f'    if (vec[r][l] != (l == {lane} ? 0x1200 + r : 0xa5a5)) bad = 1;')
        else:
            checks += (f'for (int r = 0; r < {count}; r++) if (mem[r] != vec[r][{lane}]) bad = 1;\n'
                       f'for (int r = {count}; r < 8; r++) if (mem[r] != 0xeeee) bad = 1;')
        blocks.append('''{
    uint16_t mem[8], vec[4][8]; uintptr_t wb;
    for (int r = 0; r < 8; r++) mem[r] = OP_LOAD ? 0x1200 + r : 0xeeee;
    for (int r = 0; r < 4; r++) for (int l = 0; l < 8; l++) vec[r][l] = 0x100 * r + l + 1;
    __asm__ volatile("ASSEMBLY" : [wb] "=r"(wb) : [ptr] "r"(mem), [vec] "r"(vec)
                     : "x5", "x7", CLOBBERS, "memory");
    int bad = 0;
    CHECKS
    if (bad) failures++;
}
'''.replace('OP_LOAD', '1' if op == 'ld' else '0')
           .replace('ASSEMBLY', '\\n'.join(assembly))
           .replace('CLOBBERS', clobbers).replace('CHECKS', checks))
    return ('#include <stdint.h>\n#include <stdio.h>\nint main(void) { int failures = 0;\n'
            + ''.join(blocks) + f'printf("cases={len(blocks)} failures=%d\\n", failures);\n'
            'return failures ? 1 : 0; }\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(build_source())
