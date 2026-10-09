#!/usr/bin/env python3
"""Offline half of MoltenVK's Madeira MSL cache. Keys are opaque filenames."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import fcntl

ROOT = "Library/Caches/mvk-binary-archive"
BUNDLE = "com.iediot.madeora"
KEY = re.compile(r"[0-9a-f]{16}")


def flags(options):
    if options.get("schemaVersion") != 1:
        raise ValueError("unsupported MSL options schema")
    version = int(options["languageVersion"])
    major, minor = version >> 16, version & 0xffff
    if not 1 <= major <= 4 or minor > 9:
        raise ValueError(f"invalid MTLLanguageVersion: {version}")
    # Metal 3+ uses unified language names; ios-metal3.x is rejected by metal.
    platform = "ios-" if major < 3 else ""
    # The SDK's own iOS version is the default target, and a device on an older
    # iOS refuses the library: 11,557 Detroit libraries built as
    # air64_v29-apple-ios27.0.0 all failed to load on iPadOS 26. Target the oldest
    # system the language version allows (MADEIRA_MSL_IOS_MIN overrides).
    ios_min = os.environ.get('MADEIRA_MSL_IOS_MIN') or ('26.0' if major >= 4 else '17.0')
    result = [f"-std={platform}metal{major}.{minor}", f"-mios-version-min={ios_min}"]
    if "mathMode" in options:
        result += ["-fmetal-math-mode=" + {0: "safe", 1: "relaxed", 2: "fast"}[options["mathMode"]],
                   "-fmetal-math-fp32-functions=" + {0: "fast", 1: "precise"}[options["mathFloatingPointFunctions"]]]
    else:
        result += ["-ffast-math" if options["fastMathEnabled"] else "-fno-fast-math"]
    if options["preserveInvariance"]:
        result += ["-fpreserve-invariance"]
    result += [{0: "-O2", 1: "-Os"}[options.get("optimizationLevel", 0)]]
    for name, value in sorted(options["preprocessorMacros"].items()):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name) or not isinstance(value, str):
            raise ValueError("invalid macro (expected name and exact textual value)")
        result += [f"-D{name}={value}"]
    return result


def run(command, **kwargs):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{command!r}\n{result.stdout.decode(errors='replace')}")
    return result.stdout


def atomic(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        f.write(data)
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def fingerprint(source, toolchain):
    # This is only freshness metadata, never the runtime library key.
    return hashlib.sha256(source.read_bytes() + b'\0' + source.with_suffix('.json').read_bytes()
                          + b'\0' + toolchain.encode() + Path(__file__).read_bytes()).hexdigest()


def compile_one(source, toolchain):
    source = Path(source)
    if not KEY.fullmatch(source.stem):
        raise ValueError(f"invalid runtime key filename: {source.name}")
    options = json.loads(source.with_suffix('.json').read_text())
    if options['key'] != source.stem:
        raise ValueError("JSON key differs from source filename")
    output = source.parent.parent / 'lib' / (source.stem + '.metallib')
    output.parent.mkdir(parents=True, exist_ok=True)
    stamp = output.with_suffix('.stamp')
    digest = fingerprint(source, toolchain)
    if output.exists() and stamp.exists() and stamp.read_text() == digest:
        return False
    with tempfile.TemporaryDirectory(dir=output.parent) as scratch:
        air = Path(scratch) / 'shader.air'
        lib = Path(scratch) / output.name
        run(['xcrun', '-sdk', 'iphoneos', 'metal', *flags(options), '-fmodules-cache-path=' + str(source.parent.parent / 'module-cache'), '-c', str(source), '-o', str(air)])
        run(['xcrun', '-sdk', 'iphoneos', 'metallib', str(air), '-o', str(lib)])
        lib.replace(output)
        atomic(stamp, digest.encode())
    return True


def file_names(document):
    """Read devicectl's structured listing, accepting path/name field variants."""
    result = document.get('result', {})
    entries = result.get('files')
    if entries is None:
        raise ValueError("devicectl JSON has no result.files list")
    names = set()
    for entry in entries:
        if isinstance(entry, str):
            names.add(Path(entry.rstrip('/')).name)
        else:
            path = entry.get('relativePath') or entry.get('path') or entry.get('name')
            if path:
                names.add(Path(path.rstrip('/')).name)
    return names


def device_command(device, *args):
    return ['xcrun', 'devicectl', 'device', *args, '--device', device,
            '--domain-type', 'appDataContainer', '--domain-identifier', BUNDLE,
            # A whole-folder copy moves thousands of small files one at a time (about
            # half an hour for Detroit's ~23,000): 120 s cut the pull off half way.
            '--quiet', '--timeout', '3600']


def listing(device, remote):
    with tempfile.TemporaryDirectory() as scratch:
        out = Path(scratch) / 'files.json'
        run(device_command(device, 'info', 'files', '--subdirectory', remote,
                           '--no-recurse', '--json-output', str(out)))
        return file_names(json.loads(out.read_text()))


def log_failure(log, message):
    with log.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(message + '\n')


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        source = Path(sys.argv[2])
        try:
            compile_one(source, os.environ['MADEIRA_MSL_TOOLCHAIN'])
        except Exception as error:
            log_failure(source.parent.parent / 'failures.log', f'{source.name}: {error}')
        return 0  # xargs must continue after an individual shader failure.
    if len(sys.argv) not in (2, 3) or sys.argv[1].startswith('-'):
        print('usage: tools/precompile-msl.sh <device-id> [game-dir-hash]', file=sys.stderr)
        return 2
    device = sys.argv[1]
    games = [sys.argv[2]] if len(sys.argv) == 3 else sorted(n for n in listing(device, ROOT) if KEY.fullmatch(n))
    if any(not KEY.fullmatch(game) for game in games):
        raise ValueError('game directory hash must be 16 lowercase hexadecimal digits')
    toolchain = run(['xcrun', '-sdk', 'iphoneos', 'metal', '--version']).decode() + run(['xcrun', '-sdk', 'iphoneos', '--show-sdk-version']).decode()
    try:
        workers = max(1, int(run(['sysctl', '-n', 'hw.ncpu'])))
    except RuntimeError:
        workers = os.cpu_count() or 1  # Restricted host environments can deny sysctl.
    compiled = failed = pushed = unchanged = 0
    for game in games:
        mirror = Path.home() / 'Library/Caches/madeira-msl' / game
        mirror.mkdir(parents=True, exist_ok=True)
        # Prevent concurrent invocations from publishing competing output/stamps.
        with (mirror / '.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            log = mirror / 'failures.log'
            remote = f'{ROOT}/{game}/msl'
            try:
                with tempfile.TemporaryDirectory(dir=mirror) as scratch:
                    pull = Path(scratch) / 'src'
                    local_src = mirror / 'src'
                    local_src.mkdir(exist_ok=True)
                    # MADEIRA_MSL_SKIP_PULL=1: compile and push what the local mirror
                    # already holds (filled by an earlier or a manual pull).
                    if os.environ.get('MADEIRA_MSL_SKIP_PULL') == '1':
                        pull.mkdir()
                    else:
                        run(device_command(device, 'copy', 'from', '--source', remote + '/src', '--destination', str(pull)))
                    # Handle either directory-copy layout without flattening arbitrary paths.
                    incoming = pull / 'src' if (pull / 'src').is_dir() else pull
                    for item in incoming.iterdir():
                        if item.suffix in ('.metal', '.json') and KEY.fullmatch(item.stem):
                            target = local_src / item.name
                            data = item.read_bytes()
                            if not target.exists() or target.read_bytes() != data:
                                atomic(target, data)
                sources = sorted(local_src.glob('*.metal'))
                todo = []
                for source in sources:
                    output = mirror / 'lib' / (source.stem + '.metallib')
                    stamp = output.with_suffix('.stamp')
                    try:
                        fresh = output.exists() and stamp.exists() and stamp.read_text() == fingerprint(source, toolchain)
                    except OSError:
                        fresh = False
                    if fresh:
                        unchanged += 1
                    else:
                        todo.append(source)
                if todo:
                    run(['xargs', '-0', '-n', '1', '-P', str(workers), sys.executable, str(Path(__file__).resolve()), '--worker'],
                        input=b''.join(os.fsencode(s) + b'\0' for s in todo),
                        env={**os.environ, 'MADEIRA_MSL_TOOLCHAIN': toolchain})
                valid = []
                todo_set = set(todo)
                for source in sources:
                    output = mirror / 'lib' / (source.stem + '.metallib')
                    try:
                        fresh = output.exists() and output.with_suffix('.stamp').read_text() == fingerprint(source, toolchain)
                    except OSError:
                        fresh = False
                    if not fresh:
                        failed += 1
                    else:
                        valid.append(output)
                        if source in todo_set:
                            compiled += 1
                # Check remote presence too, so an app reinstall/cache eviction gets repaired.
                remote_files = (listing(device, remote + '/lib')
                                if 'lib' in listing(device, remote) else set())
                receipt = mirror / ('pushed-' + hashlib.sha256(device.encode()).hexdigest()[:16] + '.json')
                sent = json.loads(receipt.read_text()) if receipt.exists() else {}
                pending = []
                for output in valid:
                    digest = hashlib.sha256(output.read_bytes()).hexdigest()
                    if output.name not in remote_files or sent.get(output.name) != digest:
                        pending.append((output, digest))
                # Copy only changed libraries in bounded directory batches. devicectl
                # supports directories and merges by default (never remove existing).
                for offset in range(0, len(pending), 256):
                    batch = pending[offset:offset + 256]
                    try:
                        with tempfile.TemporaryDirectory(dir=mirror) as scratch:
                            delta = Path(scratch) / 'lib'
                            delta.mkdir()
                            for output, _ in batch:
                                os.link(output, delta / output.name)
                            run(device_command(device, 'copy', 'to', '--source', str(delta), '--destination', remote + '/lib'))
                        sent.update((output.name, digest) for output, digest in batch)
                        atomic(receipt, json.dumps(sent, sort_keys=True).encode())
                        pushed += len(batch)
                    except Exception as error:
                        failed += len(batch)
                        log_failure(log, str(error))
            except Exception as error:
                failed += 1
                log_failure(log, str(error))
    print(f'MSL: {len(games)} games, {compiled} compiled, {unchanged} up-to-date, {pushed} pushed, {failed} failed. '
          'Mirrors and failures.log: ~/Library/Caches/madeira-msl/<hash>/')
    return 1 if failed else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(f'MSL: {error}', file=sys.stderr)
        sys.exit(1)
