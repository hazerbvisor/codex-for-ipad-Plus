"""Run the full pinned/staged decoder on assembler-produced A64 instructions.

Initialize upstream/ios-linuxkit (including submodules) first. The C harness
only emits gadget addresses/operands. It does not execute ARM64 gadgets or TLS.
"""
import importlib.util
import itertools
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('staging', PROJECT / 'scripts/prepare-arm64-ios.py')
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)
DECODER = Path('asbestos/guest-arm64/gen.c')


def fixtures():
    result = []
    for line in (HERE / 'arm64-halfword-encodings.txt').read_text().splitlines():
        if line.startswith('#') or not line:
            continue
        word, mnemonic = line.split(' | ')
        match = re.fullmatch(r'(ld|st)([1-4]) \{([^}]+)\}\[([0-7])\], \[x5](.*)', mnemonic)
        if match is None:
            raise ValueError(mnemonic)
        op, count, registers, lane, suffix = match.groups()
        regs = [int(reg.strip()[1:-2]) for reg in registers.split(',')]
        assert len(regs) == int(count)
        result.append((word, op, regs, int(lane), suffix))
    return result


def compile_decoder(tree, output):
    source = tree / DECODER
    names = sorted(set(re.findall(r'extern void (gadget_\w+)\(void\);', source.read_text())))
    # Address-only gadget symbols must never be called by this harness.
    (output.parent / 'gadget-stubs.inc').write_text('\n'.join(
        f'void {name}(void) {{ abort(); }}' for name in names))
    subprocess.run([shutil.which('cc') or 'cc', '-std=gnu11', '-DGUEST_ARM64=1',
                    f'-DDECODER_SOURCE="{source}"', '-I' + str(tree),
                    '-I' + str(output.parent), str(HERE / 'arm64-lanes-harness.c'),
                    '-o', str(output)], check=True)


def decode(executable, words):
    result = subprocess.run([str(executable), *words], check=True, text=True, capture_output=True)
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split()
        rows.append([(tag, int(value, 16)) for tag, value in
                     (field.split(':') for field in fields[1:])])
    assert len(rows) == len(words)
    return rows


def expected(fixture):
    word, op, regs, lane, suffix = fixture
    result = [('pc', 0), ('addr', 5)]
    for i, reg in enumerate(regs):
        if i:
            result.append(('add', 2))
        result.append(('load' if op == 'ld' else 'store', reg | ((2 * lane) << 8)))
    if suffix == ', x7':
        result.append(('update_reg', 5 | (7 << 8)))
    elif suffix:
        assert suffix == f', #{2 * len(regs)}'
        result.append(('update', 5 | ((2 * len(regs)) << 8)))
    return result


class HalfwordLaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (staging.FORK / DECODER).is_file():
            raise RuntimeError('Initialize the pinned ios-linuxkit submodule; actual decoder tests cannot be skipped')
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        cls.original_source = (staging.FORK / DECODER).read_bytes()
        cls.staged = root / 'staged'
        staging.stage(cls.staged)
        cls.original = root / 'original'
        cls.repaired = root / 'repaired'
        compile_decoder(staging.FORK, cls.original)
        compile_decoder(cls.staged, cls.repaired)
        cls.cases = fixtures()
        cls.before = decode(cls.original, [f[0] for f in cls.cases])
        cls.after = decode(cls.repaired, [f[0] for f in cls.cases])

    def test_original_decoder_reproduces_odd_lane_aliasing(self):
        failures = 0
        for case, row in zip(self.cases, self.before):
            word, op, regs, lane, suffix = case
            aliased = (word, op, regs, lane & ~1, suffix)
            with self.subTest(word=word):
                self.assertEqual(row, expected(aliased))
            failures += row != expected(case)
        self.assertEqual(failures, 96)  # 4 regs x 2 directions x 3 forms x 4 odd lanes

    def test_staged_decoder_preserves_all_lanes_and_writeback(self):
        self.assertEqual(len(self.cases), 192)
        self.assertEqual(len({case[0] for case in self.cases}), 192)
        coverage = {(op, len(regs), lane, 'base' if not suffix else
                     'register' if suffix == ', x7' else 'immediate')
                    for word, op, regs, lane, suffix in self.cases}
        self.assertEqual(coverage, set(itertools.product(
            ('ld', 'st'), range(1, 5), range(8), ('base', 'register', 'immediate'))))
        for case, row in zip(self.cases, self.after):
            with self.subTest(word=case[0]):
                self.assertEqual(row, expected(case))

    def test_staging_keeps_the_pinned_submodule_unchanged(self):
        self.assertEqual((staging.FORK / DECODER).read_bytes(), self.original_source)
        self.assertEqual((self.staged / DECODER).read_text(),
                         staging.repair_halfword_lanes(self.original_source.decode()))
        # Meson compiles the repaired file into the runtime used by Xcode.
        self.assertIn("'asbestos/guest-arm64/gen.c'", (self.staged / 'meson.build').read_text())
        self.assertIn('libish_emu.a', (self.staged / 'app/codexpad-build-runtime.sh').read_text())

    def test_p521_provider_copy_overwrite(self):
        # First eight rustls 0.23.36 aws-lc mapping schemes in source order,
        # represented here by their TLS wire IDs. Replay the actual binary's
        # SIMD copy operands; model the halfword writes/ST2 interleaving only.
        schemes = [0x0503, 0x0403, 0x0603, 0x0807, 0x0806, 0x0805, 0x0804, 0x0601]
        instructions = [line.split(' | ') for line in
                        (HERE / 'codex-provider-copy.txt').read_text().splitlines()
                        if line and not line.startswith('#')]
        def copy(executable):
            rows = decode(executable, [fields[0] for fields in instructions])
            vectors = [[0] * 8, [0] * 8]
            for fields, row in zip(instructions[:-1], rows[:-1]):
                reg, lane = int(fields[1]), int(fields[2])
                tag, operand = next((tag, value) for tag, value in row
                                    if tag in ('load', 'scalarload'))
                self.assertEqual(operand & 31, reg)
                if tag == 'scalarload':
                    self.assertEqual(lane, 0)
                    vectors[reg] = [0] * 8
                    offset = 0
                else:
                    offset = (operand >> 8) // 2
                vectors[reg][offset] = (schemes[lane] >> (reg * 16)) & 0xffff
            # ST2 {v0.8h, v1.8h} uses two registers, halfwords, Q=1.
            self.assertEqual(rows[-1], [('pc', 0), ('addr', 12),
                                       ('interleave', 0 | (2 << 8) | (1 << 16) | (1 << 24))])
            return [low | (high << 16) for low, high in zip(*vectors)]
        corrupted = copy(self.original)
        self.assertEqual(corrupted, [0x0403, 0, 0x0807, 0, 0x0805, 0, 0x0601, 0])
        self.assertNotIn(0x0603, corrupted)
        self.assertEqual(copy(self.repaired), schemes)

    def test_repair_rejects_source_drift(self):
        source = self.original_source.decode()
        old = 'lane = (Q << 2) | (S << 1) | (size & 1);'
        for changed in ('', source.replace(old, '', 1), source + old,
                        staging.repair_halfword_lanes(source)):
            with self.subTest(occurrences=changed.count(old)):
                with self.assertRaisesRegex(ValueError, 'Expected two ARM64 halfword lane decoders'):
                    staging.repair_halfword_lanes(changed)


if __name__ == '__main__':
    unittest.main()
