#!/usr/bin/env python3
"""Exercise production Epic decoders with offline fixtures, without an app build."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
checks = r'''
import Foundation
enum SteamLog { static func trace(_ message: String) {} }
func check(_ condition: Bool, _ name: String) {
    precondition(condition, name)
    print("PASS: " + name)
}
func data(_ object: Any) throws -> Data { try JSONSerialization.data(withJSONObject: object) }
@main struct Checks {
    static func main() throws {
        let paid: [String: Any] = [
            "id": "offer-1", "namespace": "namespace-1", "title": "A Game", "offerType": "BASE_GAME",
            "items": [["id": "catalog-1"]], "description": "A &amp; B", "developerDisplayName": "Dev",
            "publisherDisplayName": "Pub", "releaseDate": "2026-01-01T00:00:00.000Z",
            "keyImages": [["type": "Thumbnail", "url": "https://example.com/thumb.jpg"],
                          ["type": "OfferImageTall", "url": "https://example.com/tall.jpg"],
                          ["type": "OfferImageWide", "url": "https://example.com/wide.jpg"],
                          ["type": "featuredMedia", "url": "https://example.com/shot.jpg"],
                          ["type": "featuredMedia", "url": "https://example.com/shot.jpg"],
                          ["type": "featuredMedia", "url": "javascript:bad"]],
            "catalogNs": ["mappings": [["pageType": "other", "pageSlug": "wrong"], ["pageType": "productHome", "pageSlug": "a-game"]]],
            "price": ["totalPrice": ["originalPrice": 2000, "discountPrice": 1500, "discount": 500,
                                    "fmtPrice": ["originalPrice": "$20.00", "discountPrice": "$15.00"]]]]
        var free = paid; free["id"] = "free"; free["price"] = ["totalPrice": ["originalPrice": 2000, "discountPrice": 0]]
        var future = paid; future["id"] = "future"; future["price"] = NSNull()
        var dlc = paid; dlc["id"] = "dlc"; dlc["offerType"] = "ADD_ON"
        let layout: [String: Any] = ["errors": [["message": "One optional field failed"]], "data": ["Storefront": ["storefrontModulesPaginated": ["modules": [
            ["type": "banner", "title": "Featured", "offers": [dlc]],
            ["type": "brandedList", "offers": [["offer": paid], ["offer": paid], [:]]],
            ["type": "brandedList", "offers": [future]],
            ["type": "group", "offers": [future]],
            ["type": "subModules", "modules": [
                ["type": "group", "title": "Top Sellers", "offers": [paid, dlc]],
                ["type": "subModules", "modules": [["type": "group", "title": "Top Free to Play", "offers": [["offer": free]]]]],
                ["type": "group", "title": "Top Add-Ons", "offers": [paid]],
                ["type": "group", "title": "Top Demos", "offers": [paid]]]],
            ["type": "group", "title": "Coming Soon", "offers": [future]],
            ["type": "group", "title": "Top Sellers", "offers": [paid]],
            ["type": "brandedList", "title": "Epic Savings Spotlight", "offers": [paid]]
        ]]]]]
        let shelves = try EpicStoreDecoding.storefront(data(layout))
        check(shelves.map(\.title) == ["Featured", "Top Sellers", "Epic Savings Spotlight", "Coming Soon", "Free to play"], "nested modules, ordered shelves, skipped banners/add-ons/demos")
        check(shelves[0].games.count == 1 && shelves[0].games[0].offerID == "offer-1", "first untitled branded list only, wrapped offers, deduplication")
        check(shelves[1].games.count == 1, "duplicate groups merged and DLC filtered")
        let game = shelves[0].games[0]
        check(game.presentation.price == "$15.00" && game.presentation.discount == 25, "formatted discounted price and percentage from minor units")
        check(game.presentation.capsule?.lastPathComponent == "tall.jpg" && game.presentation.header?.lastPathComponent == "wide.jpg", "tall card and wide hero preference")
        check(game.presentation.screenshots.count == 1 && game.presentation.screenshots[0].id == 0, "featured media screenshots mapped, invalid URLs and duplicates skipped")
        check(game.presentation.description == "A & B" && game.presentation.developers == ["Dev"] && game.presentation.publishers == ["Pub"] && !game.presentation.release.isEmpty, "description, credits and release date")
        check(game.url?.absoluteString == "https://store.epicgames.com/p/a-game", "productHome URL preferred")
        check(shelves.last?.games.first?.presentation.isFree == true && shelves.last?.games.first?.presentation.price == "Free", "zero price is Free")
        check(shelves[3].games.first?.presentation.price == "TBA" && shelves[3].games.first?.presentation.isFree == false, "absent price is TBA, never free")
        check(try EpicStoreDecoding.storefront(data(["data": ["Storefront": ["storefrontModulesPaginated": ["modules": []]]]])).isEmpty, "empty valid storefront")
        do {
            _ = try EpicStoreDecoding.storefront(data(["errors": [["message": "Unavailable"]]]))
            preconditionFailure("errors-only response must fail")
        } catch { print("PASS: errors-only response fails instead of being cached") }
        let search = try EpicStoreDecoding.search(data(["data": ["Catalog": ["searchStore": ["elements": [paid, free, future, dlc, paid, [:]]]]]]))
        check(search.map(\.offerID) == ["offer-1", "free", "future"], "search decoding and duplicate/malformed/non-base filtering")
        check(try EpicStoreDecoding.search(data(["data": ["Catalog": ["searchStore": ["elements": []]]]])).isEmpty, "empty search results")
        let term = "a\"b\\c\n雪"
        let body = try StoreDecoding.object(EpicStoreDecoding.searchBody(term, country: "RO"))
        let query = body["query"] as! String
        let quoted = String(decoding: try JSONSerialization.data(withJSONObject: term, options: [.fragmentsAllowed]), as: UTF8.self)
        check(query.contains("keywords:" + quoted) && query.contains("country:\"RO\""), "GraphQL keyword escaping and regional pricing")
        check(game.matches(namespace: "namespace-1", catalogItemID: nil, title: "Different"), "ownership by namespace")
        check(game.matches(namespace: "other", catalogItemID: "catalog-1", title: "Different"), "ownership by catalog item ID")
        check(game.matches(namespace: "other", catalogItemID: "offer-1", title: "Different"), "ownership by offer ID")
        check(game.matches(namespace: "other", catalogItemID: nil, title: "  A   Gáme  "), "normalized ownership title fallback")
        check(!game.matches(namespace: "other", catalogItemID: "unknown", title: "A Game 2"), "distinct title is not owned")
        let sparse = EpicStoreDecoding.game(["id": "sparse", "title": "Sparse"])!
        check(!sparse.matches(namespace: "", catalogItemID: "", title: "") && sparse.url == nil, "missing IDs do not create ownership or product URL")
        var fallback = paid; fallback["catalogNs"] = ["mappings": []]; fallback["offerMappings"] = [["pageSlug": "offer-slug"]]
        check(EpicStoreDecoding.game(fallback)?.url?.lastPathComponent == "offer-slug", "offer mapping fallback with empty catalog mappings")
        fallback["offerMappings"] = []; fallback["productSlug"] = "product-slug"
        check(EpicStoreDecoding.game(fallback)?.url?.lastPathComponent == "product-slug", "productSlug URL fallback")
        fallback["productSlug"] = ""; fallback["urlSlug"] = "url-slug"
        check(EpicStoreDecoding.game(fallback)?.url?.lastPathComponent == "url-slug", "urlSlug fallback skips empty productSlug")
        fallback["keyImages"] = [["type": "Thumbnail", "url": "https://example.com/thumb.jpg"]]
        check(EpicStoreDecoding.game(fallback)?.presentation.capsule?.lastPathComponent == "thumb.jpg", "thumbnail artwork fallback")
        fallback["keyImages"] = [["type": "OfferImageWide", "url": "https://example.com/wide.jpg"]]
        check(EpicStoreDecoding.game(fallback)?.presentation.capsule?.lastPathComponent == "wide.jpg", "wide artwork fallback")
        func promotion(_ id: String, _ start: String, _ end: String, _ upcoming: Bool = false, _ discount: Int = 0) -> [String: Any] {
            var row = paid; row["id"] = id
            row["promotions"] = [upcoming ? "upcomingPromotionalOffers" : "promotionalOffers": [["promotionalOffers": [
                ["startDate": start, "endDate": end, "discountSetting": ["discountPercentage": discount]]]]]]
            return row
        }
        var mappedPromotion = promotion("mapped", "2026-01-01T00:00:00Z", "2026-01-08T00:00:00Z")
        mappedPromotion["catalogNs"] = ["mappings": []]
        mappedPromotion["offerMappings"] = [["pageSlug": "mapped-free-game"]]
        let now = EpicStoreDecoding.date("2026-01-02T00:00:00Z")!
        let promotions = try EpicStoreDecoding.promotions(data(["data": ["Catalog": ["searchStore": ["elements": [
            promotion("current", "2026-01-01T00:00:00Z", "2026-01-08T00:00:00Z"),
            promotion("upcoming", "2026-01-08T00:00:00Z", "2026-01-15T00:00:00Z", true),
            promotion("expired", "2025-12-25T00:00:00Z", "2026-01-02T00:00:00Z"),
            promotion("paid", "2026-01-01T00:00:00Z", "2026-01-08T00:00:00Z", false, 50)
        ]]]]]), now: now)
        let mapped = try EpicStoreDecoding.promotions(data(["data": ["Catalog": ["searchStore": ["elements": [mappedPromotion]]]]]), now: now)
        check(mapped.first?.url?.lastPathComponent == "mapped-free-game", "promotions share product URL fallbacks")
        check(promotions.map(\.offerID) == ["current", "upcoming"], "current and upcoming free promotions, expired and paid excluded")
        check(promotions[0].presentation.isFree && promotions[0].presentation.price == "Free", "active promotion overrides stale paid metadata")
        check(!promotions[1].presentation.isFree && promotions[1].presentation.price == "$15.00" && promotions[1].promotionStart! > now, "upcoming promotion retains today's price and future availability")
    }
}
'''
with tempfile.TemporaryDirectory(prefix='epic-store-') as folder:
    work = Path(folder)
    (work / 'checks.swift').write_text(checks)
    subprocess.run([os.environ.get('SWIFTC') or shutil.which('swiftc'), '-parse-as-library', '-swift-version', '5',
                    '-module-cache-path', str(work / 'cache'), str(ROOT / 'app/Madeira/Store/SteamStore.swift'),
                    str(ROOT / 'app/Madeira/Store/EpicStore.swift'),
                    str(ROOT / 'app/Madeira/SwiftSteam/Auth/SteamTokenStore.swift'),
                    str(work / 'checks.swift'), '-o', str(work / 'checks')], check=True)
    subprocess.run([str(work / 'checks')], check=True)
