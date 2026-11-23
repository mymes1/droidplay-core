// The phone's graphics driver packages: a zip of a Vulkan driver's .so and the meta.json every emulator's driver package carries (AdrenoTools', Winlator's, Eden's:
// {"schemaVersion": 2, "name": "Turnip 24.3", "driverVersion": "…", "library": "libvulkan_freedreno.so"}), unpacked into <external files>/drivers/<name>/ and named
// by a game's launcher.txt as DM_VK_DRIVER=drivers/<name>/<so>, which the host resolves against the same folder (dm/android/vk_driver.c, driver_pkg.h: the folder's
// only .so, or the one meta.json's "library" names). The settings screen's Graphics driver row lists what is here, and its Add a driver package row opens the
// phone's file picker for a zip. Plain Java, no Android APIs, so dm/tools/tests/test_android_drivers.py runs it on a JVM.
package org.nxnative.android;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

public final class Drivers {
    static final long MAX_BYTES = 512L << 20;   // a driver's .so is tens of megabytes: a zip much over this is no driver package
    static final int MAX_ENTRIES = 4096;
    static final int MAX_JSON = 1 << 16;

    private Drivers() { }

    // A package on the phone: the folder under drivers/, the .so in it, and what its meta.json calls it
    static final class Pack {
        final String id, so;
        final String name, version;   // meta.json's, either null
        Pack(String id, String so, String name, String version) { this.id = id; this.so = so; this.name = name; this.version = version; }
        String label() {   // what the settings screen shows
            String n = name != null && name.length() != 0 ? name : id;
            return version != null && version.length() != 0 ? n + " " + version : n;
        }
        String value() { return "drivers/" + id + "/" + so; }   // launcher.txt's DM_VK_DRIVER
    }

    static File root(File ext) { return new File(ext, "drivers"); }

    // The packages on the phone, by folder name. A folder with no .so to load (an install cut short, a folder a player made) is left out
    static List<Pack> scan(File ext) {
        List<Pack> out = new ArrayList<Pack>();
        File[] ds = root(ext).listFiles();
        if (ds == null) return out;
        Arrays.sort(ds);
        for (File d : ds) {
            if (!d.isDirectory() || d.getName().startsWith(".")) continue;
            Pack p = read(d);
            if (p != null) out.add(p);
        }
        return out;
    }

    // The package in dir: its .so (meta.json's "library" when that names one there, else the only one, else the only libvulkan*.so) → null when it has none to load.
    // The same rule as driver_pkg.h's dpk_scan, which the host goes by
    static Pack read(File dir) {
        String[] names = dir.list();
        if (names == null) return null;
        Arrays.sort(names);
        List<String> sos = new ArrayList<String>();
        for (String n : names) if (n.endsWith(".so") && new File(dir, n).isFile()) sos.add(n);
        if (sos.isEmpty()) return null;
        String js = text(new File(dir, "meta.json"));
        String lib = field(js, "library");
        String so = null;
        if (lib != null && sos.contains(lib)) so = lib;
        else if (sos.size() == 1) so = sos.get(0);
        else {
            String one = null; int n = 0;
            for (String s : sos) if (s.startsWith("libvulkan")) { if (one == null) one = s; n++; }
            if (n == 1) so = one;
        }
        if (so == null) return null;
        String ver = field(js, "driverVersion");
        if (ver == null) ver = field(js, "packageVersion");
        return new Pack(dir.getName(), so, field(js, "name"), ver);
    }

    // A driver package's zip unpacked into root(ext)/<label>/: its meta.json and its .so, from the zip's top level or from the one folder it wraps them in
    // (Winlator's and Eden's zips have either). → the package, or IOException saying why not (no .so, too large, a damaged zip)
    static Pack install(File ext, File zip) throws IOException {
        int soDepth = -1, metaDepth = -1;   // the shallowest folder an entry of each kind sits in
        ZipInputStream in = new ZipInputStream(new FileInputStream(zip));
        try {
            ZipEntry e;
            while ((e = in.getNextEntry()) != null) {
                if (e.isDirectory()) continue;
                String p = path(e.getName());
                if (p == null) continue;
                String base = p.substring(p.lastIndexOf('/') + 1);
                if (base.endsWith(".so")) soDepth = shallower(soDepth, depth(p));
                else if (base.equals("meta.json")) metaDepth = shallower(metaDepth, depth(p));
            }
        } finally { in.close(); }
        if (soDepth < 0) throw new IOException("the zip holds no .so: a driver package is a Vulkan driver's .so and its meta.json");
        File root = root(ext);
        if (!root.isDirectory() && !root.mkdir()) throw new IOException("cannot make " + root);
        File tmp = new File(root, ".incoming-" + System.currentTimeMillis());
        if (!tmp.mkdir()) throw new IOException("cannot make " + tmp);
        try {
            long total = 0; int n = 0;
            in = new ZipInputStream(new FileInputStream(zip));
            try {
                ZipEntry e;
                while ((e = in.getNextEntry()) != null) {
                    if (e.isDirectory()) continue;
                    String p = path(e.getName());
                    if (p == null) continue;
                    String base = p.substring(p.lastIndexOf('/') + 1);
                    int want = base.endsWith(".so") ? soDepth : base.equals("meta.json") ? metaDepth : -2;
                    if (want < 0 || depth(p) != want) continue;   // a deeper file is no part of the package (another GPU's .so beside it, a README)
                    if (++n > MAX_ENTRIES) throw new IOException("the zip holds over " + MAX_ENTRIES + " files");
                    total += copy(in, new File(tmp, base), base.equals("meta.json") ? MAX_JSON : MAX_BYTES);
                    if (total > MAX_BYTES) throw new IOException("the zip holds over " + (MAX_BYTES >> 20) + " MB");
                }
            } finally { in.close(); }
            Pack p = read(tmp);
            if (p == null) throw new IOException("the zip's .so files are several, and its meta.json names none (\"library\")");
            String id = free(root, sanitize(p.name != null && p.name.length() != 0 ? p.name : p.so));
            File dst = new File(root, id);
            if (!tmp.renameTo(dst)) {   // (a rename across the same folder: the launcher's files are one filesystem)
                if (!dst.mkdir()) throw new IOException("cannot make " + dst);
                copyDir(tmp, dst);
            }
            return new Pack(id, p.so, p.name, p.version);
        } finally {
            if (tmp.isDirectory()) delete(tmp);
        }
    }

    // drivers/<id> and everything in it → whether it went
    static boolean remove(File ext, String id) {
        if (id == null || id.length() == 0 || id.indexOf('/') >= 0) return false;
        File d = new File(root(ext), id);
        if (!d.isDirectory()) return false;
        delete(d);
        return !d.exists();
    }

    // ---------------- The small parts ----------------

    // A JSON string field of a package's meta.json: json's value of key unescaped, or null. Its strings are walked, so a field's name inside another's value is no field
    static String field(String js, String key) {
        if (js == null) return null;
        int i = 0, n = js.length();
        while (i < n) {
            char c = js.charAt(i);
            if (c == '"') {
                StringBuilder t = new StringBuilder();
                int k = i + 1;
                for (; k < n && js.charAt(k) != '"'; k++) { if (js.charAt(k) == '\\' && k + 1 < n) k++; t.append(js.charAt(k)); }
                i = k + 1;
                if (!t.toString().equals(key)) continue;
                while (i < n && isSpace(js.charAt(i))) i++;
                if (i >= n || js.charAt(i) != ':') continue;
                i++;
                while (i < n && isSpace(js.charAt(i))) i++;
                if (i >= n || js.charAt(i) != '"') continue;
                StringBuilder v = new StringBuilder();
                for (i++; i < n && js.charAt(i) != '"'; i++) { if (js.charAt(i) == '\\' && i + 1 < n) i++; v.append(js.charAt(i)); }
                return v.toString();
            }
            i++;
        }
        return null;
    }

    static boolean isSpace(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }

    // An entry's path with one kind of slash, no leading one, or null when it would leave the folder or holds no name (a "..", an absolute path, a drive letter)
    static String path(String entry) {
        String s = entry.replace('\\', '/');
        while (s.startsWith("/")) s = s.substring(1);
        if (s.length() == 0) return null;
        for (String part : s.split("/", -1)) {
            if (part.length() == 0 || part.equals(".") || part.equals("..") || part.indexOf(':') >= 0) return null;
        }
        return s;
    }

    static int depth(String p) {   // how many folders an entry sits in: 0 at the zip's top level
        int d = 0;
        for (int i = 0; i < p.length(); i++) if (p.charAt(i) == '/') d++;
        return d;
    }

    // A folder name a package's name makes: letters, digits, '.', '-' and '_' kept, the rest made '_', at most 64 characters (driver_pkg.h's dpk_label)
    static String sanitize(String s) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < s.length() && b.length() < 64; i++) {
            char c = s.charAt(i);
            b.append((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '-' || c == '_' ? c : '_');
        }
        String r = b.toString();
        return r.length() == 0 ? "driver" : r;
    }

    static int shallower(int least, int d) { return least < 0 || d < least ? d : least; }

    static String free(File root, String id) {   // id, or id-2, id-3… when a package of that name is there
        String r = id;
        for (int n = 2; new File(root, r).exists() && n < 1000; n++) r = id + "-" + n;
        return r;
    }

    static String text(File f) {
        if (!f.isFile() || f.length() > MAX_JSON) return null;
        try {
            byte[] b = new byte[(int) f.length()];
            InputStream in = new FileInputStream(f);
            try {
                int o = 0, k;
                while (o < b.length && (k = in.read(b, o, b.length - o)) > 0) o += k;
                return o == b.length ? new String(b, StandardCharsets.UTF_8) : null;
            } finally { in.close(); }
        } catch (IOException e) { return null; }
    }

    // in copied into f, at most cap bytes → how many were written; over cap: IOException
    static long copy(InputStream in, File f, long cap) throws IOException {
        OutputStream out = new FileOutputStream(f);
        try {
            byte[] b = new byte[1 << 16]; long n = 0; int k;
            while ((k = in.read(b)) > 0) {
                n += k;
                if (n > cap) throw new IOException("the zip's " + f.getName() + " is larger than a driver package's");
                out.write(b, 0, k);
            }
            return n;
        } finally { out.close(); }
    }

    static void copyDir(File from, File to) throws IOException {
        File[] fs = from.listFiles();
        if (fs == null) return;
        for (File f : fs) {
            File t = new File(to, f.getName());
            if (f.isDirectory()) { if (!t.mkdir() && !t.isDirectory()) throw new IOException("cannot make " + t); copyDir(f, t); }
            else { InputStream in = new FileInputStream(f); try { copy(in, t, MAX_BYTES); } finally { in.close(); } }
        }
    }

    static void delete(File f) {   // links are deleted, not followed (as Removal.java's)
        if (!link(f) && f.isDirectory()) { File[] fs = f.listFiles(); if (fs != null) for (File c : fs) delete(c); }
        f.delete();
    }

    static boolean link(File f) { return Files.isSymbolicLink(f.toPath()); }
}
