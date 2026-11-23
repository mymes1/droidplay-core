"""dm/android/spv_rne.h (a fragment shader's output rounded to an 8-bit UNORM target as Maxwell rounds it: a value exactly halfway between two codes goes to
the even one, where the Adreno stores the odd one, so Hedgehog Engine 2's G-buffer flag bytes come out as the Switch's), built for the PC with its C compiler and
run on a module assembled here word by word: the masked output's stores are rounded and the unmasked one's are not, the module still parses with every id under
its bound, a module of another stage or with no masked output is left as it is, and an output that is read or copied rather than stored keeps the GPU's
conversion. The arithmetic is checked against the conversion itself, over every code and over the halfway values the engine writes. Skipped without a C compiler.
With DM_SPV_DIR=<a game's packed spv folder>, also run over every module there: each rewritten one must still parse, and spirv-val checks it where the NDK has
the tool."""
import ctypes
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
MAGIC = 0x07230203
DONE, KEPT = 1, 2
F255, INV255 = 0x437f0000, 0x3b808081   # 255.0f, 1 / 255 as float
OP_ENTRY, OP_EXECMODE, OP_TYPE_FLOAT, OP_TYPE_VECTOR, OP_TYPE_POINTER, OP_CONSTANT, OP_CONST_COMPOSITE = 15, 16, 22, 23, 32, 43, 44
OP_VARIABLE, OP_STORE, OP_FUNCTION, OP_FUNCTION_END, OP_RETURN, OP_LABEL, OP_COPYMEMORY = 59, 62, 54, 56, 253, 248, 63
OP_VEC_TIMES_SCALAR, OP_ROUND_EVEN, OP_FMUL = 142, 46, 133
OP_DECORATE, DEC_LOCATION = 71, 30


def compiler():
    if sys.platform == 'darwin': return ['xcrun', 'clang']
    for c in ('clang', 'cc', 'gcc'):
        if shutil.which(c): return [c]
    return None


def ins(op, *a):
    return [((len(a) + 1) << 16) | op, *a]


def module(stage=4, outs=2, int_out=False, copy=False, scalar=False):
    """A fragment (stage 4) module with `outs` float vec4 Output variables at locations 0..outs-1 (an integer one instead when int_out), each stored once from a
    constant, or copied into when copy. Its ids: 1 void, 2 fn, 3 f32, 4 vec4, 5 pointer, 6 zero, 7 one-constant, 8 composite, 9 main, 10 label, 11.. the outputs
    (variable), then later ids."""
    w = [MAGIC, 0x00010000, 0, 0, 0]
    vars_ = [11 + i for i in range(outs)]
    iface = '' .join(' %d' % v for v in vars_)
    w += ins(OP_ENTRY, stage, 9, 0x6e69616d, 0, *vars_)   # OpEntryPoint <stage> %main "main" %out...
    w += ins(OP_EXECMODE, 9, 7) if stage == 4 else []   # OriginUpperLeft
    for i, v in enumerate(vars_): w += ins(OP_DECORATE, v, DEC_LOCATION, i)
    w += ins(19, 1)            # OpTypeVoid %1
    w += ins(33, 2, 1)         # OpTypeFunction %2 %1
    w += ins(OP_TYPE_FLOAT, 3, 32)
    w += ins(OP_TYPE_VECTOR, 4, 3, 4)
    if int_out: w += ins(21, 12, 32, 0)   # OpTypeInt %12 32 signedness 0
    w += ins(OP_TYPE_POINTER, 5, 3, 12 if int_out else 3 if scalar else 4)   # Output pointer to it
    w += ins(OP_CONSTANT, 3, 6, 0)         # float 0
    w += ins(OP_CONSTANT, 3, 7, 0x3f800000)   # float 1
    if int_out: w += ins(OP_CONSTANT, 12, 8, 7)
    elif scalar: w += ins(OP_CONSTANT, 3, 8, 0x3f800000)   # the value is a float itself
    else: w += ins(OP_CONST_COMPOSITE, 4, 8, 7, 7, 7, 7)
    for v in vars_: w += ins(OP_VARIABLE, 5, v, 3)
    nb = 11 + outs
    w += ins(OP_FUNCTION, 1, 9, 0, 2)
    w += ins(OP_LABEL, 10)
    w += ins(OP_COPYMEMORY, vars_[0], nb, 0, 0, 4) if copy else []
    for v in vars_: w += ins(OP_STORE, v, 8)
    w += ins(OP_RETURN) + ins(OP_FUNCTION_END)
    w[3] = nb + 1
    return w


# where each opcode of this test's modules keeps its result id (its instruction's operands), and how many operands are ids
RESULT = {19: 1, 21: 1, 22: 1, 23: 1, 32: 1, 33: 1, 43: 2, 44: 2, 59: 2, 54: 2, 248: 1, 46: 2, 133: 2, 142: 2}


def bound_ok(w):
    """0, or why the words are not a module whose every result id is under its bound and defined once"""
    if len(w) < 5 or w[0] != MAGIC: return 'not SPIR-V'
    b, i, ids = w[3], 5, set()
    while i < len(w):
        n, op = w[i] >> 16, w[i] & 0xffff
        if not n or i + n > len(w): return 'bad instruction length at %d' % i
        if op in RESULT:
            r = w[i + RESULT[op]]
            if r in ids: return 'id %d defined twice' % r
            ids.add(r)
            if r >= b: return 'id %d at or past the bound %d' % (r, b)
        i += n
    if i != len(w): return 'instructions run past the end'
    return None


def stores(w):
    """[(at, pointer id, value id)] of the module's OpStores"""
    out, i = [], 5
    while i < len(w):
        n, op = w[i] >> 16, w[i] & 0xffff
        if op == OP_STORE: out.append((i, w[i + 1], w[i + 2]))
        if not n or i + n > len(w): break
        i += n
    return out


def instructions(w):
    """[(opcode, words)] of the module"""
    out, i = [], 5
    while i < len(w) and w[i] >> 16:
        n = w[i] >> 16
        out.append((w[i] & 0xffff, w[i:i + n]))
        i += n
    return out


def before(w, at, count):
    """The instructions ending `count` instructions before the word at `at` (walking back over lengths)"""
    heads = []
    i = 5
    while i < len(w):
        n = w[i] >> 16
        heads.append((i, n))
        i += n
    k = [q for q, (i, n) in enumerate(heads) if i == at][0]
    out = []
    for i, n in heads[k - count:k]: out.append((w[i] & 0xffff, w[i:i + n]))
    return out


@unittest.skipIf(compiler() is None or os.name == 'nt', 'no C compiler')
class SpvRne(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        src = cls.tmp / 't.c'
        src.write_text('#include <stdlib.h>\n#include "spv_rne.h"\n'
                       '#define X __attribute__((visibility("default")))\n'
                       'X size_t run(const uint32_t *w, size_t n, uint32_t mask, uint32_t *o, size_t cap, size_t *need, int *what) {\n'
                       '    uint32_t *sc = malloc(3 * (size_t)w[3] * 4); size_t k = spv_rne(w, n, mask, sc, o, cap, need, what); free(sc); return k; }\n')
        subprocess.run([*compiler(), '-shared', '-fPIC', '-O1', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-I', REPO / 'dm/android', src, '-o', cls.tmp / 't.so'], check=True)
        cls.lib = ctypes.CDLL(str(cls.tmp / 't.so'))
        cls.lib.run.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t,
                                ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_int)]
        cls.lib.run.restype = ctypes.c_size_t

    def run_on(self, w, mask):
        arr = (ctypes.c_uint32 * len(w))(*w); need = ctypes.c_size_t(); what = ctypes.c_int()
        n = self.lib.run(arr, len(w), mask, None, 0, ctypes.byref(need), ctypes.byref(what))
        self.assertEqual(n, 0)   # a sizing call makes nothing
        if not need.value: return None, what.value
        out = (ctypes.c_uint32 * need.value)()
        n = self.lib.run(arr, len(w), mask, out, need.value, ctypes.byref(need), ctypes.byref(what))
        self.assertGreater(n, 0)
        self.assertEqual(bound_ok(list(out[:n])), None)
        return list(out[:n]), what.value

    def test_masked_output_rounded_others_left(self):
        w = module()
        out, what = self.run_on(w, 1)   # location 0 only
        self.assertEqual(what, DONE)
        self.assertIsNotNone(out)
        self.assertEqual(out[3], w[3] + 2 + 3)   # two constants and one store's results
        self.assertEqual(len(out), len(w) + 8 + 14)
        st = stores(out)
        self.assertEqual(len(st), 2)
        seq = before(out, st[0][0], 3)   # the masked store: times 255, round even, times 1/255
        self.assertEqual([op for op, _ in seq], [OP_VEC_TIMES_SCALAR, OP_ROUND_EVEN, OP_VEC_TIMES_SCALAR])
        mul1, rou, mul2 = (x for _, x in seq)
        consts = {x[2]: x[3] for op, x in instructions(out) if op == OP_CONSTANT and x[1] == 3}   # result id -> value   # float ids -> their value
        self.assertEqual(consts.get(mul1[4]), F255)   # v * 255
        self.assertEqual(consts.get(mul2[4]), INV255)   # ... * 1/255
        self.assertEqual(mul1[1], 4)   # a vec4: the value type the output has
        self.assertEqual(rou[3], mul1[2])   # roundEven(v * 255)
        self.assertEqual(mul2[3], rou[2])   # * 1/255
        self.assertEqual(mul2[2], st[0][2])   # the stored value is the rounded one
        self.assertNotEqual([op for op, _ in before(out, st[1][0], 3)], [OP_VEC_TIMES_SCALAR, OP_ROUND_EVEN, OP_VEC_TIMES_SCALAR])   # location 1: untouched

    def test_both_masked(self):
        out, what = self.run_on(module(), 3)
        self.assertEqual(what, DONE)
        self.assertEqual(out[3], module()[3] + 2 + 6)
        for at, _, _ in stores(out):
            self.assertEqual([op for op, _ in before(out, at, 3)], [OP_VEC_TIMES_SCALAR, OP_ROUND_EVEN, OP_VEC_TIMES_SCALAR])

    def test_a_scalar_output_is_a_float_multiply(self):
        w = module(outs=1, scalar=True)
        out, what = self.run_on(w, 1)
        self.assertEqual(what, DONE)
        self.assertEqual(out[3], w[3] + 2 + 3)
        (at, _, _), = stores(out)
        seq = before(out, at, 3)
        self.assertEqual([op for op, _ in seq], [OP_FMUL, OP_ROUND_EVEN, OP_FMUL])   # R8_UNORM: a float, not a vector
        self.assertEqual({x[1] for _, x in seq}, {3})   # the float type

    def test_left_as_it_is(self):
        self.assertEqual(self.run_on(module(), 0), (None, 0))            # no location masked
        self.assertEqual(self.run_on(module(), 4), (None, 0))            # a location the shader does not write
        self.assertEqual(self.run_on(module(stage=0), 1), (None, 0))     # a vertex shader
        self.assertEqual(self.run_on(module(int_out=True), 1), (None, 0))   # an integer output is no normalized colour target: nothing to round

    def test_copied_output_kept(self):
        self.assertEqual(self.run_on(module(copy=True), 1), (None, KEPT))

    def test_the_arithmetic(self):
        """The rewrite's own sum: roundEven(v * 255) / 255 converts to the code roundEven(v * 255) gives whatever the GPU's tie rule, and the halfway values the
        engine writes land on the even code (k) where the Adreno's rule alone would land on k + 1."""
        f32 = lambda x: struct.unpack('<f', struct.pack('<f', x))[0]
        inv = f32(1 / 255)
        def rne(x):   # round half to even, as OpRoundEven does
            r, lo, d = f32(x), math.floor(f32(x)), f32(x) - math.floor(f32(x))
            return lo if d < 0.5 or (d == 0.5 and lo % 2 == 0) else lo + 1
        def code(v, up):   # a GPU's conversion to the nearest code: a tie away from zero (Adreno), or to the even one (Maxwell)
            y = f32(f32(v) * 255.0)
            return math.floor(y + 0.5) if up else rne(y)
        for up in (0, 1):
            for c in range(256):
                v = f32(c / 255.0)   # the value a code c is written as, and the rewrite's value stored back
                self.assertEqual(code(f32(f32(rne(f32(v * 255.0))) * inv), up), c, (c, up))
        for k in range(0, 255, 2):   # (flags + 0.5) / 255: flags even, as Hedgehog Engine 2 writes them
            v = f32((k + 0.5) / 255.0)
            self.assertEqual(code(v, 1), k + 1)   # the Adreno: the odd code, bit 0 set
            self.assertEqual(code(v, 0), k)       # Maxwell, and the rewrite's value
            self.assertEqual(code(f32(f32(rne(f32(v * 255.0))) * inv), 1), k)

    def test_a_games_shaders(self):
        d = os.environ.get('DM_SPV_DIR')
        if not d: self.skipTest('no DM_SPV_DIR')
        ndk = os.environ.get('ANDROID_NDK_HOME') or os.environ.get('ANDROID_NDK')
        tools = Path(ndk) / 'shader-tools' if ndk else None
        val = next(iter(sorted(tools.glob('*/spirv-val'))) , None) if tools and tools.is_dir() else None
        seen = rewritten = 0
        for p in sorted(Path(d).glob('*.spv')):
            w = list(struct.unpack('<%dI' % (p.stat().st_size // 4), p.read_bytes()))
            if len(w) < 5 or w[0] != MAGIC: continue
            seen += 1
            out, what = self.run_on(w, 3)
            if not out: continue
            rewritten += 1
            if val:
                q = self.tmp / 'v.spv'; q.write_bytes(struct.pack('<%dI' % len(out), *out))
                r = subprocess.run([str(val), '--target-env', 'vulkan1.1', str(q)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, '%s: %s' % (p.name, r.stdout + r.stderr))
        self.assertGreater(seen, 0, d)
        sys.stderr.write('\nspv_rne over %s: %d modules, %d rewritten\n' % (d, seen, rewritten))


if __name__ == '__main__':
    unittest.main()
