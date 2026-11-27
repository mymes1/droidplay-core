# A game's shaders that its own shader pack has no module for, saved on the device by the Android replay (DM_SPV_MISSING=<dir>, gfx_vk_draw.inc spv_missing_save) as
# <key>.ctl and <key>.code pairs, packed into the package dm/android/tools/spv_build.py translates: the way to fix a game whose pack was built without them (a file the
# split could not read, an update's or a DLC's shader file the build was not given) without its files here. The keys are checked against the bytes, so a profile that
# differs from the pack's says so instead of adding a module under a key the replay never asks for (docs/reference/rendering.md, "A draw whose shader the pack does not hold").
# Usage: shader_dump_pack.py <dump dir> <package dir> [--facts <shader_facts.tsv>]
import argparse
import json
from pathlib import Path
import struct
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try: import shader_split as ss   # glslc_stage, check, skey, ctl_len: the split's own reading of a control section and a code area
except SystemExit:   # its import reads a game profile: the keys are the profile's (shader_key_skip), so it is the game's own
    print('[RED] shader_dump_pack: DM_GAME is not set. The key of a shader is its profile\'s (shader_key_skip), so build with the game\'s own: DM_GAME=<title ID> (\'python3 '
          'dm/games/game.py list\', or the ID the build used). A game with no profile in this tree still needs the one the pack was built with', flush=True)
    raise SystemExit(1)

REC = b'BTCDBTCD'   # the package's record separator (shader_split.py writes the same; spv_build.py records/split read it)

def record(key, ctl, code):
    """One shader as the package holds it: 'BTCDBTCD', the key's 8 bytes, 0x28 bytes, the control section's length and the code area's at +0x28, then the code area and the
    control section. The separator must appear once and the control magic must start the control section (spv_build.py checks both the same way)"""
    r = bytearray(REC + bytes.fromhex(key) + bytes(0x28))
    struct.pack_into('<2I', r, 0x28, len(ctl), len(code))
    r += code + ctl
    if r.count(REC) != 1: raise ValueError('the record separator appears inside the shader')
    if r.find(ss.CTL_MAGIC, 0x38) != 0x38 + len(code): raise ValueError('the control magic appears inside the code area')
    return bytes(r)

def facts_of(ctl):
    """A compute shader's thread group and shared memory, as shader_split.py writes them (NVN2: +0x58 / +0x64, NVN: +0x748 / +0x754)"""
    at = (0x58, 0x64) if ss.nvn2_ctl(ctl) else (0x748, 0x754)
    if len(ctl) < at[1] + 4: return None
    x, y, z = struct.unpack_from('<3I', ctl, at[0]); sh = struct.unpack_from('<I', ctl, at[1])[0]
    return {'local': [x, y, z], 'shared': (sh + 15) // 16 * 16} if x and y and z else None

def main():
    ap = argparse.ArgumentParser(description='pack the shaders an Android replay saved (DM_SPV_MISSING) for spv_build.py')
    ap.add_argument('dump'); ap.add_argument('package'); ap.add_argument('--facts', default=None)
    a = ap.parse_args()
    dump, pkg = Path(a.dump), Path(a.package)
    if not dump.is_dir(): print(f'[RED] shader_dump_pack: {dump} is no directory (DM_SPV_MISSING\'s)', flush=True); return 1
    names = sorted(p.stem for p in dump.glob('*.ctl'))
    if not names:
        print(f'[RED] shader_dump_pack: no <key>.ctl in {dump}: the replay writes them where DM_SPV_MISSING points, for each shader its pack has no module for', flush=True)
        return 1
    pkg.mkdir(parents=True, exist_ok=True)
    recs, facts, bad, short = {}, {}, [], []
    for k in names:
        c = dump / f'{k}.ctl'; d = dump / f'{k}.code'
        if not d.exists(): bad.append(f'{k}: its .code is missing'); continue
        ctl, code = c.read_bytes(), d.read_bytes()
        if ss.skey(ctl, code) != k:   # the replay's key is the host's (hle_nvn.c keys_hash): a different one means another profile or another shader's bytes
            bad.append(f'{k}: the bytes are not this key\'s (their key is {ss.skey(ctl, code)}): DM_GAME\'s profile is not the one the pack was built with, or the pair is mixed'); continue
        st = ss.glslc_stage(ctl, code)
        if not st: bad.append(f'{k}: no stage in its control section and code area'); continue
        if ss.nvn2_ctl(ctl) and len(ctl) <= ss.NVN2_KEY_CTL:   # the host keeps an NVN2 control section only as far as its key reads it, and the translator needs all of it
            short.append(f'{k}: its control section is the key\'s {len(ctl):#x} bytes'); continue
        why = ss.check(k, st, ctl, code)
        if why:
            # NVN2's control section in memory is only the key's 0x48 bytes, and the translator needs all of it: that one has to come from the game's files
            if ss.nvn2_ctl(ctl) or len(ctl) < ss.ctl_len(ctl): short.append(f'{k}: {why}')
            else: bad.append(f'{k}: {why}')
            continue
        try: recs.setdefault(f'{st}_dump.bin', bytearray()).extend(record(k, ctl, code))
        except ValueError as e: bad.append(f'{k}: {e}'); continue
        if st == 'cs' and (f := facts_of(ctl)): facts[k] = f
    for name, r in recs.items(): (pkg / name).write_bytes(bytes(r))
    fpath = Path(a.facts) if a.facts else pkg / 'shader_facts.tsv'
    with open(fpath, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# Answer table for translation queries (generated by dm/gfx/tools/shader_dump_pack.py, do not edit): key <TAB> fact JSON. Only a compute shader\'s thread group / shared memory is known; other queries get the translator defaults\n')
        for k in sorted(facts): f.write(k + '\t' + json.dumps(facts[k], sort_keys=True, separators=(',', ':')) + '\n')
    print(f'shader_dump_pack: {len(names) - len(bad) - len(short)} of {len(names)} shader(s) packed into {pkg}' + (f' ({", ".join(sorted(recs))})' if recs else ''), flush=True)
    for b in bad: print(f'[RED] shader_dump_pack: {b}', flush=True)
    for s in short: print(f'[RED] shader_dump_pack: {s}: the control section kept on the device is shorter than the translator needs (NVN2 keeps only its key bytes), so this one has to come from the game\'s files', flush=True)
    if recs:
        print(f'shader_dump_pack: translate them with:\n  python3 dm/android/tools/spv_build.py {pkg} <the game\'s spv folder> --xlate <the build\'s dmsh_xlate.dll> --facts {fpath}\n'
              'then start the game again: the replay finds <key>.spv and <key>.res.json beside the pack\'s others', flush=True)
    return 0 if recs else 1

if __name__ == '__main__':
    sys.exit(main())
