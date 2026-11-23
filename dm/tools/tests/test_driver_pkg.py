"""dm/android/driver_pkg.h (a driver package: the .so its folder holds, what its meta.json calls it, and the label its records go by), built for the PC with the
system's C compiler and run over folders made here: one .so; several with the meta.json's "library" naming one; several with only one libvulkan*.so; none at all;
a folder that is not there; a directory named like one; a link to one. And the meta.json reader: AdrenoTools' and Winlator's fields, an escaped quote, a field
name that only appears inside a value, a key without a colon. Skipped without a C compiler."""
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]

SHIM = r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "driver_pkg.h"
int field(const char *js, const char *key, char *out, size_t cap) { return dpk_field(js, strlen(js), key, out, cap); }
int scan(const char *dir, char *out, size_t cap) { return dpk_scan(dir, out, cap); }
void label(const char *p, char *out, size_t cap) { dpk_label(p, out, cap); }
'''

OK, NO_DIR, NO_SO, MANY_SO = 0, 1, 2, 3


def compiler():
    if os.name == 'nt': return None
    for c in ('clang', 'cc', 'gcc'):
        if shutil.which(c): return c
    return None


@unittest.skipIf(compiler() is None, 'no C compiler')
class DriverPkg(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        src = cls.tmp / 't.c'; src.write_text(SHIM)
        subprocess.run([compiler(), '-shared', '-fPIC', '-O1', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-I', REPO / 'dm/android', src, '-o', cls.tmp / 't.so'], check=True)
        cls.lib = ctypes.CDLL(str(cls.tmp / 't.so'))
        cls.lib.field.restype = ctypes.c_int
        cls.lib.field.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
        cls.lib.scan.restype = ctypes.c_int
        cls.lib.scan.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
        cls.lib.label.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]

    @classmethod
    def tearDownClass(cls): shutil.rmtree(cls.tmp)

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir=self.tmp)); self.addCleanup(shutil.rmtree, self.d)

    def field(self, js, key='name'):
        out = ctypes.create_string_buffer(96)
        return self.lib.field(js.encode(), key.encode(), out, len(out)), out.value.decode()

    def scan(self, d=None):
        out = ctypes.create_string_buffer(200)
        return self.lib.scan(str(d if d is not None else self.d).encode(), out, len(out)), out.value.decode()

    def label(self, p):
        out = ctypes.create_string_buffer(96)
        self.lib.label(str(p).encode(), out, len(out))
        return out.value.decode()

    def package(self, *names, meta=None):
        for n in names:
            p = self.d / n
            p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'\x7fELF')
        if meta is not None: (self.d / 'meta.json').write_text(meta)
        return self.d

    def test_the_only_so(self):
        self.package('libvulkan_freedreno.so', meta='{"schemaVersion": 2, "name": "Turnip 24.3.0"}')
        self.assertEqual(self.scan(), (OK, 'libvulkan_freedreno.so'))

    def test_the_library_named(self):
        self.package('libvulkan_freedreno.so', 'libvulkan_lvp.so', meta='{"name": "Two", "library": "libvulkan_lvp.so"}')
        self.assertEqual(self.scan(), (OK, 'libvulkan_lvp.so'))

    def test_the_one_libvulkan(self):
        self.package('libhook.so', 'libvulkan_freedreno.so', 'libparser.so')
        self.assertEqual(self.scan(), (OK, 'libvulkan_freedreno.so'))

    def test_several_without_a_library(self):
        self.package('aa.so', 'bb.so')
        self.assertEqual(self.scan(), (MANY_SO, ''))
        # a meta.json that names one that is not there leaves it to the choice, which is still several
        self.package('aa.so', 'bb.so', meta='{"library": "libvulkan_other.so"}')
        self.assertEqual(self.scan(), (MANY_SO, ''))

    def test_nothing_to_load(self):
        self.package('meta.json.orig', 'readme.txt', meta='{"name": "Empty"}')
        self.assertEqual(self.scan(), (NO_SO, ''))
        self.assertEqual(self.scan(self.d / 'nowhere'), (NO_DIR, ''))

    def test_a_directory_and_a_link(self):
        (self.d / 'libfoo.so.bak').mkdir(); (self.d / 'libvulkan_x.so').write_bytes(b'\x7fELF')
        (self.d / 'dir.so').mkdir()
        self.assertEqual(self.scan(), (OK, 'libvulkan_x.so'))   # a folder named .so is no driver
        os.symlink(self.d / 'libvulkan_x.so', self.d / 'libvulkan_y.so')
        self.assertEqual(self.scan(), (MANY_SO, ''))           # a link to one counts, and now there are two libvulkan ones

    def test_meta_fields(self):
        js = ('{"schemaVersion": 2, "name": "Turnip \\"dev\\" 24.3", "description": "name: not this one", '
              '"vendor": "mesa", "driverVersion": "24.3.0-devel", "library": "libvulkan_freedreno.so"}')
        self.assertEqual(self.field(js), (1, 'Turnip "dev" 24.3'))   # an escaped quote comes out as itself
        self.assertEqual(self.field(js, 'vendor'), (1, 'mesa'))
        self.assertEqual(self.field(js, 'driverVersion'), (1, '24.3.0-devel'))
        self.assertEqual(self.field(js, 'library'), (1, 'libvulkan_freedreno.so'))
        self.assertEqual(self.field(js, 'author'), (0, ''))
        self.assertEqual(self.field('{"name" "no colon"}'), (0, ''))
        self.assertEqual(self.field('{"xname": "no"}'), (0, ''))   # a key that only ends with this one
        self.assertEqual(self.field('{"name": 42}'), (0, ''))      # not a string: left out

    def test_labels(self):
        self.assertEqual(self.label('/data/user/0/app/files/drivers/Turnip 24.3/'), 'Turnip_24.3')
        self.assertEqual(self.label('drivers/freedreno/'), 'freedreno')
        self.assertEqual(self.label('libvulkan_freedreno.so'), 'libvulkan_freedreno')
        self.assertEqual(self.label('/x/a~b/c d.so'), 'c_d')
        self.assertEqual(len(self.label('x' * 300)), 64)
        self.assertNotEqual(self.label('has/slash'), 'has/slash')


if __name__ == '__main__':
    unittest.main()
