// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 Jakub Burgis
// Madeira Converter Exception: see LICENSE-EXCEPTION.md

import Foundation
#if canImport(FoundationXML)
import FoundationXML
#endif

/// What Madeira installed from a game's Workshop collection, kept beside the
/// game's install records as `steamapps/workshop/madeira_<appid>.json`. Folders
/// are relative to `steamapps`, because the app's container path can change.
/// Only folders listed here are ever replaced or removed by a sync.
struct WorkshopRecord: Codable, Equatable {
    struct Entry: Codable, Equatable {
        var title: String
        /// The content manifest (decimal; JSON numbers lose 64-bit precision),
        /// "0" for a legacy single-file item.
        var manifest: String
        var timeUpdated: UInt32
        /// The installed folder, relative to `steamapps`.
        var folder: String
        /// The mod that required it, when no collection lists it.
        var requiredBy: UInt64?
        var bytes: UInt64
    }

    var appID: UInt32
    var collection: UInt64 = 0
    /// By Workshop item ID (decimal).
    var items: [String: Entry] = [:]

    static func url(appID: UInt32, steamApps: URL) -> URL {
        steamApps.appendingPathComponent("workshop/madeira_\(appID).json")
    }

    static func load(appID: UInt32, steamApps: URL) -> WorkshopRecord {
        guard let data = try? Data(contentsOf: url(appID: appID, steamApps: steamApps)),
              let record = try? JSONDecoder().decode(WorkshopRecord.self, from: data), record.appID == appID
        else { return WorkshopRecord(appID: appID) }
        return record
    }

    func save(steamApps: URL) throws {
        let url = Self.url(appID: appID, steamApps: steamApps)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(self).write(to: url, options: .atomic)
    }
}

enum WorkshopInstall {
    /// Where an item's folder goes, relative to `steamapps`. A game that reads
    /// mods from its own folder gets them there (RimWorld: `Mods/<id>`, the only
    /// place it looks when Steam is not running); any other game gets Valve's
    /// layout, `workshop/content/<app>/<id>`.
    static func folder(itemID: UInt64, appID: UInt32, installFolder: String) -> String {
        switch appID {
        case 294100: return "common/\(installFolder)/Mods/\(itemID)"
        default: return "workshop/content/\(appID)/\(itemID)"
        }
    }

    struct Plan: Equatable {
        /// New items and items whose content changed, in the collection's order.
        var install: [WorkshopItem] = []
        /// Items whose place is taken by a folder Madeira did not install (a
        /// copy made by hand or by Valve's client): left alone, reported.
        var conflicts: [WorkshopItem] = []
        /// Recorded items the collection no longer resolves to (item IDs).
        var remove: [String] = []
        var unchanged = 0
        /// Items would be removed, but Steam did not answer for everything the
        /// collection lists, so removals wait for a complete answer.
        var removalsDeferred = false
        var isEmpty: Bool { install.isEmpty && remove.isEmpty }
    }

    /// What a sync has to do: install what is new or changed (another manifest,
    /// a legacy item updated, or its folder gone), and remove what the
    /// collection no longer lists or requires. An item it still lists but that
    /// was skipped this time (unreadable, private, banned, not answered) is kept.
    static func plan(_ resolution: SteamWorkshop.Resolution, record: WorkshopRecord, installFolder: String,
                     folderExists: (String) -> Bool) -> Plan {
        var plan = Plan()
        var wanted = Set(resolution.listed.map(String.init)).union(resolution.mods.map { String($0.id) })
        // What an unavailable mod required is unknown: keep what it required before.
        for (id, entry) in record.items where entry.requiredBy.map(resolution.unavailable.contains) == true {
            wanted.insert(id)
        }
        for item in resolution.mods {
            let id = String(item.id)
            let relative = folder(itemID: item.id, appID: record.appID, installFolder: installFolder)
            let ours = record.items[id].map { isExpected($0, itemID: id, appID: record.appID, installFolder: installFolder) } ?? false
            if ours, let entry = record.items[id], entry.manifest == String(item.manifestID),
               item.manifestID != 0 || entry.timeUpdated == item.timeUpdated, folderExists(relative) {
                plan.unchanged += 1
            } else if !ours, folderExists(relative) {
                plan.conflicts.append(item)
            } else {
                plan.install.append(item)
            }
        }
        plan.remove = record.items.keys.filter { !wanted.contains($0) }.sorted()
        if resolution.incomplete, !plan.remove.isEmpty {
            plan.removalsDeferred = true
            plan.remove = []
        }
        return plan
    }

    /// Whether a recorded folder is exactly where this item belongs. A sync
    /// replaces or deletes only such a folder: never anything a record
    /// (which sits on the Wine drive, writable by the guest) merely claims.
    static func isExpected(_ entry: WorkshopRecord.Entry, itemID: String, appID: UInt32, installFolder: String) -> Bool {
        guard let id = UInt64(itemID) else { return false }
        // Case-insensitively, as the file system compares: a game folder whose
        // installdir changes case is still the same folder.
        return entry.folder.caseInsensitiveCompare(folder(itemID: id, appID: appID, installFolder: installFolder)) == .orderedSame
    }

    /// The `<packageId>` an About/About.xml declares for the mod itself (the
    /// root element's own child, not one under modDependencies or loadAfter),
    /// lower-cased as RimWorld compares it, or nil.
    static func packageID(inModFolder folder: URL) -> String? {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("About/About.xml")) else { return nil }
        return packageID(aboutXML: data)
    }

    static func packageID(aboutXML data: Data) -> String? {
        let reader = RootPackageID()
        let parser = XMLParser(data: data)
        parser.delegate = reader
        parser.parse()
        let id = reader.value?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() ?? ""
        return id.isEmpty ? nil : id
    }

    /// Collects the text of the first `<packageId>` directly under the root
    /// element, then stops.
    private final class RootPackageID: NSObject, XMLParserDelegate {
        var value: String?
        private var depth = 0
        private var text: String?

        func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?,
                    qualifiedName: String?, attributes: [String: String] = [:]) {
            depth += 1
            if depth == 2, name.caseInsensitiveCompare("packageId") == .orderedSame { text = "" }
        }

        func parser(_ parser: XMLParser, foundCharacters string: String) { text? += string }

        func parser(_ parser: XMLParser, foundCDATA block: Data) {
            if text != nil, let string = String(data: block, encoding: .utf8) { text? += string }
        }

        func parser(_ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?) {
            if depth == 2, let found = text {
                value = found
                parser.abortParsing()
            }
            depth -= 1
        }
    }

    /// Folders beside `installed` (not installed by Madeira) that declare the
    /// same packageId: a second copy the game would load only one of.
    static func duplicates(of installed: URL, managed: Set<String>, steamApps: URL) -> [String] {
        guard let id = packageID(inModFolder: installed) else { return [] }
        let parent = installed.deletingLastPathComponent()
        let siblings = (try? FileManager.default.contentsOfDirectory(at: parent, includingPropertiesForKeys: [.isDirectoryKey])) ?? []
        let base = steamApps.standardizedFileURL.path + "/"
        return siblings.compactMap { url -> String? in
            guard url.lastPathComponent != installed.lastPathComponent,
                  (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true,
                  packageID(inModFolder: url) == id else { return nil }
            let path = url.standardizedFileURL.path
            let relative = path.hasPrefix(base) ? String(path.dropFirst(base.count)) : path
            return managed.contains(relative) ? nil : url.lastPathComponent
        }.sorted()
    }

    /// Outcome of a sync, for the log and the game's page.
    struct Result: Equatable {
        var installed = 0
        var removed = 0
        var unchanged = 0
        var failed = 0
        var bytes: UInt64 = 0
        /// Items left alone or not installed this time, and second copies.
        var warnings: [String] = []
    }

    static func conflictWarning(_ item: WorkshopItem, appID: UInt32, installFolder: String) -> String {
        "\(item.title): \(folder(itemID: item.id, appID: appID, installFolder: installFolder)) already exists and was not installed by Madeira; it was left alone"
    }

    /// "<title>: also installed by hand in Mods/<folder>; remove that copy"
    /// for every recorded mod that another folder beside it declares too (same
    /// packageId), read from disk now: each folder holding recorded items is
    /// scanned once.
    static func secondCopies(_ record: WorkshopRecord, steamApps: URL) -> [String] {
        let fm = FileManager.default
        let managed = Set(record.items.values.map { $0.folder.lowercased() })
        var byParent: [String: [(id: String, entry: WorkshopRecord.Entry)]] = [:]
        for (id, entry) in record.items {
            let parent = (entry.folder as NSString).deletingLastPathComponent
            byParent[parent, default: []].append((id, entry))
        }
        var warnings: [String] = []
        for (parent, entries) in byParent.sorted(by: { $0.key < $1.key }) {
            let dir = steamApps.appendingPathComponent(parent, isDirectory: true)
            var others: [String: [String]] = [:]   // packageId -> unmanaged folder names
            for name in ((try? fm.contentsOfDirectory(atPath: dir.path)) ?? []).sorted()
            where !managed.contains("\(parent)/\(name)".lowercased()) {
                if let id = packageID(inModFolder: dir.appendingPathComponent(name, isDirectory: true)) {
                    others[id, default: []].append(name)
                }
            }
            guard !others.isEmpty else { continue }
            for (_, entry) in entries.sorted(by: { $0.id < $1.id }) {
                guard let id = packageID(inModFolder: steamApps.appendingPathComponent(entry.folder, isDirectory: true)),
                      let copies = others[id] else { continue }
                let where_ = copies.map { "\((parent as NSString).lastPathComponent)/\($0)" }.joined(separator: ", ")
                warnings.append("\(entry.title): also installed by hand in \(where_); remove that copy")
            }
        }
        return warnings
    }

    /// Applies a plan: each item is downloaded to its staging folder by
    /// `download`, then swapped into place (the old copy is moved aside, the new
    /// one renamed in, the old one deleted), and the record is saved after every
    /// item so an interruption keeps what finished. An item that fails is
    /// reported and the rest go on. A folder already at an item's place that
    /// the record does not list as that item's (a copy installed by hand, or by
    /// Valve's client) is never touched: the item is skipped with a warning.
    /// Dropped items' recorded folders are deleted. `progress` reports items
    /// done of the total.
    static func apply(_ plan: Plan, resolution: SteamWorkshop.Resolution, record: inout WorkshopRecord,
                      installFolder: String, steamApps: URL,
                      download: (WorkshopItem) async throws -> (folder: URL, bytes: UInt64),
                      progress: (_ done: Int, _ total: Int, _ title: String) -> Void) async throws -> Result {
        let fm = FileManager.default
        var result = Result(unchanged: plan.unchanged)
        result.warnings = plan.conflicts.map { conflictWarning($0, appID: record.appID, installFolder: installFolder) }
        record.collection = resolution.collection.id
        let total = plan.install.count + plan.remove.count
        var done = 0

        for item in plan.install {
            try Task.checkCancellation()
            progress(done, total, item.title)
            done += 1
            let id = String(item.id)
            let relative = folder(itemID: item.id, appID: record.appID, installFolder: installFolder)
            let destination = steamApps.appendingPathComponent(relative, isDirectory: true)
            let ours = record.items[id].map { isExpected($0, itemID: id, appID: record.appID, installFolder: installFolder) } ?? false
            if fm.fileExists(atPath: destination.path), !ours {
                result.warnings.append(conflictWarning(item, appID: record.appID, installFolder: installFolder))
                continue
            }
            do {
                let staged = try await download(item)
                // A folder may have appeared there while the item downloaded.
                if fm.fileExists(atPath: destination.path), !ours {
                    result.warnings.append(conflictWarning(item, appID: record.appID, installFolder: installFolder))
                    continue
                }
                let work = staged.folder.deletingLastPathComponent()
                // The content is complete: its journal must not outlive it, or a
                // later download would trust chunks that are no longer on disk.
                try? fm.removeItem(at: work.appendingPathComponent("journal", isDirectory: true))
                // Claimed in the record before the swap: if the app dies in
                // between, the next sync finds its own (provisional) folder and
                // installs it again, instead of a folder it must not touch.
                record.items[id] = WorkshopRecord.Entry(title: item.title, manifest: "0", timeUpdated: 0, folder: relative,
                                                        requiredBy: resolution.requiredBy[item.id], bytes: 0)
                try record.save(steamApps: steamApps)
                try fm.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
                let aside = work.appendingPathComponent("previous", isDirectory: true)
                try? fm.removeItem(at: aside)
                if fm.fileExists(atPath: destination.path) { try fm.moveItem(at: destination, to: aside) }
                do {
                    try fm.moveItem(at: staged.folder, to: destination)
                } catch {
                    // Put the previous copy back rather than leave the mod missing.
                    if fm.fileExists(atPath: aside.path), !fm.fileExists(atPath: destination.path) {
                        try? fm.moveItem(at: aside, to: destination)
                    }
                    throw error
                }
                try? fm.removeItem(at: work)
                record.items[id] = WorkshopRecord.Entry(
                    title: item.title, manifest: String(item.manifestID), timeUpdated: item.timeUpdated,
                    folder: relative, requiredBy: resolution.requiredBy[item.id], bytes: staged.bytes)
                try record.save(steamApps: steamApps)
                result.installed += 1
                result.bytes += staged.bytes
            } catch {
                if error is CancellationError || Task.isCancelled { throw error }
                result.failed += 1
                result.warnings.append("\(item.title): not installed (\(error.localizedDescription))")
            }
        }

        for id in plan.remove {
            try Task.checkCancellation()
            guard let entry = record.items[id] else { continue }
            progress(done, total, entry.title)
            done += 1
            // The item's own place is deleted, never the record's spelling of it.
            if isExpected(entry, itemID: id, appID: record.appID, installFolder: installFolder), let itemID = UInt64(id) {
                let own = folder(itemID: itemID, appID: record.appID, installFolder: installFolder)
                try? fm.removeItem(at: steamApps.appendingPathComponent(own, isDirectory: true))
            }
            try? fm.removeItem(at: steamApps.appendingPathComponent("downloading/workshop/\(id)", isDirectory: true))
            record.items[id] = nil
            try record.save(steamApps: steamApps)
            result.removed += 1
        }
        if plan.removalsDeferred {
            result.warnings.append("Steam did not answer for every item in the collection, so no mods were removed this time")
        }
        result.warnings += secondCopies(record, steamApps: steamApps)
        try record.save(steamApps: steamApps)
        progress(done, total, "")
        return result
    }
}
