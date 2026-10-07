// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 Jakub Burgis
// Madeira Converter Exception: see LICENSE-EXCEPTION.md

import Foundation

/// A Steam Workshop item as PublishedFile.GetDetails#1 describes it: the fields
/// a download and the collection walk need (steammessages_publishedfile
/// PublishedFileDetails).
struct WorkshopItem: Sendable, Equatable {
    /// EWorkshopFileType: a mod (Community) and a collection; other kinds are
    /// art, videos, guides and the like.
    static let typeCommunity: UInt32 = 0
    static let typeCollection: UInt32 = 2

    var id: UInt64
    /// The item's EResult: 1 when it could be read. A private, friends-only or
    /// deleted item comes back with another code.
    var result: UInt32 = 0
    var title = ""
    var consumerAppID: UInt32 = 0
    var fileType: UInt32 = 0
    /// The manifest of the item's content in the app's Workshop depot; 0 for a
    /// legacy item, whose content is the single file at `fileURL`.
    var manifestID: UInt64 = 0
    var fileURL = ""
    var filename = ""
    var fileSize: UInt64 = 0
    var timeUpdated: UInt32 = 0
    var visibility: UInt32 = 0
    var banned = false
    /// A collection's items, or a mod's required items, in their sort order.
    var children: [UInt64] = []

    var isCollection: Bool { fileType == Self.typeCollection }
    var isMod: Bool { fileType == Self.typeCommunity }
}

enum WorkshopError: LocalizedError, Equatable {
    case notACollectionLink
    case unreadable(UInt64, UInt32)
    case unsuitable(UInt64, String)

    var errorDescription: String? {
        switch self {
        case .notACollectionLink:
            return "That is not a Steam Workshop link or item number."
        case .unreadable(let id, let result):
            return "Steam would not show Workshop item \(id) (result \(result)). A private or friends-only collection may not be readable; make it public or unlisted."
        case .unsuitable(let id, let reason):
            return "Workshop item \(id) cannot be synced: \(reason)."
        }
    }
}

enum SteamWorkshop {
    /// Item IDs per GetDetails request.
    static let batchSize = 100

    /// The item ID in a Workshop link (`...filedetails/?id=123`) or a bare number.
    static func itemID(from text: String) -> UInt64? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if let id = UInt64(trimmed), id > 0 { return id }
        guard let items = URLComponents(string: trimmed)?.queryItems,
              let value = items.first(where: { $0.name.lowercased() == "id" })?.value,
              let id = UInt64(value), id > 0 else { return nil }
        return id
    }

    /// CPublishedFile_GetDetails_Request: the IDs (fixed64, one tag each),
    /// includechildren and the app.
    static func detailsRequest(_ ids: [UInt64], appID: UInt32) -> Data {
        var request = ProtobufEncoder()
        for id in ids { request.writeFixed64Always(fieldNumber: 1, value: id) }
        request.writeBool(fieldNumber: 4, value: true)       // includechildren
        request.writeUInt32(fieldNumber: 14, value: appID)   // appid
        return request.data
    }

    /// CPublishedFile_GetDetails_Response: its publishedfiledetails (field 1).
    static func parseDetails(_ data: Data) throws -> [WorkshopItem] {
        var decoder = ProtobufDecoder(data)
        var items: [WorkshopItem] = []
        while let tag = try decoder.readTag() {
            if tag.fieldNumber == 1, tag.wireType == .lengthDelimited {
                items.append(try parseItem(try decoder.readBytes()))
            } else {
                try decoder.skip(wireType: tag.wireType)
            }
        }
        return items
    }

    private static func parseItem(_ data: Data) throws -> WorkshopItem {
        var decoder = ProtobufDecoder(data)
        var item = WorkshopItem(id: 0)
        var children: [(id: UInt64, order: UInt32)] = []
        while let tag = try decoder.readTag() {
            switch (tag.fieldNumber, tag.wireType) {
            case (1, .varint): item.result = UInt32(truncatingIfNeeded: try decoder.readVarint())
            case (2, _): item.id = try number(&decoder, tag.wireType)
            case (5, .varint): item.consumerAppID = UInt32(truncatingIfNeeded: try decoder.readVarint())
            case (7, .lengthDelimited): item.filename = try decoder.readString()
            case (8, _): item.fileSize = try number(&decoder, tag.wireType)
            case (10, .lengthDelimited): item.fileURL = try decoder.readString()
            case (14, _): item.manifestID = try number(&decoder, tag.wireType)
            case (16, .lengthDelimited): item.title = try decoder.readString()
            case (20, .varint): item.timeUpdated = UInt32(truncatingIfNeeded: try decoder.readVarint())
            case (21, .varint): item.visibility = UInt32(truncatingIfNeeded: try decoder.readVarint())
            case (28, .varint): item.banned = try decoder.readVarint() != 0
            case (34, .varint): item.fileType = UInt32(truncatingIfNeeded: try decoder.readVarint())
            case (53, .lengthDelimited):
                var child = ProtobufDecoder(try decoder.readBytes())
                var id: UInt64 = 0, order: UInt32 = 0
                while let t = try child.readTag() {
                    switch (t.fieldNumber, t.wireType) {
                    case (1, _): id = try number(&child, t.wireType)
                    case (2, .varint): order = UInt32(truncatingIfNeeded: try child.readVarint())
                    default: try child.skip(wireType: t.wireType)
                    }
                }
                if id != 0 { children.append((id, order)) }
            default: try decoder.skip(wireType: tag.wireType)
            }
        }
        // A stable sort keeps the reply's order among equal sort orders.
        item.children = children.enumerated()
            .sorted { ($0.element.order, $0.offset) < ($1.element.order, $1.offset) }
            .map(\.element.id)
        return item
    }

    /// An integer field Steam may send as varint or fixed64.
    private static func number(_ decoder: inout ProtobufDecoder, _ wireType: ProtoWireType) throws -> UInt64 {
        switch wireType {
        case .fixed64: return try decoder.readFixed64()
        case .fixed32: return UInt64(try decoder.readFixed32())
        case .varint: return try decoder.readVarint()
        case .lengthDelimited: _ = try decoder.readBytes(); return 0
        }
    }

    /// Details of up to `batchSize` items over the logged-on connection.
    @MainActor
    static func fetchDetails(_ ids: [UInt64], appID: UInt32, session: SteamCMSession) async throws -> [WorkshopItem] {
        try await session.ensureConnected()
        let body = try await session.callServiceMethod(method: .publishedFileGetDetails,
                                                       body: detailsRequest(ids, appID: appID), timeout: 20)
        return try parseDetails(body)
    }

    /// What a collection resolves to.
    struct Resolution: Sendable, Equatable {
        /// The collection itself (title, last update), or the single item when
        /// the link names a mod.
        var collection: WorkshopItem
        /// The mods to install, level by level: the collection's own in its
        /// order, then those of nested collections and the required items.
        var mods: [WorkshopItem] = []
        /// A required item no collection lists: the mod that requires it.
        var requiredBy: [UInt64: UInt64] = [:]
        /// Items left out, with the reason, for the user to see.
        var skipped: [UInt64: String] = [:]
        /// Every item the collection still lists or requires, skipped ones
        /// included: a recorded mod that is listed but momentarily skipped
        /// (unreadable, made private, banned) is kept, not removed.
        var listed: Set<UInt64> = []
        /// Steam did not answer for some item (not returned, or a transient
        /// error): what hangs below it is unknown, so nothing is removed.
        var incomplete = false
        /// Items Steam answered for as gone for good (deleted, private). A
        /// mod's required items are unknown then, so recorded items it
        /// required are kept.
        var unavailable: Set<UInt64> = []
    }

    /// EResults that say "try again" rather than "this item is gone": Fail,
    /// Busy, Timeout, ServiceUnavailable, and 0 for an ID Steam did not return.
    static let transientResults: Set<UInt32> = [0, 2, 10, 16, 20]

    /// Walks a collection: nested collections are expanded, each mod's required
    /// items are followed (transitively), and anything for another app, banned,
    /// not a mod or unreadable is left out with its reason. Every item is
    /// visited once, so cycles end. A link to a single mod resolves to that mod
    /// and its requirements. `fetch` asks Steam for up to `batchSize` items.
    static func resolve(collection rootID: UInt64, appID: UInt32,
                        fetch: ([UInt64]) async throws -> [WorkshopItem]) async throws -> Resolution {
        var known: [UInt64: WorkshopItem] = [:]
        func load(_ ids: [UInt64]) async throws {
            let missing = Array(Set(ids.filter { known[$0] == nil })).sorted()
            var start = 0
            while start < missing.count {
                let batch = Array(missing[start..<min(start + batchSize, missing.count)])
                for item in try await fetch(batch) where item.id != 0 { known[item.id] = item }
                // An ID Steam did not answer for is unreadable.
                for id in batch where known[id] == nil { known[id] = WorkshopItem(id: id) }
                start += batchSize
            }
        }

        try await load([rootID])
        guard let root = known[rootID], root.result == 1 else {
            throw WorkshopError.unreadable(rootID, known[rootID]?.result ?? 0)
        }
        // A root that is not this game's collection or mod would resolve to
        // nothing, and a sync would then remove everything it installed.
        if root.banned { throw WorkshopError.unsuitable(rootID, "it is banned") }
        guard root.isCollection || root.isMod else { throw WorkshopError.unsuitable(rootID, "it is not a collection or a mod") }
        guard root.consumerAppID == appID else { throw WorkshopError.unsuitable(rootID, "it belongs to another game (app \(root.consumerAppID))") }
        var resolution = Resolution(collection: root)
        var visited: Set<UInt64> = []
        var level: [(id: UInt64, requiredBy: UInt64?)] = [(rootID, nil)]
        while !level.isEmpty {
            try await load(level.map(\.id))
            var next: [(id: UInt64, requiredBy: UInt64?)] = []
            for entry in level {
                // Listed by a collection after all: not merely required.
                if entry.requiredBy == nil { resolution.requiredBy.removeValue(forKey: entry.id) }
                guard visited.insert(entry.id).inserted else { continue }
                resolution.listed.insert(entry.id)
                guard let item = known[entry.id], item.result == 1 else {
                    let result = known[entry.id]?.result ?? 0
                    resolution.skipped[entry.id] = "not available (result \(result))"
                    if transientResults.contains(result) { resolution.incomplete = true } else { resolution.unavailable.insert(entry.id) }
                    continue
                }
                if item.banned { resolution.skipped[item.id] = "banned"; continue }
                if item.isCollection {
                    next += item.children.map { ($0, entry.requiredBy) }
                    continue
                }
                guard item.isMod else { resolution.skipped[item.id] = "not a mod (type \(item.fileType))"; continue }
                guard item.consumerAppID == appID else {
                    resolution.skipped[item.id] = "for another game (app \(item.consumerAppID))"
                    continue
                }
                resolution.mods.append(item)
                if let parent = entry.requiredBy { resolution.requiredBy[item.id] = parent }
                next += item.children.map { ($0, item.id) }
            }
            level = next
        }
        return resolution
    }
}
