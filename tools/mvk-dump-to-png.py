#!/usr/bin/env python3
"""Convert a MoltenVK frame dump to PNGs and an index.html contact sheet.

The dump is what MoltenVK writes to Documents/mvk-dump-<n>/ when Documents/mvk-dump-now
appears on the device (see mvkMadeiraFrameDumpTick in MVKCommandBuffer.mm): one raw file
per render-pass attachment, compute storage image, copy destination or sampled input
("input" entries), and index.json (index.jsonl while the frame is still being written)
describing each one. Render entries carry drawList (per draw: the textures and samplers bound
to its stages), compute entries inputs/samplers, "transfer" entries a copy's source,
destination and regions, and shaders/ holds each pipeline's MSL and binding map.

    tools/mvk-dump-to-png.py DUMP_DIR [--float reinhard|clamp|normalize] [--gamma 2.2] [-j N]

PNGs are written next to the raw files: 8-bit formats as they are, float/HDR color
tonemapped (Reinhard by default, then gamma), single-channel, depth, integer and two-channel
formats normalized min..max over the image (inf/nan ignored); depth also gets a _p1-99 PNG
normalized over its 1st..99th percentile. Alpha is dropped. Needs only the standard library.
"""

import argparse
import html
import json
import math
import os
import struct
import sys
import zlib
from concurrent.futures import ProcessPoolExecutor

# formatName -> (decoder, channel count). Channel order is R, G, B, A after decoding.
FORMATS = {
    'A8Unorm': ('unorm8', 1), 'R8Unorm': ('unorm8', 1), 'R8Unorm_sRGB': ('unorm8', 1),
    'R8Snorm': ('snorm8', 1), 'R8Uint': ('uint8', 1), 'R8Sint': ('sint8', 1), 'Stencil8': ('uint8', 1),
    'R16Unorm': ('unorm16', 1), 'R16Snorm': ('snorm16', 1), 'R16Uint': ('uint16', 1),
    'R16Sint': ('sint16', 1), 'R16Float': ('float16', 1),
    'RG8Unorm': ('unorm8', 2), 'RG8Unorm_sRGB': ('unorm8', 2), 'RG8Snorm': ('snorm8', 2),
    'RG8Uint': ('uint8', 2), 'RG8Sint': ('sint8', 2),
    'Depth16Unorm': ('depth16', 1),
    'R32Uint': ('uint32', 1), 'R32Sint': ('sint32', 1), 'R32Float': ('float32', 1),
    'RG16Unorm': ('unorm16', 2), 'RG16Snorm': ('snorm16', 2), 'RG16Uint': ('uint16', 2),
    'RG16Sint': ('sint16', 2), 'RG16Float': ('float16', 2),
    'RGBA8Unorm': ('unorm8', 4), 'RGBA8Unorm_sRGB': ('unorm8', 4), 'RGBA8Snorm': ('snorm8', 4),
    'RGBA8Uint': ('uint8', 4), 'RGBA8Sint': ('sint8', 4),
    'BGRA8Unorm': ('bgra8', 4), 'BGRA8Unorm_sRGB': ('bgra8', 4),
    'RGB10A2Unorm': ('rgb10a2', 4), 'RGB10A2Uint': ('rgb10a2uint', 4), 'BGR10A2Unorm': ('bgr10a2', 4),
    'RG11B10Float': ('rg11b10', 3), 'RGB9E5Float': ('rgb9e5', 3),
    'Depth32Float': ('depth32', 1), 'Depth32Float_Stencil8': ('depth32', 1),
    'RG32Uint': ('uint32', 2), 'RG32Sint': ('sint32', 2), 'RG32Float': ('float32', 2),
    'RGBA16Unorm': ('unorm16', 4), 'RGBA16Snorm': ('snorm16', 4), 'RGBA16Uint': ('uint16', 4),
    'RGBA16Sint': ('sint16', 4), 'RGBA16Float': ('float16', 4),
    'RGBA32Uint': ('uint32', 4), 'RGBA32Sint': ('sint32', 4), 'RGBA32Float': ('float32', 4),
}

# Decoders whose values are already display-ready 0..255.
DIRECT8 = {'unorm8', 'bgra8'}
# Decoders whose values are 0..1 (shown scaled, not normalized, unless single-channel).
UNIT = {'unorm16', 'snorm8', 'snorm16', 'rgb10a2', 'bgr10a2'}
FLOAT = {'float16', 'float32', 'rg11b10', 'rgb9e5'}
STRUCT = {'uint8': 'B', 'sint8': 'b', 'snorm8': 'b', 'uint16': 'H', 'sint16': 'h', 'unorm16': 'H',
          'snorm16': 'h', 'depth16': 'H', 'uint32': 'I', 'sint32': 'i', 'float16': 'e',
          'float32': 'f', 'depth32': 'f'}


# ---------------------------------------------------------------- PNG output

def png_bytes(width, height, channels, data):
    """data: bytes of width*height*channels 8-bit samples, channels 1 (gray) or 3 (RGB)."""
    stride = width * channels
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        rows += data[y * stride:(y + 1) * stride]

    def chunk(tag, body):
        c = struct.pack('>I', len(body)) + tag + body
        return c + struct.pack('>I', zlib.crc32(tag + body) & 0xffffffff)

    color = 0 if channels == 1 else 2
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, color, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(rows), 6))
            + chunk(b'IEND', b''))


def write_png(path, width, height, channels, data):
    with open(path, 'wb') as f:
        f.write(png_bytes(width, height, channels, data))


def thumbnail(width, height, channels, data, limit):
    """Nearest-neighbour shrink so the longer side is at most limit."""
    step = max(1, math.ceil(max(width, height) / limit))
    if step == 1:
        return width, height, data
    tw, th = math.ceil(width / step), math.ceil(height / step)
    out = bytearray()
    stride = width * channels
    for y in range(0, height, step):
        row = data[y * stride:(y + 1) * stride]
        if channels == 1:
            out += row[::step]
        else:
            px = bytearray(tw * 3)
            for c in range(3):
                px[c::3] = row[c::3][::step]
            out += px
    return tw, th, bytes(out)


# ---------------------------------------------------------------- decoding

def small_float(mant, exp, mbits):
    if exp == 0:
        return mant / (1 << mbits) * 2.0 ** -14
    if exp == 31:
        return math.inf if mant == 0 else math.nan
    return 2.0 ** (exp - 15) * (1 + mant / (1 << mbits))


def decode(raw, width, height, decoder, channels):
    """Returns a list of per-channel value lists (row-major)."""
    n = width * height
    if decoder in DIRECT8:
        raw = raw[:n * channels]
        planes = [list(raw[c::channels]) for c in range(channels)]
        if decoder == 'bgra8':
            planes[0], planes[2] = planes[2], planes[0]
        return planes
    if decoder in ('rgb10a2', 'bgr10a2', 'rgb10a2uint', 'rg11b10', 'rgb9e5'):
        words = struct.unpack('<%dI' % n, raw[:n * 4])
        if decoder == 'rg11b10':
            r = [small_float(w & 63, (w >> 6) & 31, 6) for w in words]
            g = [small_float((w >> 11) & 63, (w >> 17) & 31, 6) for w in words]
            b = [small_float((w >> 22) & 31, (w >> 27) & 31, 5) for w in words]
            return [r, g, b]
        if decoder == 'rgb9e5':
            out = [[], [], []]
            for w in words:
                scale = 2.0 ** (((w >> 27) & 31) - 15 - 9)
                out[0].append((w & 511) * scale)
                out[1].append(((w >> 9) & 511) * scale)
                out[2].append(((w >> 18) & 511) * scale)
            return out
        div = 1 if decoder == 'rgb10a2uint' else 1023.0
        lo = [(w & 1023) / div for w in words]
        mid = [((w >> 10) & 1023) / div for w in words]
        hi = [((w >> 20) & 1023) / div for w in words]
        a = [(w >> 30) / (1 if decoder == 'rgb10a2uint' else 3.0) for w in words]
        return [hi, mid, lo, a] if decoder == 'bgr10a2' else [lo, mid, hi, a]
    code = STRUCT[decoder]
    size = struct.calcsize(code)
    values = struct.unpack('<%d%s' % (n * channels, code), raw[:n * channels * size])
    if decoder == 'unorm16':
        values = [v / 65535.0 for v in values]
    elif decoder in ('snorm8', 'snorm16'):
        m = 127.0 if decoder == 'snorm8' else 32767.0
        values = [(max(v / m, -1.0) + 1) / 2 for v in values]
    elif decoder == 'depth16':
        values = [v / 65535.0 for v in values]
    return [list(values[c::channels]) for c in range(channels)]


def finite_range(values, lo_pct=None, hi_pct=None):
    vals = [v for v in values if math.isfinite(v)]
    if not vals:
        return 0.0, 1.0
    if lo_pct is None:
        return min(vals), max(vals)
    step = max(1, len(vals) // 1000000)
    sample = sorted(vals[::step])
    last = len(sample) - 1
    return sample[int(lo_pct * last)], sample[int(hi_pct * last)]


def to8(values, lo, hi):
    span = hi - lo
    out = bytearray(len(values))
    if span <= 0:
        return out
    k = 255.0 / span
    for i, v in enumerate(values):
        if v != v:
            continue
        x = (v - lo) * k
        out[i] = 0 if x <= 0 else 255 if x >= 255 else int(x + 0.5)
    return out


def unit8(values):
    return bytes(0 if v <= 0 else 255 if v >= 1 else int(v * 255 + 0.5) for v in values)


def tonemap8(values, mode, gamma, lo=0.0, hi=1.0):
    inv = 1.0 / gamma if gamma > 0 else 1.0
    out = bytearray(len(values))
    span = (hi - lo) or 1.0
    for i, v in enumerate(values):
        if v != v:
            continue
        if mode == 'normalize':
            x = (v - lo) / span
        elif mode == 'clamp':
            x = v
        else:
            x = v / (1.0 + v) if v > 0 else 0.0
        x = 0.0 if x <= 0 else 1.0 if x >= 1 else x ** inv
        out[i] = int(x * 255 + 0.5)
    return out


def interleave(planes, n):
    rgb = bytearray(n * 3)
    for c in range(3):
        rgb[c::3] = planes[c] if c < len(planes) else bytes(n)
    return bytes(rgb)


def convert(job):
    """Writes the PNG(s) for one entry; returns {png, variants, thumb} or {error}."""
    entry, root, opts = job
    name = entry.get('formatName')
    width, height = entry['width'], entry['height']
    fmt = FORMATS.get(name)
    if not fmt:
        return {'error': 'no converter for %s' % name}
    decoder, channels = fmt
    raw_path = os.path.join(root, entry['file'])
    try:
        with open(raw_path, 'rb') as f:
            raw = f.read()
    except OSError as err:
        return {'error': str(err)}
    need = width * height * entry['bytesPerPixel']
    if len(raw) < need:
        return {'error': 'short file: %d of %d bytes' % (len(raw), need)}
    n = width * height
    planes = decode(raw, width, height, decoder, channels)
    base = os.path.splitext(raw_path)[0]
    results = []

    if channels == 1:
        if decoder == 'unorm8':
            out = bytes(planes[0])
        else:
            lo, hi = finite_range(planes[0])
            out = to8(planes[0], lo, hi)
        results.append(('', 1, out, '' if decoder == 'unorm8' else 'min..max %.6g..%.6g' % (lo, hi)))
        if decoder in ('depth16', 'depth32'):
            plo, phi = finite_range(planes[0], 0.01, 0.99)
            results.append(('_p1-99', 1, to8(planes[0], plo, phi), 'p1..p99 %.6g..%.6g' % (plo, phi)))
    else:
        color = planes[:3]
        if decoder in DIRECT8:
            out = [bytes(p) for p in color]
            note = ''
        elif decoder in FLOAT and channels >= 3:
            lo = hi = None
            if opts.float == 'normalize':
                lo, hi = finite_range([v for p in color for v in p])
                out = [tonemap8(p, 'normalize', opts.gamma, lo, hi) for p in color]
                note = 'normalize %.6g..%.6g' % (lo, hi)
            else:
                out = [tonemap8(p, opts.float, opts.gamma) for p in color]
                lo, hi = finite_range([v for p in color for v in p])
                note = '%s, range %.6g..%.6g' % (opts.float, lo, hi)
        elif decoder in UNIT:
            out = [unit8(p) for p in color]
            note = ''
        else:
            # Two-channel data (motion vectors, normals, ...) and integers: each channel min..max.
            out, ranges = [], []
            for p in color:
                lo, hi = finite_range(p)
                out.append(to8(p, lo, hi))
                ranges.append('%.4g..%.4g' % (lo, hi))
            note = 'per channel ' + ' / '.join(ranges)
        results.append(('', 3, interleave(out, n), note))

    written = []
    for suffix, ch, data, note in results:
        path = base + suffix + '.png'
        write_png(path, width, height, ch, data)
        tw, th, tdata = thumbnail(width, height, ch, data, opts.thumb)
        tpath = os.path.join(root, 'thumbs', os.path.basename(base) + suffix + '.png')
        write_png(tpath, tw, th, ch, tdata)
        written.append({'png': os.path.basename(path), 'thumb': 'thumbs/' + os.path.basename(tpath),
                        'note': (suffix.lstrip('_') + ' ' + note).strip()})
    return {'outputs': written}


# ---------------------------------------------------------------- index

def load_index(root):
    path = os.path.join(root, 'index.json')
    try:
        with open(path) as f:
            return json.load(f), 'index.json'
    except (OSError, ValueError):
        pass
    entries = []
    with open(os.path.join(root, 'index.jsonl')) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    pass  # a line cut off by a kill
    entries.sort(key=lambda e: e['seq'])
    return entries, 'index.jsonl (index.json missing: the frame did not finish writing)'


def pass_summary(e):
    parts = []
    if e.get('draws') is not None:
        parts.append('%d draws' % e['draws'])
    if e.get('renderTarget'):
        parts.append('rt %dx%d' % tuple(e['renderTarget']))
    tg = e.get('threadgroups')
    if tg is not None:
        parts.append('groups ' + (tg if isinstance(tg, str) else 'x'.join(map(str, tg))))
    if e.get('threadsPerThreadgroup'):
        parts.append('tg ' + 'x'.join(map(str, e['threadsPerThreadgroup'])))
    if e.get('viewports'):
        parts.append('viewports ' + ' '.join('[%g,%g %gx%g z%g..%g]' % tuple(v) for v in e['viewports']))
    return ' · '.join(parts)


def pipeline_text(p):
    return p.get('name') or p.get('label') or p.get('ptr')


def pipeline_html(p):
    """Pipeline name with links to its MSL per stage and its binding map."""
    links = ['<a href="%s">%s</a>' % (html.escape(f), html.escape(stage))
             for stage, f in (p.get('shaders') or {}).items() if f]
    missing = [stage for stage, f in (p.get('shaders') or {}).items() if not f]
    if p.get('bindingMap'):
        links.append('<a href="%s">bindings</a>' % html.escape(p['bindingMap']))
    text = html.escape(str(pipeline_text(p)))
    if p.get('name') or p.get('label'):
        text += ' <span class="m">%s</span>' % html.escape(str(p.get('ptr')))
    if missing:
        links.append('<span class="m">no MSL: %s</span>' % html.escape(', '.join(missing)))
    return text + (' [%s]' % ' '.join(links) if links else '')


def tex_text(t):
    if not t:
        return 'none'
    if t.get('dead'):
        return '%s (destroyed)' % t.get('ptr')
    if t.get('unverified'):
        return '%s (partially bound, not checked)' % t.get('ptr')
    size = t.get('size') or [0, 0, 0, 0]
    text = '%s %dx%d' % (t.get('ptr'), size[0], size[1])
    if size[2] > 1:
        text += 'x%d' % size[2]
    if size[3] > 1:
        text += '[%d]' % size[3]
    text += ' %s mips %s' % (t.get('formatName') or t.get('format'), t.get('mips'))
    if t.get('parentTexture'):
        text += ' · view of %s (%s) levels %s slices %s' % (
            t['parentTexture'], t.get('parentFormatName') or t.get('parentFormat'),
            t.get('levels'), t.get('slices'))
    if t.get('buffer'):
        text += ' · buffer %s+%s' % (t['buffer'], t.get('bufferOffset'))
    if t.get('label'):
        text = '%s · %s' % (t['label'], text)
    return text


class Writers:
    """Which earlier entry wrote a texture (by pointer, or the parent of a view)."""

    def __init__(self, entries):
        self.by_ptr = {}
        for e in entries:
            if e.get('kind') == 'input':
                continue
            if e.get('kind') == 'transfer':
                t = (e.get('dst') or {}).get('texture')
                what = e.get('use') or 'transfer'
            else:
                t = e.get('texture')
                what = '%s %s' % (e.get('kind'), e.get('slot'))
            if not t:
                continue
            for ptr in (t.get('ptr'), t.get('parentTexture')):
                if ptr:
                    self.by_ptr.setdefault(ptr, []).append((e.get('pass', -1), e['seq'], what))

    def last_before(self, t, pass_no):
        best = None
        for ptr in (t.get('ptr'), t.get('parentTexture')):
            for w in self.by_ptr.get(ptr, []):
                if w[0] < pass_no and (best is None or w[0] > best[0] or (w[0] == best[0] and w[1] > best[1])):
                    best = w
        return best


def inputs_html(inputs, samplers, pass_no, results, writers):
    rows = []
    for i in inputs or []:
        t = i.get('texture')
        thumb = ''
        res = results.get(i.get('inputSeq'), {}) if i.get('inputSeq') is not None else {}
        if res.get('outputs'):
            o = res['outputs'][0]
            thumb = '<a href="%s"><img loading="lazy" src="%s"></a>' % (html.escape(o['png']), html.escape(o['thumb']))
        where = '%s%s · set %s binding %s[%s] · %s%s' % (
            i.get('stage'), i.get('index'), i.get('set'), i.get('binding'), i.get('element'), i.get('type'),
            ' (argument buffer)' if i.get('argBuffer') else '')
        extra = ''
        if i.get('inputSeq') is not None:
            extra += ' · dumped #%04d' % i['inputSeq']
        if t and not t.get('dead') and not t.get('unverified'):
            w = writers.last_before(t, pass_no)
            extra += (' · last written by #%04d (pass %s, %s)' % (w[1], w[0], w[2])) if w else ' · not written earlier in this dump'
        rows.append('<div class="in">%s<div><b>%s</b><div class="m">%s%s</div></div></div>'
                    % (thumb, html.escape(where), html.escape(tex_text(t)), html.escape(extra)))
    if samplers:
        rows.append('<div class="m">samplers: %s</div>' % html.escape(', '.join(
            '%s%s=%s' % (s.get('stage'), s.get('index'), 'constexpr' if s.get('constexpr') else s.get('ptr'))
            for s in samplers)))
    return ''.join(rows)


def regions_text(e):
    parts = []
    for r in (e.get('regions') or [])[:4]:
        parts.append(json.dumps(r, separators=(',', ':')))
    more = (e.get('regionCount') or 0) - len(parts)
    return ' '.join(parts) + (' (+%d more)' % more if more > 0 else '')


def endpoint_text(x):
    if not x:
        return 'none'
    if x.get('texture'):
        return 'texture ' + tex_text(x['texture'])
    return 'buffer %s+%s (%s bytes)%s' % (x.get('buffer'), x.get('offset'), x.get('length'),
                                          (' ' + x['label']) if x.get('label') else '')


CSS = """
:root { color-scheme: light dark; --bg:#f5f5f4; --card:#fff; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4; --bad:#b91c1c; }
@media (prefers-color-scheme: dark) { :root { --bg:#1c1917; --card:#292524; --fg:#f5f5f4; --muted:#a8a29e; --line:#44403c; --bad:#f87171; } }
body { margin:0; padding:16px; background:var(--bg); color:var(--fg); font:13px/1.4 -apple-system, system-ui, sans-serif; }
h1 { font-size:18px; margin:0 0 4px; }
.top { color:var(--muted); margin-bottom:12px; }
.pass { border-top:1px solid var(--line); padding:10px 0; }
.ph { margin-bottom:6px; }
.ph b { font-variant-numeric:tabular-nums; }
.ph .k { display:inline-block; padding:0 6px; border-radius:4px; background:var(--line); margin:0 4px; }
.ph .m { color:var(--muted); }
.cards { display:flex; flex-wrap:wrap; gap:8px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:6px; padding:6px; width:330px; }
.card img { display:block; max-width:320px; max-height:240px; image-rendering:pixelated; background:repeating-conic-gradient(#8884 0 25%, transparent 0 50%) 0 0/16px 16px; }
.card .t { font-weight:600; }
.card .m { color:var(--muted); word-break:break-all; }
.card .bad { color:var(--bad); }
.draws { margin:4px 0 8px; }
.draw { margin:2px 0 6px; }
.in { display:flex; gap:8px; align-items:flex-start; margin:2px 0 2px 12px; }
.in img { max-width:96px; max-height:72px; image-rendering:pixelated; background:repeating-conic-gradient(#8884 0 25%, transparent 0 50%) 0 0/16px 16px; }
.in .m, .draw .m, .ph .m { word-break:break-all; }
.hide-skipped .skipped { display:none; }
label { user-select:none; }
"""


def write_html(root, entries, source, results):
    passes = []
    for e in entries:
        if not passes or passes[-1][0] != e.get('pass'):
            passes.append((e.get('pass'), []))
        passes[-1][1].append(e)
    skipped = sum(1 for e in entries if e.get('status') not in ('ok', 'meta'))
    writers = Writers(entries)
    total = sum(e['bytesPerRow'] * e['height'] for e in entries if e.get('status') == 'ok')
    out = ['<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           '<title>%s</title><style>%s</style>' % (html.escape(os.path.basename(os.path.abspath(root))), CSS),
           '<h1>%s</h1>' % html.escape(os.path.abspath(root)),
           '<div class="top">%d passes · %d entries · %d not dumped · %.1f MB · from %s · '
           '<label><input type="checkbox" onchange="document.body.classList.toggle(\'hide-skipped\', this.checked)"> hide skipped</label></div>'
           % (len(passes), len(entries), skipped, total / 1048576.0, html.escape(source))]
    for pno, group in passes:
        # Input entries come first in a pass; the pass's own kind is that of its other entries.
        e0 = next((e for e in group if e.get('kind') != 'input'), group[0])
        pipes = ', '.join(pipeline_html(p) for p in e0.get('pipelines', []))
        cls = 'pass skipped' if all(e.get('status') not in ('ok', 'meta') for e in group) else 'pass'
        out.append('<div class="%s"><div class="ph"><b>pass %s</b><span class="k">%s</span>%s <span class="m">%s · %s</span>%s</div>'
                   % (cls, pno, html.escape(e0['kind']), html.escape(str(e0.get('encoder') or '')),
                      html.escape(str(e0.get('use') or '')), html.escape(pass_summary(e0)),
                      ('<div class="m">pipelines: %s</div>' % pipes) if pipes else ''))
        if e0.get('kind') == 'transfer':
            out.append('<div class="m">src: %s<br>dst: %s<br>%d regions: %s</div>' % (
                html.escape(endpoint_text(e0.get('src'))), html.escape(endpoint_text(e0.get('dst'))),
                e0.get('regionCount') or 0, html.escape(regions_text(e0))))
        # Bindings ride on the pass's first entry (drawList for render passes, inputs for dispatches).
        info = next((e for e in group if 'drawList' in e or 'inputs' in e), None)
        if info is not None:
            pass_no = info.get('pass', -1)
            out.append('<details class="draws"%s><summary>bindings</summary>' % (' open' if len(info.get('drawList') or [1]) <= 4 else ''))
            if 'drawList' in info:
                for d in info['drawList']:
                    out.append('<div class="draw">draw %s <span class="m">pipeline %s</span>%s</div>' % (
                        d.get('draw'), html.escape(str(d.get('pipeline'))),
                        inputs_html(d.get('inputs'), d.get('samplers'), pass_no, results, writers)))
                if info.get('drawsDropped'):
                    out.append('<div class="m">%d more draws not listed</div>' % info['drawsDropped'])
            else:
                out.append(inputs_html(info.get('inputs'), info.get('samplers'), pass_no, results, writers))
            out.append('</details>')
        out.append('<div class="cards">')
        for e in group:
            if e.get('slot') is None:
                continue
            res = results.get(e['seq'], {})
            tex = e.get('texture', {})
            meta = '%s · %dx%d %s (%s) · mip %s slice %s' % (
                html.escape(str(tex.get('label') or tex.get('ptr'))), e['width'], e['height'],
                html.escape(str(e.get('formatName') or '?')), e.get('format'), e.get('level'), e.get('slice'))
            title = e['slot'] if e.get('kind') != 'input' else 'input ' + e['slot']
            card = ['<div class="card%s"><div class="t">#%04d %s</div>' % ('' if e.get('status') == 'ok' else ' skipped', e['seq'], html.escape(title))]
            for o in res.get('outputs', []):
                card.append('<a href="%s"><img loading="lazy" src="%s"></a>' % (html.escape(o['png']), html.escape(o['thumb'])))
                if o['note']:
                    card.append('<div class="m">%s</div>' % html.escape(o['note']))
            if e.get('status') != 'ok':
                card.append('<div class="bad">%s</div>' % html.escape(str(e.get('skipped') or e.get('status') or 'not written')))
            if res.get('error'):
                card.append('<div class="bad">%s</div>' % html.escape(res['error']))
            if e.get('kind') == 'input':
                meta += ' · copied %s%s' % (e.get('when', ''), ', also written by this pass' if e.get('alsoWritten') else '')
            if tex.get('parentTexture'):
                meta += ' · view of %s' % html.escape(tex['parentTexture'])
            card.append('<div class="m">%s</div></div>' % meta)
            out.append(''.join(card))
        out.append('</div></div>')
    with open(os.path.join(root, 'index.html'), 'w') as f:
        f.write('\n'.join(out))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dump_dir')
    ap.add_argument('--float', choices=('reinhard', 'clamp', 'normalize'), default='reinhard',
                    help='how float/HDR color is mapped to 8 bits (default reinhard)')
    ap.add_argument('--gamma', type=float, default=2.2,
                    help='display gamma applied after tonemapping float color (1 for data already in gamma space)')
    ap.add_argument('--thumb', type=int, default=320, help='contact-sheet thumbnail size')
    ap.add_argument('-j', '--jobs', type=int, default=os.cpu_count() or 4)
    opts = ap.parse_args()
    root = opts.dump_dir
    entries, source = load_index(root)
    os.makedirs(os.path.join(root, 'thumbs'), exist_ok=True)
    jobs = [e for e in entries if e.get('status') in ('ok', 'gpu-error') and e.get('file')]
    results = {}
    with ProcessPoolExecutor(max_workers=max(1, opts.jobs)) as pool:
        for e, res in zip(jobs, pool.map(convert, [(e, root, opts) for e in jobs])):
            results[e['seq']] = res
            if res.get('error'):
                print('#%04d %s: %s' % (e['seq'], e['file'], res['error']), file=sys.stderr)
    write_html(root, entries, source, results)
    print('%d entries, %d converted, contact sheet %s' % (
        len(entries), sum(1 for r in results.values() if 'outputs' in r), os.path.join(root, 'index.html')))


if __name__ == '__main__':
    main()
