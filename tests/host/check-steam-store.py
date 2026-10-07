#!/usr/bin/env python3
"""Compile and exercise the production storefront decoders without network or an app build."""
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
func data(_ s: String) -> Data { Data(s.utf8) }
@main struct Checks {
    static func main() throws {
        let featured = try StoreDecoding.featured(data(#"{"specials":{"items":[{"id":440,"name":"TF2","is_free":true},{"id":440,"name":"Duplicate"},{"id":0,"name":"Bad"},{}]},"top_sellers":{"items":[{"id":10,"name":"Paid","discount_percent":25,"final_price":750,"currency":"USD"}]},"new_releases":null}"#))
        check(featured["specials"]?.count == 1, "deduplicate and skip malformed cards")
        check(featured["specials"]?.first?.price == "Free", "free featured game")
        check(featured["top_sellers"]?.first?.discount == 25, "featured discount")
        check(featured["coming_soon"]?.isEmpty == true, "missing shelf")
        let carousel = try StoreDecoding.carousel(data(#"{"featured_win":[{"id":10,"name":"Carousel","windows_available":true,"header_image":"https://example.com/header.jpg","final_price":750,"currency":"USD","discount_percent":25},{"id":20,"name":"No Windows","windows_available":false},{"id":30,"name":"Bundle","type":1},{"id":10,"name":"Duplicate"},{"id":40,"name":"Windows array membership","is_free":true},{}],"featured_mac":[{"id":50,"name":"Mac"}]}"#))
        check(carousel.map(\.id) == [10, 40], "front-page order, Windows filter, apps only and deduplication")
        check(carousel[0].header?.lastPathComponent == "header.jpg" && carousel[0].discount == 25 && !carousel[0].price.isEmpty && carousel[1].isFree, "carousel artwork, price and discount")
        check(try StoreDecoding.carousel(data("{}")).isEmpty, "missing carousel hides shelf")
        let queue = try StoreDecoding.discovery(data(#"{"response":{"appids":[20,10,20,0,-1,40]}}"#))
        check(queue == [20, 10, 40], "discovery queue order and valid unique appids")
        check(try StoreDecoding.discovery(data(#"{"response":{}}"#)).isEmpty, "empty discovery queue")
        let browse = try StoreDecoding.browse(data(#"{"response":{"store_items":[{"appid":10,"name":"Paid","success":1,"assets":{"asset_url_format":"steam/apps/10/${FILENAME}","header":"header.jpg","library_capsule":"library.jpg"},"best_purchase_option":{"formatted_final_price":"$7.50","discount_pct":25}},{"appid":20,"name":"Free","assets":{"header":"https://example.com/free.jpg"},"best_purchase_option":{"is_free":true}},{"appid":40,"name":"Unavailable","success":2},{"id":50,"name":"Package","item_type":1},{}]}}"#), order: queue)
        check(browse.map(\.id) == [20, 10], "browse restores discovery order and skips unavailable records")
        check(browse[0].isFree && browse[0].price == "Free" && browse[1].price == "$7.50" && browse[1].discount == 25, "browse purchase options")
        check(browse[1].capsule?.absoluteString == "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/10/library.jpg" && browse[0].header?.host == "example.com", "relative and absolute browse assets")
        func token(_ claims: String) -> String { "header." + Data(claims.utf8).base64EncodedString().replacingOccurrences(of: "=", with: "") + ".signature" }
        let now = Date(timeIntervalSince1970: 1000)
        check(!StoreDecoding.needsRefresh(token(#"{"exp":1001,"sub":"123"}"#), now: now), "unexpired access token reused")
        check(StoreDecoding.needsRefresh(token(#"{"exp":1000}"#), now: now), "access token expires at boundary")
        check(StoreDecoding.needsRefresh(token(#"{"exp":999}"#), now: now), "expired access token refreshed")
        check(StoreDecoding.needsRefresh(token(#"{"exp":2000}"#), now: now, unauthorized: true), "401 forces refresh even before expiry")
        check(StoreDecoding.needsRefresh("", now: now) && StoreDecoding.needsRefresh("broken", now: now) && StoreDecoding.needsRefresh(token("{}"), now: now), "missing or unknown token expiry refreshed")
        check(StoreDecoding.tokenClaims(token(#"{"sub":"123"}"#))["sub"] as? String == "123", "refresh token subject supplies steamid")
        let freeCategory = try StoreDecoding.featured(data(#"{"free_to_play":{"items":[{"id":440,"name":"TF2","is_free":true}]}}"#))
        check(freeCategory["free_to_play"]?.first?.id == 440 && featured["free_to_play"]?.isEmpty == true, "optional free-to-play category")
        let search = try StoreDecoding.search(data(#"{"items":[{"id":10,"name":"Paid","tiny_image":"https://example.com/header.jpg","price":{"currency":"USD","final":999}},{"id":11,"name":"Coming soon"}]}"#))
        check(search.count == 2 && !search[0].price.isEmpty && search[1].price.isEmpty, "search prices and missing prices")
        let details = try StoreDecoding.details(data(#"{"10":{"success":true,"data":{"name":"Game","short_description":"A &amp; B<br> &quot;Play&quot;","header_image":"https://example.com/header.jpg","is_free":false,"price_overview":{"final_formatted":"$9.99","discount_percent":50},"platforms":{"windows":true,"mac":false},"genres":[{"description":"Action"},{}],"developers":["Dev"],"publishers":["Pub"],"release_date":{"date":"1 Jan, 2026"},"screenshots":[{"path_full":"https://example.com/full.jpg","path_thumbnail":"https://example.com/thumb.jpg"},{}],"movies":[{"name":"HLS","hls_h264":"https://example.com/video.m3u8","mp4":{"max":"https://example.com/video.mp4"}},{"mp4":{"480":"https://example.com/low.mp4"}},{"webm":{"max":"https://example.com/video.webm"}},{}]}}}"#), id: 10)!
        check(details.id == 10 && details.price == "$9.99" && details.discount == 50, "keyed appdetails price")
        check(details.description == "A & B \"Play\"", "plain text description")
        check(details.platforms == ["windows"] && details.genres == ["Action"], "platforms and genres")
        check(details.developers == ["Dev"] && details.publishers == ["Pub"] && !details.release.isEmpty, "credits and release")
        check(details.screenshots.count == 1 && details.movies.count == 3, "skip malformed media")
        check(details.movies[0].url.pathExtension == "m3u8" && details.movies[1].url.pathExtension == "mp4", "HLS preference and MP4 fallback")
        check(try StoreDecoding.details(data(#"{"10":{"success":false,"data":[]}}"#), id: 10) == nil, "unavailable app")
        let sparse = try StoreDecoding.details(data(#"{"10":{"success":true,"data":{"name":"Sparse"}}}"#), id: 10)!
        check(sparse.screenshots.isEmpty && sparse.movies.isEmpty && !sparse.isFree, "safe missing fields")
        check(StoreAction.decide(owned: true, isFree: true) == .owned, "owned free game opens library")
        check(StoreAction.decide(owned: true, isFree: false) == .owned, "owned paid game opens library")
        check(StoreAction.decide(owned: false, isFree: true) == .get, "unowned free game gets license")
        check(StoreAction.decide(owned: false, isFree: false) == .buy, "unowned paid game buys on Steam")
        let epic = try EpicStoreOffer.decode(data(#"{"data":{"Catalog":{"searchStore":{"elements":[{"id":"free","title":"Free offer","productSlug":"free-offer","keyImages":[{"type":"OfferImageTall","url":"https://example.com/tall.jpg"}],"promotions":{"promotionalOffers":[{"promotionalOffers":[{"startDate":"2026-01-01T00:00:00.000Z","endDate":"2026-01-08T00:00:00.000Z","discountSetting":{"discountPercentage":0}}]}]}},{"id":"paid","title":"Paid offer"}]}}}}"#), now: ISO8601DateFormatter().date(from: "2026-01-02T00:00:00Z")!)
        check(epic.count == 1 && epic[0].image?.lastPathComponent == "tall.jpg", "Epic active free promotion and tall artwork")
        check(try EpicStoreOffer.decode(data("{}" )).isEmpty, "Epic absent data is hidden")
    }
}
'''
with tempfile.TemporaryDirectory(prefix='steam-store-') as folder:
    work = Path(folder)
    (work / 'checks.swift').write_text(checks)
    subprocess.run([os.environ.get('SWIFTC') or shutil.which('swiftc'), '-parse-as-library', '-swift-version', '5',
                    '-module-cache-path', str(work / 'cache'), str(ROOT / 'app/Madeira/Store/SteamStore.swift'),
                    str(ROOT / 'app/Madeira/SwiftSteam/Auth/SteamTokenStore.swift'), str(work / 'checks.swift'), '-o', str(work / 'checks')], check=True)
    subprocess.run([str(work / 'checks')], check=True)
