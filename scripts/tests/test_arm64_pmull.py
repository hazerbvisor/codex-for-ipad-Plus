"""Exercise the complete pinned/staged crypto helper with overlapping registers."""
import ctypes
import importlib.util
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('staging', PROJECT / 'scripts/prepare-arm64-ios.py')
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)
HELPER = Path('asbestos/guest-arm64/crypto_helpers.c')


def multiply(a, b):
    # Independent GF(2) polynomial oracle: sum monomials of both operands.
    result = 0
    for i in range(a.bit_length()):
        for j in range(b.bit_length()):
            if a & (1 << i) and b & (1 << j):
                result ^= 1 << (i + j)
    return result


class PMULLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (staging.FORK / HELPER).read_text()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        staged = root / 'staged'
        staging.stage(staged)
        cls.functions = []
        for tree, name in [(staging.FORK, 'before'), (staged, 'after')]:
            library = root / (name + '.so')
            subprocess.run(['cc', '-shared', '-fPIC', '-O2', str(tree / HELPER),
                            '-o', str(library)], check=True)
            function = ctypes.CDLL(str(library)).pmull_helper
            function.argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_uint32] * 2
            cls.functions.append(function)
        cls.staged_source = (staged / HELPER).read_text()

    def test_all_widths_halves_and_aliases(self):
        rng = random.Random(4500)
        failures = 0
        for size in (0, 3):
            for upper in (0, 1):
                for dest, rn, rm in [(2, 0, 1), (0, 0, 1), (1, 0, 1), (0, 0, 0)]:
                    for _ in range(64):
                        values = bytes(rng.randrange(256) for _ in range(48))
                        offset = upper * 8
                        n = values[rn * 16 + offset:rn * 16 + offset + 8]
                        m = values[rm * 16 + offset:rm * 16 + offset + 8]
                        if size == 0:
                            expected = b''.join(multiply(a, b).to_bytes(2, 'little') for a, b in zip(n, m))
                        else:
                            expected = multiply(int.from_bytes(n, 'little'),
                                                int.from_bytes(m, 'little')).to_bytes(16, 'little')
                        for index, function in enumerate(self.functions):
                            registers = (ctypes.c_ubyte * 48).from_buffer_copy(values)
                            address = ctypes.addressof(registers)
                            function(address + dest * 16, address + rn * 16,
                                     address + rm * 16, upper, size)
                            actual = bytes(registers)[dest * 16:dest * 16 + 16]
                            if index == 0:
                                failures += actual != expected
                                if size == 3 or dest == 2:
                                    self.assertEqual(actual, expected)
                            else:
                                self.assertEqual(actual, expected, (size, upper, dest, rn, rm))
                                # No source register outside Vd may be changed.
                                for reg in range(3):
                                    if reg != dest:
                                        self.assertEqual(bytes(registers)[reg*16:reg*16+16],
                                                         values[reg*16:reg*16+16])
        # Lower-half byte PMULL fails for all three alias patterns. Upper-half
        # reads happen to precede the overlapping writes, but must stay correct.
        self.assertEqual(failures, 192)

    def test_staging_and_drift_guards(self):
        self.assertEqual((staging.FORK / HELPER).read_text(), self.original)
        self.assertEqual(self.staged_source, staging.repair_pmull_aliasing(self.original))
        for changed in ('', self.staged_source,
                        self.original.replace('            dst[i] = result;', '', 1)):
            with self.assertRaises(ValueError):
                staging.repair_pmull_aliasing(changed)


if __name__ == '__main__':
    unittest.main()
