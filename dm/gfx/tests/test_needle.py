"""Needle's embedded NVN shader records, without game data."""
from pathlib import Path
from contextlib import redirect_stderr
import io
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import shader_split as split


def payload(stage, desc=0x90):
    """A control section and its code area, optionally behind a descriptor of desc bytes (its +0x30 words: control offset, code offset, code length, control length)"""
    ctl, code = bytearray(0x850), bytearray(0x100)
    ctl[:4] = split.CTL_MAGIC
    struct.pack_into('<I', ctl, 0x20, len(ctl))
    struct.pack_into('<I', ctl, 0x704, len(code))
    if stage == 'cs':
        struct.pack_into('<3I', ctl, 0x748, 8, 4, 1)
    else:
        code[:4] = split.GFX_MAGIC
        struct.pack_into('<I', code, 0x30, (1 if stage == 'vs' else 5) << 10 | 1)
        if stage == 'ps': struct.pack_into('<I', code, 0x48, 1)   # pixel shader reads generic location 0
    if not desc: return bytes(ctl + code), bytes(ctl), bytes(code)
    header = bytearray(desc)
    struct.pack_into('<4I', header, 0x30, desc, desc + len(ctl), len(code), len(ctl))
    return bytes(header + ctl + code), bytes(ctl), bytes(code)


def shader(stage, desc=0x90):
    return payload(stage, desc)


def container(*stages, desc=0x90):
    # Reflection/variant metadata can precede and separate the NVN records.
    d = bytearray(b'HNSHV002' + bytes(24) + b'variant metadata')
    for stage in stages:
        d += shader(stage, desc)[0] + bytes(36)
    struct.pack_into('>I', d, 8, len(d))
    return bytes(d)


def shader_file(stage):
    """A Hedgehog Engine 2 shader file: HHNEEDLE, its length, the shader's path, then the HNSHV002 archive whose variants are size-prefixed payloads with no descriptor"""
    d = bytearray(b'HHNEEDLE' + struct.pack('>I', 0) + b'needle/ps_test\0')
    while len(d) % 4: d += b'\0'
    at = len(d)
    body, ctl, code = payload(stage, 0)
    d += b'HNSHV002' + struct.pack('>I', 0) + bytes(8)
    struct.pack_into('>I', d, at + 8, 0)   # the archive's length, below once it is whole
    d += struct.pack('<I', len(body) + 4) + body   # a variant's shader data (its size includes that field)
    struct.pack_into('>I', d, at + 8, len(d) - at)
    struct.pack_into('>I', d, 8, len(d))
    return bytes(d)


def nvn2_payload(stage):
    """An NVN2 record (a Switch 2 game: Sonic Frontiers Definitive Edition): a 0x98-byte descriptor, a 0xd8-byte control section (version 0xc9, its own length at +0x30, the code's
    at +0x44, a compute shader's stage at +0x56) and the code right after it"""
    ctl, code = bytearray(0xd8), bytearray(0x200)
    ctl[:8] = split.CTL_MAGIC + b'\xc9\0\0\0'
    struct.pack_into('<I', ctl, 0x30, len(ctl))
    struct.pack_into('<I', ctl, 0x44, len(code))
    if stage == 'cs': ctl[0x56] = 5
    else: struct.pack_into('<I', code, 0, (1 if stage == 'vs' else 5) << 10 | 1)
    header = bytearray(0x98)
    struct.pack_into('<4I', header, 0x30, 0x98, 0x98 + len(ctl), len(code), len(ctl))
    return bytes(header + ctl + code)


def nvn2_container(*stages):
    d = bytearray(b'HNSHV002' + bytes(24) + b'variant metadata')
    for stage in stages: d += nvn2_payload(stage) + bytes(36)
    struct.pack_into('>I', d, 8, len(d))
    return bytes(d)


def lz4_chunks(b, stored=()):
    """b in 64 KB chunks: (bytes, stored length, length), LZ4 except the chunks numbered in stored"""
    import lz4.block
    out = []
    for i in range(0, max(len(b), 1), 0x10000):
        part = b[i:i + 0x10000]
        c = part if i // 0x10000 in stored else lz4.block.compress(part, store_size=False)
        out.append((c, len(c), len(part)))
    return out


def pacx403(blocks, types=b'VertexShader\0PixelShader\0', deps=0):
    """A Hedgehog Engine 2 PACx 403 archive: blocks are (name, PACx 402 bytes); with none, the root holds the resources (the types string stands in for its trees). deps: a
    dependency table's length (archives this one needs)"""
    data, entries = bytearray(), []
    for name, b in blocks:
        ch = lz4_chunks(b, stored={1})
        entries.append((name, sum(c for _, c, _ in ch), len(b), ch))
        data += b''.join(c for c, _, _ in ch)
    trees = bytearray(b'type tree\0' + types)
    trees += bytes(-len(trees) % 8)
    root = bytearray(b'PACx402L' + bytes(0x28)) + trees
    info_at = len(root)
    info = 0x10 + sum(0x20 + 8 * len(ch) for *_, ch in entries) if entries else 0
    tab = info_at + 0x10
    cks = tab + 0x20 * len(entries)
    names = cks + 8 * sum(len(ch) for *_, ch in entries)
    if entries: root += struct.pack('<QQ', len(entries), tab)
    k, nm = cks, names
    for name, stored, ln, ch in entries:
        root += struct.pack('<QIIIIQ', nm, stored, ln, 0, len(ch), k)   # the offset in the file is filled below
        k += 8 * len(ch); nm += len(name) + 1
    for *_, ch in entries:
        for _, c, u in ch: root += struct.pack('<II', c, u)
    for name, *_ in entries: root += name + b'\0'
    struct.pack_into('<II', root, 0x10, len(trees), info)
    ctab = (4 + 8 * -(-len(root) // 0x10000) + 7) & ~7   # the root's chunk count follows from its length
    head = bytearray(0x30 + deps + ctab)
    at = len(head)
    for i, (_, stored, *_) in enumerate(entries):   # the blocks follow the header, the root comes last
        struct.pack_into('<I', root, tab + 0x20 * i + 0x10, at); at += stored
    rch = lz4_chunks(bytes(root))
    head[:8] = b'PACx403L'
    struct.pack_into('<III', head, 0x10, at, sum(c for _, c, _ in rch), len(root))
    struct.pack_into('<II', head, 0x20, deps, ctab)
    struct.pack_into('<I', head, 0x30 + deps, len(rch))
    for i, (_, c, u) in enumerate(rch): struct.pack_into('<II', head, 0x34 + deps + 8 * i, c, u)
    return bytes(head + data + b''.join(c for c, _, _ in rch))


class NeedleTests(unittest.TestCase):
    def test_embedded_variants_and_headerless_compute(self):
        data = b'prefix\0' + container('vs', 'ps', 'cs') + b'suffix'
        found = []
        split.scan_blob(data, 'main', found)
        self.assertEqual([s for _, s, _, _ in found], ['vs', 'ps', 'cs'])
        for (_, stage, ctl, code) in found:
            self.assertEqual((ctl, code), shader(stage)[1:])
        keys = split.uniq_keys(found)
        # These stages are independent programs; the main/GSC wrapper is not a pair.
        self.assertEqual(split.var_pairs(found), set())
        self.assertEqual(len(split.separable_pairs(keys)[0]), 1)
        self.assertTrue(all('#NEEDLE@' in label for label, *_ in found))

    def test_container_bounds_reject_truncated_or_string_magic(self):
        self.assertEqual(split.needle_shaders(b'HNSHV002', 'short'), [])
        self.assertEqual(split.needle_shaders(container('vs')[:-1], 'truncated'), [])
        data = bytearray(container('vs'))
        struct.pack_into('>I', data, 8, 32)
        self.assertEqual(split.needle_shaders(data, 'short envelope'), [])

    def test_a_broken_descriptor_falls_back_to_the_sections_own_lengths(self):
        # A descriptor whose code area runs past the container is not used: the section's own lengths (+0x20, +0x704) and the code area after it are.
        data = bytearray(container('vs'))
        c = data.index(split.CTL_MAGIC)
        struct.pack_into('<I', data, c - 0x90 + 0x34, len(data))
        found = split.needle_shaders(bytes(data) + container('ps'), 'bad code offset')
        self.assertEqual([s for _, s, _, _ in found], ['vs', 'ps'])
        self.assertEqual(found[0][3], shader('vs')[2])   # the shader's own code area, never the next record's bytes

    def test_a_descriptor_that_disagrees_with_the_control_section_is_not_used(self):
        data = bytearray(container('vs'))
        c = data.index(split.CTL_MAGIC)
        struct.pack_into('<I', data, c - 0x90 + 0x3c, 0x880)   # a control length the control section's own +0x20 contradicts
        found = split.needle_shaders(bytes(data), 'bad control length')
        self.assertEqual([(s, len(ctl), len(code)) for _, s, ctl, code in found], [('vs', 0x850, 0x100)])

    def test_a_descriptor_of_another_size_is_found_by_its_own_offset(self):
        # Hedgehog Engine 2's shader payloads: the descriptor is not 0x90 bytes, and its +0x30 says how big it is
        for desc in (0xa0, 0x80, 0x100):
            with self.subTest(desc=desc):
                found = split.needle_shaders(container('vs', 'ps', desc=desc), f'descriptor {desc:#x}')
                self.assertEqual([(s, len(ctl)) for _, s, ctl, _ in found], [('vs', 0x850), ('ps', 0x850)])
                self.assertEqual(found[1][2:], shader('ps', desc)[1:])

    def test_a_payload_with_no_descriptor_is_a_control_section_and_its_code(self):
        d = bytearray(b'HNSHV002' + bytes(24))
        d += payload('ps', 0)[0]   # 0x850 bytes of control, then its code area
        struct.pack_into('>I', d, 8, len(d))
        found = split.needle_shaders(bytes(d), 'no descriptor')
        self.assertEqual([s for _, s, _, _ in found], ['ps'])
        self.assertEqual(found[0][2:], payload('ps', 0)[1:])

    def test_a_shader_file_of_hedgehog_engine_2(self):
        found = split.needle_shaders(shader_file('ps'), 'NeedleShader.pac')
        self.assertEqual([s for _, s, _, _ in found], ['ps'])
        self.assertEqual(found[0][2:], payload('ps', 0)[1:])

    def test_one_unreadable_section_leaves_the_files_others_in(self):
        good = payload('ps', 0)[0]
        d = bytearray(b'HNSHV002' + bytes(24))
        d += good + bytes(0x40) + split.CTL_MAGIC + bytes(0x758)   # a control section cut short: no code area in the container
        struct.pack_into('>I', d, 8, len(d))
        err = io.StringIO()
        with redirect_stderr(err): found = split.needle_shaders(bytes(d), 'half readable')
        self.assertEqual([s for _, s, _, _ in found], ['ps'])
        self.assertEqual(found[0][2:], payload('ps', 0)[1:])
        self.assertIn('could not be read, the rest are in', err.getvalue())   # the log says so; the file is not marked unreadable
    def test_pacx403_blocks(self):
        # two blocks over several chunks (one of them stored), the second with the Needle container; the archive's own bytes hold no readable container
        a = b'PACx402L' + bytes(0x28) + bytes(0x18000)
        b = b'PACx402L' + bytes(0x28) + container('vs', 'ps', 'cs') + bytes(0x12000)
        for deps in (0, 0x18):   # a dependency table moves the root's chunk table
            with self.subTest(deps=deps):
                found = []
                split.scan_blob(pacx403([(b'shaders.000', a), (b'shaders.001', b)], deps=deps), '/raw/x.pac', found)
                self.assertEqual([st for _, st, _, _ in found], ['vs', 'ps', 'cs'])
                self.assertTrue(all(label.startswith('/raw/x.pac/shaders.001#NEEDLE@') for label, *_ in found))

    def test_pacx403_resources_in_the_root(self):
        found = []
        split.scan_blob(pacx403([], types=b'ComputeShader\0' + container('cs')), '/raw/y.pac', found)
        self.assertEqual([(label.split('#')[0], st) for label, st, _, _ in found], [('/raw/y.pac/root', 'cs')])

    def test_pacx403_without_shader_types_is_not_opened(self):
        # decompressed whole, a game's other 1,434 archives gave 15.6 GB and held no shader: only one that names a shader type is opened
        d = pacx403([(b'stage.000', b'PACx402L' + bytes(0x28) + container('vs'))], types=b'Texture\0Model\0')
        self.assertEqual(split.pacx4_parts(d, 'stage.pac'), [])

    def test_pacx403_cut_short(self):
        d = pacx403([(b'shaders.000', b'PACx402L' + bytes(0x28))])
        with self.assertRaisesRegex(SystemExit, 'lie outside'):
            split.pacx4_parts(d[:-1], 'cut.pac')   # the root's last chunk cut short

    def test_pacx403_invalid_lz4_chunk(self):
        d = bytearray(pacx403([]))
        root, stored = struct.unpack_from('<II', d, 0x10)
        d[root:root + stored] = bytes(stored)
        with self.assertRaisesRegex(SystemExit, r'^\[RED\] corrupt\.pac: PACx 403 root: invalid LZ4 chunk'):
            split.pacx4_parts(d, 'corrupt.pac')

    def test_pacx403_truncated_header_and_root_chunk_table(self):
        deps = 0x18
        d = pacx403([], deps=deps)
        for length, section in ((0x24, 'header'), (0x34 + deps + 3, 'root chunk table'), (0x34 + deps + 7, 'root chunk table')):
            with self.subTest(length=length), self.assertRaisesRegex(SystemExit, f'PACx 403 {section}: .*lie outside'):
                split.pacx4_parts(d[:length], 'cut.pac')

    def test_pacx403_truncated_shader_root_header(self):
        root = b'PACx402LShader'
        d = bytearray(0x3c)
        d[:8] = b'PACx403L'
        struct.pack_into('<III4xII', d, 0x10, len(d), len(root), len(root), 0, 12)
        struct.pack_into('<III', d, 0x30, 1, len(root), len(root))
        with self.assertRaisesRegex(SystemExit, 'PACx 402 root header: .*lie outside'):
            split.pacx4_parts(d + root, 'cut.pac')

    def test_pacx403_layout_not_known_falls_back_to_its_bytes(self):
        # an archive revision this reader does not know: the bytes stored uncompressed hold their container, which is read as before (one line in the log says so)
        d = bytearray(pacx403([(b'shaders.000', b'PACx402L' + bytes(0x28) + bytes(0x10000) + container('vs'))]))   # the container in the stored chunk
        root, stored = struct.unpack_from('<II', d, 0x10)
        struct.pack_into('<I', d, 0x14, struct.unpack_from('<I', d, 0x14)[0] + 1)   # the root's chunks no longer add up to its stored length
        found, err = [], io.StringIO()
        with redirect_stderr(err): split.scan_blob(bytes(d), '/raw/z.pac', found)
        self.assertEqual([st for _, st, _, _ in found], ['vs'])
        self.assertIn('/raw/z.pac: the PACx 403 root chunks do not add up', err.getvalue())

    def test_nvn2_records(self):
        # Sonic Frontiers Definitive Edition's main: 1,586 records in 233 containers, every one with this layout
        data = b'prefix\0' + nvn2_container('vs', 'ps', 'cs') + b'suffix'
        with redirect_stderr(io.StringIO()):
            found = split.needle_shaders(data, 'main')
        self.assertEqual([st for _, st, _, _ in found], ['vs', 'ps', 'cs'])
        with redirect_stderr(io.StringIO()):   # a container of one NVN2 record is smaller than one NVN record: it is still read
            self.assertEqual(len(split.needle_shaders(nvn2_container('cs'), 'small')), 1)

    def test_nvn2_control_length_must_match_descriptor(self):
        # the control section says it is 0xd8 bytes, its descriptor 0xd0: the second section is left out and said, the first is read
        data = bytearray(nvn2_container('vs', 'ps'))
        c = data.index(split.CTL_MAGIC, data.index(split.CTL_MAGIC) + 4)
        struct.pack_into('<I', data, c - 0x98 + 0x3c, 0xd0)
        err = io.StringIO()
        with redirect_stderr(err): found = split.needle_shaders(bytes(data), 'bad control length')
        self.assertEqual([st for _, st, _, _ in found], ['vs'])
        self.assertIn('disagrees with its control section', err.getvalue())
        # in a file whose every section is such, nothing is read: the build says so
        bad = bytearray(nvn2_container('vs'))
        struct.pack_into('<I', bad, bad.index(split.CTL_MAGIC) - 0x98 + 0x3c, 0xd0)
        with self.assertRaisesRegex(SystemExit, 'disagrees with its control section'):
            split.needle_shaders(bytes(bad), 'only bad')

if __name__ == '__main__':
    unittest.main()
