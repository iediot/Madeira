#!/usr/bin/env python3
"""Execute production install removal/record helpers on temporary fixtures."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'app/Madeira/SteamInstall.swift').read_text()

def function(marker):
    start = source.index(marker)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

delete = function('nonisolated static func delete(')
# Workshop removal is unchanged and unrelated; exercise preceding production code.
delete = delete[:delete.index('        // Workshop items')] + '\n    }'
helpers = '\n'.join(function(marker) for marker in (
    'nonisolated static func safeFolderName(',
    'private static func otherInstall(', 'private static func record('))
harness = '''import Foundation
enum SteamInstallFiles {
''' + delete + '\n' + helpers + r'''
}
let fm = FileManager.default
let root = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
defer { try? fm.removeItem(at: root) }
try fm.createDirectory(at: root.appendingPathComponent("common/Game"), withIntermediateDirectories: true)
func record(_ id: Int, _ folder: String, _ held: [Int], _ shared: [Int:Int] = [:]) throws {
    let depots = held.map { "\"\($0)\" { \"manifest\" \"1\" }" }.joined(separator: " ")
    let lent = shared.map { "\"\($0.key)\" \"\($0.value)\"" }.joined(separator: " ")
    let data = "\"AppState\" { \"appid\" \"\(id)\" \"installdir\" \"\(folder)\" \"StateFlags\" \"4\" \"InstalledDepots\" { \(depots) } \"SharedDepots\" { \(lent) } }"
    try data.write(to: root.appendingPathComponent("appmanifest_\(id).acf"), atomically: true, encoding: .utf8)
}
func exists(_ path: String) -> Bool { fm.fileExists(atPath: root.appendingPathComponent(path).path) }
try record(100,"Game",[1,9,12],[9:200,12:300])
try record(200,"DifferentOwnerFolder",[9])
try record(300,"ActualOtherGame",[12,13])
SteamInstallFiles.delete(appID:100,folderName:"Game",steamApps:root)
assert(!exists("appmanifest_100.acf"))
assert(!exists("appmanifest_200.acf"))
assert(exists("appmanifest_300.acf"))
assert(!exists("common/Game"))
try fm.createDirectory(at:root.appendingPathComponent("common/Game"),withIntermediateDirectories:true)
try "progress".write(to:root.appendingPathComponent("common/Game/save.txt"),atomically:true,encoding:.utf8)
try record(100,"Game",[1])
try record(101,"Game",[99])
SteamInstallFiles.delete(appID:100,folderName:"Game",steamApps:root)
assert(!exists("appmanifest_100.acf") && exists("appmanifest_101.acf"))
assert(try String(contentsOf:root.appendingPathComponent("common/Game/save.txt"),encoding:.utf8)=="progress")
print("PASS: cross-folder shared-only owner removed; real owner install and shared-folder saves preserved")
'''
harness = harness.replace('assert(try String(contentsOf:', 'let progress = try String(contentsOf:')
harness = harness.replace(',encoding:.utf8)=="progress")', ',encoding:.utf8)\nassert(progress == "progress")')
with tempfile.TemporaryDirectory(prefix='madeira-shared-removal-') as directory:
    main = Path(directory) / 'main.swift'
    main.write_text(harness)
    exe = Path(directory) / 'test'
    subprocess.run(['xcrun','swiftc','-sanitize=address',str(root/'app/Madeira/SteamKeyValues.swift'),
                    str(main),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
