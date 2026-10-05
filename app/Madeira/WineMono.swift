import Foundation
import SwiftUI

/// Wine Mono: the .NET runtime Wine runs .NET programs with (Terraria and other
/// XNA/.NET games stopped with "Wine Mono is not installed").
///
/// The official 42 MB wine-mono-<version>-x86.tar.xz ships in the app
/// (build/wine-mono/fetch.sh). The first time a .NET program is started it is
/// unpacked (232 MB) into Application Support, and linked into the prefix as
/// C:\windows\mono\mono-2.0, the folder mscoree searches first; the link is
/// checked on every launch because the data container can move. Players who
/// never start a .NET program never pay the disk space.
final class WineMono: ObservableObject {
    static let shared = WineMono()
    static let version = "11.0.0"

    /// Non-nil while the runtime is being unpacked; the overlay shows it.
    @Published private(set) var status: String?

    private init() {}

    private static var archive: URL? {
        Bundle.main.url(forResource: "wine-mono-\(version)-x86", withExtension: "tar.xz")
    }
    private static var installRoot: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("wine-mono", isDirectory: true)
    }
    private static var installDir: URL { installRoot.appendingPathComponent("wine-mono-\(version)", isDirectory: true) }
    private static var marker: URL { installRoot.appendingPathComponent(".installed-\(version)") }

    static var isInstalled: Bool {
        FileManager.default.fileExists(atPath: marker.path)
    }

    // MARK: - Does this launch need it?

    /// A PE file with a CLR (.NET) header: data directory 14 is non-empty.
    static func isDotNet(_ url: URL) -> Bool {
        guard let h = try? FileHandle(forReadingFrom: url) else { return false }
        defer { try? h.close() }
        guard let dos = try? h.read(upToCount: 64), dos.count == 64, dos[0] == 0x4D, dos[1] == 0x5A else { return false }
        let peOff = UInt64(dos.withUnsafeBytes { $0.load(fromByteOffset: 0x3C, as: UInt32.self) })
        guard peOff < 0x10000, (try? h.seek(toOffset: peOff)) != nil,
              let pe = try? h.read(upToCount: 24 + 240), pe.count >= 24 + 112,
              pe[0] == 0x50, pe[1] == 0x45 else { return false }
        let magic = pe.withUnsafeBytes { $0.load(fromByteOffset: 24, as: UInt16.self) }
        // Data directories start at optional-header offset 96 (PE32) or 112 (PE32+).
        let dirs = 24 + (magic == 0x20B ? 112 : 96)
        let clr = dirs + 14 * 8
        guard pe.count >= clr + 8 else { return false }
        let rva = pe.withUnsafeBytes { $0.load(fromByteOffset: clr, as: UInt32.self) }
        let size = pe.withUnsafeBytes { $0.load(fromByteOffset: clr + 4, as: UInt32.self) }
        return rva != 0 && size != 0
    }

    /// Whether `program`, or a program in `folder` (its top level and one level
    /// down, where a Steam game keeps its executables), is a .NET program.
    static func needed(program: URL?, folder: URL?) -> Bool {
        if let program, isDotNet(program) { return true }
        guard let folder else { return false }
        let fm = FileManager.default
        var candidates: [URL] = []
        for item in (try? fm.contentsOfDirectory(at: folder, includingPropertiesForKeys: [.isDirectoryKey])) ?? [] {
            if item.pathExtension.lowercased() == "exe" { candidates.append(item); continue }
            if (try? item.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true {
                for sub in (try? fm.contentsOfDirectory(at: item, includingPropertiesForKeys: nil)) ?? []
                where sub.pathExtension.lowercased() == "exe" { candidates.append(sub) }
            }
        }
        // Crash reporters and installers are not the game.
        let skip = ["crash", "unins", "setup", "redist", "vc_", "dxsetup"]
        return candidates.prefix(24).contains { url in
            let name = url.lastPathComponent.lowercased()
            return !skip.contains(where: name.contains) && isDotNet(url)
        }
    }

    // MARK: - Install and link

    /// Make the runtime available for a launch, then call `done` on the main
    /// thread with whether it is (a failure is logged; the launch goes on, and
    /// Wine reports the missing runtime as before).
    @MainActor
    func prepare(drive: URL, done: @escaping (Bool) -> Void) {
        if Self.isInstalled {
            done(Self.link(drive: drive))
            return
        }
        guard let archive = Self.archive else {
            LogStore.shared.log("[wine-mono] the app has no wine-mono-\(Self.version)-x86.tar.xz (build/wine-mono/fetch.sh)", level: .error)
            done(false)
            return
        }
        status = "Installing the .NET runtime…"
        LogStore.shared.log("[wine-mono] unpacking Wine Mono \(Self.version) for a .NET program (first time only)")
        let started = Date()
        Task.detached(priority: .userInitiated) {
            let ok = Self.unpack(archive)
            await MainActor.run {
                self.status = nil
                LogStore.shared.log(String(format: "[wine-mono] %@ in %.1f s", ok ? "unpacked" : "unpacking FAILED",
                                           Date().timeIntervalSince(started)), level: ok ? .info : .error)
                done(ok && Self.link(drive: drive))
            }
        }
    }

    /// Unpack into a scratch folder, then move it into place, so an interrupted
    /// unpack never looks installed.
    private static func unpack(_ archive: URL) -> Bool {
        let fm = FileManager.default
        let scratch = installRoot.appendingPathComponent(".partial", isDirectory: true)
        try? fm.removeItem(at: scratch)
        try? fm.createDirectory(at: installRoot, withIntermediateDirectories: true)
        guard madeira_extract_tar_xz(archive.path, scratch.path) == 0 else { return false }
        let unpacked = scratch.appendingPathComponent("wine-mono-\(version)", isDirectory: true)
        guard fm.fileExists(atPath: unpacked.appendingPathComponent("bin/libmono-2.0-x86_64.dll").path) else { return false }
        try? fm.removeItem(at: installDir)
        do {
            try fm.moveItem(at: unpacked, to: installDir)
            try? fm.removeItem(at: scratch)
            // Not backed up to iCloud: it is in the app and can be unpacked again.
            var values = URLResourceValues(); values.isExcludedFromBackup = true
            var dir = installRoot; try? dir.setResourceValues(values)
            return fm.createFile(atPath: marker.path, contents: Data())
        } catch {
            return false
        }
    }

    /// drive_c/windows/mono/mono-2.0 -> the unpacked runtime. A real folder there
    /// (the runtime installed some other way) is left alone.
    @discardableResult
    static func link(drive: URL) -> Bool {
        let fm = FileManager.default
        let monoDir = drive.appendingPathComponent("windows/mono", isDirectory: true)
        let linkURL = monoDir.appendingPathComponent("mono-2.0")
        try? fm.createDirectory(at: monoDir, withIntermediateDirectories: true)
        if let target = try? fm.destinationOfSymbolicLink(atPath: linkURL.path) {
            if target == installDir.path { return true }
            try? fm.removeItem(at: linkURL)
        } else if fm.fileExists(atPath: linkURL.path) {
            return true
        }
        do {
            try fm.createSymbolicLink(at: linkURL, withDestinationURL: installDir)
            LogStore.shared.log("[wine-mono] C:\\windows\\mono\\mono-2.0 -> \(installDir.path)")
            return true
        } catch {
            LogStore.shared.log("[wine-mono] could not link the runtime into the prefix: \(error.localizedDescription)", level: .error)
            return false
        }
    }
}

/// The card shown while Wine Mono is unpacked before a .NET game starts.
struct WineMonoOverlay: View {
    @ObservedObject private var mono = WineMono.shared

    var body: some View {
        ZStack {
            if let status = mono.status {
                ZStack {
                    Color.black.opacity(0.35).ignoresSafeArea()
                    VStack(spacing: 12) {
                        ProgressView().controlSize(.large)
                        Text(status).font(.headline)
                        Text("Only the first time.").font(.subheadline).foregroundStyle(.secondary)
                    }
                    .padding(24)
                    .frame(minWidth: 220)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20))
                }
                .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.2), value: mono.status)
    }
}
