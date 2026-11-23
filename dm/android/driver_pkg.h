// A driver package: a folder a player put on the phone holding a Vulkan driver's .so, and usually the meta.json every emulator's driver package carries
// (AdrenoTools', Winlator's and Eden's layout: {"schemaVersion": 2, "name": "Turnip 24.3", "description": "…", "author": "…", "vendor": "mesa",
// "driverVersion": "…", "library": "libvulkan_freedreno.so"}), the launcher's "Add a driver package (.zip)" unpacks one into files/drivers/<name>/.
// DM_VK_DRIVER (env.txt, or the game's launcher.txt) may name the folder or the .so in it; vk_driver.c asks here which .so that is, what the package calls
// itself, and what label it goes by (the copy in the internal files, the start records, the pipeline cache's file, which all want a plain name).
// The rules are plain functions of a directory and a text, so dm/tools/tests/test_driver_pkg.py checks them on the host.
#pragma once
#include <dirent.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

enum { DPK_OK = 0, DPK_NO_DIR = 1, DPK_NO_SO = 2, DPK_MANY_SO = 3 };
#define DPK_MAX_SO 8   // .so files looked at in one folder

// A JSON string field of a meta.json's text (its value's escapes dropped): 1 when it is there, its value in out (empty when not)
static int dpk_field(const char *js, size_t n, const char *key, char *out, size_t cap) {
    size_t kl = strlen(key), i = 0;
    if (cap) out[0] = 0;
    for (; i + kl + 2 < n; i++) {   // "key" then a colon: a field of the object, not part of a value
        if (js[i] != '"' || js[i + 1] != key[0] || memcmp(js + i + 1, key, kl) || js[i + 1 + kl] != '"') continue;
        size_t k = i + 2 + kl;
        while (k < n && (js[k] == ' ' || js[k] == '\t' || js[k] == '\r' || js[k] == '\n')) k++;
        if (k >= n || js[k] != ':') continue;
        k++;
        while (k < n && (js[k] == ' ' || js[k] == '\t' || js[k] == '\r' || js[k] == '\n')) k++;
        if (k >= n || js[k] != '"') continue;
        size_t o = 0;
        for (k++; k < n && js[k] != '"'; k++) {
            if (js[k] == '\\' && k + 1 < n) k++;
            if (cap && o + 1 < cap) out[o++] = js[k];
        }
        if (cap) out[o] = 0;
        return 1;
    }
    return 0;
}

// The label a package goes by: its folder's name (trailing slashes dropped), anything but letters, digits, '.', '-' and '_' made '_', cut to 64 bytes, so
// that it is a file name the internal copy and the records can take. A path that names a .so (not a folder) gives the .so's name without ".so"
static void dpk_label(const char *path, char *out, size_t cap) {
    char b[256]; snprintf(b, sizeof b, "%s", path);
    for (size_t n = strlen(b); n && b[n - 1] == '/'; ) b[--n] = 0;
    const char *t = strrchr(b, '/'); t = t && t[1] ? t + 1 : b;
    size_t i = 0;
    if (cap < 2) { if (cap) out[0] = 0; return; }
    for (; *t && i + 1 < cap && i < 64; t++) {
        char c = *t;
        out[i++] = ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '-' || c == '_') ? c : '_';
    }
    if (i > 3 && !strcmp(out + i - 3, ".so")) i -= 3, out[i] = 0;   // a .so named here: its package label is its name without the suffix
    out[i] = 0;
}

// The .so of the package in dir into so (its file name): its meta.json's "library" when that names one there, else the only one, else the only one whose
// name starts with libvulkan. → DPK_OK, or why not
static int dpk_scan(const char *dir, char *so, size_t cap) {
    DIR *d = opendir(dir);
    if (!d) return DPK_NO_DIR;
    char name[DPK_MAX_SO][128]; int n = 0; struct dirent *e;
    while ((e = readdir(d)) && n < DPK_MAX_SO) {
        size_t l = strlen(e->d_name);
        if (l < 4 || l >= sizeof name[0] || strcmp(e->d_name + l - 3, ".so")) continue;
        char p[1200]; snprintf(p, sizeof p, "%s/%s", dir, e->d_name);
        struct stat st; if (stat(p, &st) || !S_ISREG(st.st_mode)) continue;   // a link to one counts (stat follows it)
        memcpy(name[n++], e->d_name, l + 1);   // (its length was checked above)
    }
    closedir(d);
    if (!n) return DPK_NO_SO;
    char lib[128] = "";
    { char p[1200], js[8192]; snprintf(p, sizeof p, "%s/meta.json", dir);
      FILE *f = fopen(p, "rb");
      if (f) { size_t k = fread(js, 1, sizeof js - 1, f); fclose(f); js[k] = 0; dpk_field(js, k, "library", lib, sizeof lib); } }
    for (int i = 0; i < n; i++) if (lib[0] && !strcmp(name[i], lib)) { snprintf(so, cap, "%s", name[i]); return DPK_OK; }
    if (n == 1) { snprintf(so, cap, "%s", name[0]); return DPK_OK; }
    int one = -1, vulkan = 0;
    for (int i = 0; i < n; i++) if (!strncmp(name[i], "libvulkan", 9)) { if (one < 0) one = i; vulkan++; }
    if (vulkan == 1) { snprintf(so, cap, "%s", name[one]); return DPK_OK; }
    return DPK_MANY_SO;
}
