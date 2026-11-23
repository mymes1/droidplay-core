"""The launcher's driver packages (dm/android/java/org/nxnative/android/Drivers.java), run on a JVM: a zip's .so and meta.json unpacked into drivers/<name>/,
from the zip's top level or from the one folder it wraps them in; several .so files with the meta.json's "library" naming one; no .so, or several and nothing
naming one (refused, and nothing left behind); an entry that would escape the folder left out; two packages of the same name side by side; what scan() lists,
what remove() takes off, and the value a game's DM_VK_DRIVER takes. Synthetic zips only: no phone and no driver. Skipped without a JDK (JAVA_HOME, or javac on
PATH)."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

from test_android_saves import JDK

REPO = Path(__file__).resolve().parents[3]

DRIVER = r'''package org.nxnative.android;
import java.io.File;
public class DriverCheck {
    static String t(Drivers.Pack p) { return p.id + "\t" + p.so + "\t" + p.label() + "\t" + p.value(); }
    public static void main(String[] a) throws Exception {
        if (a[0].equals("scan")) {
            for (Drivers.Pack p : Drivers.scan(new File(a[1]))) System.out.println(t(p));
        } else if (a[0].equals("install")) {
            try { System.out.println(t(Drivers.install(new File(a[1]), new File(a[2])))); }
            catch (Exception e) { System.out.println("ERR " + e.getMessage()); }
        } else if (a[0].equals("remove")) {
            System.out.println(Drivers.remove(new File(a[1]), a[2]));
        } else if (a[0].equals("read")) {
            Drivers.Pack p = Drivers.read(new File(a[1]));
            System.out.println(p == null ? "-" : t(p));
        } else if (a[0].equals("field")) {
            String js = Drivers.text(new File(a[1]));
            String v = Drivers.field(js, a[2]);
            System.out.println(v == null ? "-" : v);
        } else if (a[0].equals("sanitize")) {
            System.out.println(Drivers.sanitize(a[1]));
        }
    }
}
'''


@unittest.skipUnless(JDK, 'needs a JDK (JAVA_HOME, or javac on PATH)')
class DriverPackages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = Path(tempfile.mkdtemp())
        src = cls.build / 'src'; out = cls.build / 'out'; src.mkdir(); out.mkdir()
        (src / 'DriverCheck.java').write_text(DRIVER)
        subprocess.run([JDK[0], '-source', '8', '-target', '8', '-Xlint:none', '-nowarn', '-encoding', 'UTF-8', '-d', str(out),
                        str(REPO / 'dm/android/java/org/nxnative/android/Drivers.java'), str(src / 'DriverCheck.java')], check=True, capture_output=True)
        cls.classes = out

    @classmethod
    def tearDownClass(cls): shutil.rmtree(cls.build)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp)
        self.ext = self.tmp / 'ext'; self.ext.mkdir()

    def run_java(self, *args):
        r = subprocess.run([JDK[1], '-cp', str(self.classes), 'DriverCheck', *map(str, args)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def zip(self, name, files):
        p = self.tmp / name
        with zipfile.ZipFile(p, 'w') as z:
            for n, b in files.items(): z.writestr(n, b)
        return p

    META = b'{"schemaVersion": 2, "name": "Turnip 24.3.0", "description": "a driver", "vendor": "mesa", "driverVersion": "24.3.0-devel", "library": "libvulkan_freedreno.so"}'

    def test_install_and_scan(self):
        z = self.zip('t.zip', {'libvulkan_freedreno.so': b'\x7fELF' * 10, 'meta.json': self.META})
        self.assertEqual(self.run_java('install', self.ext, z), 'Turnip_24.3.0\tlibvulkan_freedreno.so\tTurnip 24.3.0 24.3.0-devel\tdrivers/Turnip_24.3.0/libvulkan_freedreno.so')
        self.assertEqual(self.run_java('scan', self.ext), 'Turnip_24.3.0\tlibvulkan_freedreno.so\tTurnip 24.3.0 24.3.0-devel\tdrivers/Turnip_24.3.0/libvulkan_freedreno.so')
        self.assertTrue((self.ext / 'drivers/Turnip_24.3.0/libvulkan_freedreno.so').is_file())
        self.assertFalse(list((self.ext / 'drivers').glob('.incoming-*')))   # nothing left of the unpacking

    def test_a_folder_wrapping_the_package(self):
        z = self.zip('t.zip', {'Turnip_24.3/libvulkan_freedreno.so': b'\x7fELF', 'Turnip_24.3/meta.json': self.META, 'Turnip_24.3/README.md': b'x'})
        self.assertEqual(self.run_java('install', self.ext, z).split('\t')[1], 'libvulkan_freedreno.so')

    def test_the_library_named(self):
        z = self.zip('t.zip', {'libvulkan_lvp.so': b'L', 'libvulkan_freedreno.so': b'F', 'meta.json': b'{"name": "Two", "library": "libvulkan_lvp.so"}'})
        self.assertEqual(self.run_java('install', self.ext, z).split('\t')[1], 'libvulkan_lvp.so')

    def test_nothing_to_load(self):
        z = self.zip('t.zip', {'README.md': b'not a driver'})
        self.assertTrue(self.run_java('install', self.ext, z).startswith('ERR '))
        self.assertEqual(self.run_java('scan', self.ext), '')
        z = self.zip('u.zip', {'aa.so': b'a', 'bb.so': b'b'})
        self.assertIn('library', self.run_java('install', self.ext, z))
        self.assertFalse(list((self.ext / 'drivers').glob('*')))   # no half-made package left

    def test_an_entry_that_escapes(self):
        z = self.zip('t.zip', {'../outside.so': b'x', 'ok/../../../also.so': b'y', '/abs.so': b'z'})
        self.assertTrue(self.run_java('install', self.ext, z).startswith('ERR '))
        self.assertFalse((self.tmp / 'outside.so').exists())
        self.assertFalse((self.ext / 'abs.so').exists())

    def test_two_packages_of_one_name(self):
        z = self.zip('t.zip', {'libvulkan_freedreno.so': b'\x7fELF', 'meta.json': self.META})
        self.assertEqual(self.run_java('install', self.ext, z).split('\t')[0], 'Turnip_24.3.0')
        self.assertEqual(self.run_java('install', self.ext, z).split('\t')[0], 'Turnip_24.3.0-2')
        self.assertEqual(len(self.run_java('scan', self.ext).splitlines()), 2)

    def test_remove(self):
        z = self.zip('t.zip', {'libvulkan_freedreno.so': b'\x7fELF', 'meta.json': self.META})
        self.run_java('install', self.ext, z)
        self.assertEqual(self.run_java('remove', self.ext, 'Turnip_24.3.0'), 'true')
        self.assertEqual(self.run_java('scan', self.ext), '')
        self.assertEqual(self.run_java('remove', self.ext, 'Turnip_24.3.0'), 'false')
        self.assertEqual(self.run_java('remove', self.ext, '../ext'), 'false')   # nothing outside drivers/

    def test_reads_a_folder_where_a_player_made_one(self):
        d = self.ext / 'drivers/my_driver'; d.mkdir(parents=True)
        (d / 'x.so').write_bytes(b'\x7fELF')
        self.assertEqual(self.run_java('read', d), 'my_driver\tx.so\tmy_driver\tdrivers/my_driver/x.so')
        (d / 'meta.json').write_text('{"name": "Mine", "library": "not_here.so"}')
        self.assertEqual(self.run_java('read', d).split('\t')[1], 'x.so')   # a library that is not there: the only .so
        (d / 'y.so').write_bytes(b'\x7fELF')                                 # two, and nothing naming one: not a package
        self.assertEqual(self.run_java('read', d), '-')

    def test_the_meta_reader(self):
        f = self.tmp / 'meta.json'
        f.write_text('{"name": "A \\"quoted\\" one", "description": "name: not this one", "driverVersion": "1.2"}')
        self.assertEqual(self.run_java('field', f, 'name'), 'A "quoted" one')
        self.assertEqual(self.run_java('field', f, 'description'), 'name: not this one')
        self.assertEqual(self.run_java('field', f, 'driverVersion'), '1.2')
        self.assertEqual(self.run_java('field', f, 'author'), '-')
        f.write_text('{"name": 42}')
        self.assertEqual(self.run_java('field', f, 'name'), '-')

    def test_labels(self):
        self.assertEqual(self.run_java('sanitize', 'Turnip 24.3.0 (dev)'), 'Turnip_24.3.0__dev_')
        self.assertEqual(self.run_java('sanitize', '...'), '...')
        self.assertEqual(self.run_java('sanitize', 'драйвер'), '_______')
        self.assertEqual(len(self.run_java('sanitize', 'x' * 300)), 64)


if __name__ == '__main__':
    unittest.main()
