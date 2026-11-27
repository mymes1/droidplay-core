"""dm/gfx/tools/shader_dump_pack.py (the shaders an Android replay saved because the game's shader pack has no module for them: <key>.ctl and <key>.code pairs from
DM_SPV_MISSING, packed into the package dm/android/tools/spv_build.py translates). Read here the way spv_build.py reads a package: the records split into a code area and
a control section, each key the host's, and each file named for its stage. A pair whose bytes are not its key's is refused (another profile, or two shaders' halves), a
compute shader's thread group reaches the facts table, and an NVN2 control section, of which the device keeps only the key's 0x48 bytes, is reported as one that has to
come from the game's files."""
import importlib
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'dm/gfx/tools'))
had = os.environ.get('DM_GAME'); os.environ['DM_GAME'] = '01001F5010DFA000'   # shader_split loads a profile at import (no shader_key_skip there: the keys are plain)
import shader_split as ss   # noqa: E402
if had is None: os.environ.pop('DM_GAME', None)
else: os.environ['DM_GAME'] = had
sdp = importlib.import_module('shader_dump_pack')


def records(d):   # spv_build.py records()
    r = [m.start() for m in __import__('re').finditer(b'BTCDBTCD', d)]
    return [d[x:(r[k + 1] if k + 1 < len(r) else len(d))] for k, x in enumerate(r)]


def split(rec):   # spv_build.py split()
    nctl, ncode = struct.unpack_from('<2I', rec, 0x28)
    return rec[0x38:0x38 + ncode], rec[0x38 + ncode:0x38 + ncode + nctl]


def shader(stage, nctl=0x880, code_len=0x300, group=(8, 4, 2), shared=0x400):
    """A shader as the device saves it: a control section's key fields and its code area"""
    if stage == 'cs':
        code = ss.CS_MAGIC + bytes(code_len - 4)
    else:
        code = bytearray(ss.GFX_MAGIC + bytes(code_len - 4)); struct.pack_into('<I', code, 0x30, 5 << 10); code = bytes(code)
    ctl = bytearray(nctl)
    ctl[0:4] = ss.CTL_MAGIC; struct.pack_into('<I', ctl, 0x20, nctl); struct.pack_into('<I', ctl, 0x704, len(code))
    struct.pack_into('<3I', ctl, 0x748, *group); struct.pack_into('<I', ctl, 0x754, shared)
    return bytes(ctl), code


class Pack(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dump, self.pkg = self.tmp / 'dump', self.tmp / 'pkg'
        self.dump.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, ctl, code, key=None):
        key = key or ss.skey(ctl, code)
        (self.dump / f'{key}.ctl').write_bytes(ctl); (self.dump / f'{key}.code').write_bytes(code)
        return key

    def pack(self):
        av = sys.argv; sys.argv = ['shader_dump_pack', str(self.dump), str(self.pkg)]
        try: return sdp.main()
        finally: sys.argv = av

    def test_a_compute_shader_and_a_pixel_shader_pack_for_their_stage(self):
        kc, kp = self.save(*shader('cs')), self.save(*shader('ps'))
        self.assertEqual(self.pack(), 0)
        got = {}
        for f in sorted(self.pkg.glob('*.bin')):
            stage = f.name[:2]
            for rec in records(f.read_bytes()):
                code, ctl = split(rec)
                key = ss.skey(ctl, code)
                self.assertEqual(rec[8:16], bytes.fromhex(key), 'the record carries its own key')
                self.assertEqual(stage, ss.glslc_stage(ctl, code), 'the file is named for the stage the control section and code area give')
                got[key] = stage
        self.assertEqual(got, {kc: 'cs', kp: 'ps'})
        facts = dict(ln.rstrip('\n').split('\t') for ln in open(self.pkg / 'shader_facts.tsv', encoding='utf-8') if not ln.startswith('#'))
        self.assertEqual(facts[kc], '{"local":[8,4,2],"shared":1024}')   # 0x400 rounded up to 16
        self.assertNotIn(kp, facts)   # no thread group for a pixel shader

    def test_bytes_that_are_not_their_keys_are_refused(self):
        ctl, code = shader('cs')
        self.save(ctl, code, key='0123456789abcdef')   # the pair is right but under another key: a profile that is not the pack's, or two shaders' halves
        self.assertEqual(self.pack(), 1)
        self.assertFalse(list(self.pkg.glob('*.bin')))

    def test_an_nvn2_control_section_of_key_length_is_reported_not_packed(self):
        ctl, code = shader('cs'); ctl = bytearray(ctl[:0x48]); ctl[0:4] = ss.CTL_MAGIC; ctl[4:8] = b'\xc9\0\0\0'
        struct.pack_into('<I', ctl, 0x44, len(code))
        self.save(bytes(ctl), code)
        self.assertEqual(self.pack(), 1)
        self.assertFalse(list(self.pkg.glob('*.bin')))

    def test_an_empty_dump_says_where_the_pairs_come_from(self):
        self.assertEqual(self.pack(), 1)


if __name__ == '__main__':
    unittest.main()
