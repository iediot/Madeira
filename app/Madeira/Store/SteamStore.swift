import Foundation

struct StoreGame: Identifiable {
    let id: Int
    let name: String
    var header: URL?
    var capsule: URL?
    var price: String
    var discount: Int
    var isFree: Bool
    var description = ""
    var developers: [String] = []
    var publishers: [String] = []
    var release = ""
    var genres: [String] = []
    var platforms: [String] = []
    var screenshots: [StoreScreenshot] = []
    var movies: [StoreMovie] = []
}
struct StoreScreenshot: Identifiable {
    let id: Int
    let full: URL
    let thumbnail: URL
}
struct StoreMovie: Identifiable {
    let id: Int
    let name: String
    let thumbnail: URL?
    let url: URL
}
enum StoreAction: Equatable {
    case owned, get, buy
    static func decide(owned: Bool, isFree: Bool) -> Self { owned ? .owned : isFree ? .get : .buy }
}

/// Tolerates absent fields and skips malformed records without losing a whole shelf.
enum StoreDecoding {
    static func url(_ value: Any?) -> URL? {
        guard let s = value as? String, let u = URL(string: s), ["https", "http"].contains(u.scheme?.lowercased() ?? "") else { return nil }
        return u
    }
    static func game(_ j: [String: Any], id: Int? = nil) -> StoreGame? {
        guard let id = id ?? j["id"] as? Int ?? j["steam_appid"] as? Int, id > 0,
              let name = j["name"] as? String, !name.isEmpty else { return nil }
        let p = j["price_overview"] as? [String: Any] ?? [:]
        let free = j["is_free"] as? Bool ?? false
        var price = p["final_formatted"] as? String ?? j["final_formatted"] as? String ?? ""
        // Search and featured responses use integer minor units and an ISO currency.
        let searchPrice = j["price"] as? [String: Any] ?? [:]
        if price.isEmpty, let amount = (searchPrice["final"] ?? j["final_price"]) as? Int,
           let currency = (searchPrice["currency"] ?? j["currency"]) as? String {
            let formatter = NumberFormatter(); formatter.numberStyle = .currency; formatter.currencyCode = currency
            price = formatter.string(from: NSNumber(value: Double(amount) / 100)) ?? ""
        }
        var g = StoreGame(id: id, name: name, header: url(j["header_image"] ?? j["large_capsule_image"] ?? j["tiny_image"]),
                          capsule: url(j["capsule_imagev5"] ?? j["capsule_image"]), price: free ? "Free" : price,
                          discount: p["discount_percent"] as? Int ?? j["discount_percent"] as? Int ?? 0, isFree: free)
        g.description = plainText(j["short_description"] as? String ?? "")
        g.developers = j["developers"] as? [String] ?? []; g.publishers = j["publishers"] as? [String] ?? []
        g.release = (j["release_date"] as? [String: Any])?["date"] as? String ?? ""
        g.genres = (j["genres"] as? [[String: Any]] ?? []).compactMap { $0["description"] as? String }
        g.platforms = (j["platforms"] as? [String: Bool] ?? [:]).filter { $0.value }.map(\.key).sorted()
        g.screenshots = (j["screenshots"] as? [[String: Any]] ?? []).enumerated().compactMap { i, s in
            guard let full = url(s["path_full"]) else { return nil }
            return StoreScreenshot(id: i, full: full, thumbnail: url(s["path_thumbnail"]) ?? full)
        }
        g.movies = (j["movies"] as? [[String: Any]] ?? []).enumerated().compactMap { i, m in
            let mp4 = m["mp4"] as? [String: Any] ?? [:], webm = m["webm"] as? [String: Any] ?? [:]
            guard let video = url(m["hls_h264"]) ?? url(mp4["max"]) ?? url(mp4["480"]) ?? url(webm["max"]) ?? url(webm["480"]) else { return nil }
            return StoreMovie(id: i, name: m["name"] as? String ?? "Trailer", thumbnail: url(m["thumbnail"]), url: video)
        }
        return g
    }
    static func plainText(_ s: String) -> String {
        var text = s.replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
        for (entity, value) in [("&quot;", "\""), ("&#39;", "'"), ("&apos;", "'"), ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " "), ("&amp;", "&")] { text = text.replacingOccurrences(of: entity, with: value) }
        return text
    }
    static func object(_ data: Data) throws -> [String: Any] { try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:] }
    static func featured(_ data: Data) throws -> [String: [StoreGame]] {
        let root = try object(data)
        return Dictionary(uniqueKeysWithValues: ["specials", "top_sellers", "new_releases", "coming_soon", "free_to_play", "free"].map { key in
            (key, unique(((root[key] as? [String: Any])?["items"] as? [[String: Any]] ?? []).compactMap { game($0) }))
        })
    }
    static func carousel(_ data: Data) throws -> [StoreGame] {
        unique((try object(data)["featured_win"] as? [[String: Any]] ?? []).filter {
            // featured_win itself indicates Windows support; honour explicit exclusions.
            ($0["windows_available"] as? Bool ?? true) &&
            (($0["platforms"] as? [String: Bool])?["windows"] ?? true) &&
            ($0["type"] as? Int ?? 0) == 0
        }.compactMap { game($0) })
    }
    static func discovery(_ data: Data) throws -> [Int] {
        let response = try object(data)["response"] as? [String: Any] ?? [:]
        var seen = Set<Int>()
        return (response["appids"] as? [Int] ?? []).filter { $0 > 0 && seen.insert($0).inserted }
    }
    static func browse(_ data: Data, order: [Int]) throws -> [StoreGame] {
        let response = try object(data)["response"] as? [String: Any] ?? [:]
        let games = unique((response["store_items"] as? [[String: Any]] ?? []).compactMap { row -> StoreGame? in
            guard (row["success"] as? Int ?? 1) == 1, (row["item_type"] as? Int ?? 0) == 0,
                  let id = row["appid"] as? Int ?? row["id"] as? Int,
                  var game = game(row, id: id) else { return nil }
            let assets = row["assets"] as? [String: Any] ?? [:]
            func asset(_ key: String) -> URL? {
                guard let path = assets[key] as? String, !path.isEmpty else { return nil }
                if let absolute = url(path) { return absolute }
                guard let format = assets["asset_url_format"] as? String, !format.isEmpty else { return nil }
                let resolved = format.replacingOccurrences(of: "${FILENAME}", with: path)
                return url(resolved) ?? url("https://shared.fastly.steamstatic.com/store_item_assets/" + resolved)
            }
            game.header = asset("header") ?? game.header
            game.capsule = asset("library_capsule") ?? asset("main_capsule")
            let purchase = row["best_purchase_option"] as? [String: Any] ?? [:]
            game.isFree = purchase["is_free"] as? Bool ?? row["is_free"] as? Bool ?? false
            game.price = game.isFree ? "Free" : purchase["formatted_final_price"] as? String ?? ""
            game.discount = purchase["discount_pct"] as? Int ?? 0
            return game
        })
        let byID = Dictionary(uniqueKeysWithValues: games.map { ($0.id, $0) })
        return order.compactMap { byID[$0] }
    }
    static func tokenClaims(_ token: String) -> [String: Any] {
        let parts = token.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 3, parts[1].utf8.count <= 8192 else { return [:] }
        var encoded = String(parts[1]).replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        encoded += String(repeating: "=", count: (4 - encoded.count % 4) % 4)
        guard let data = Data(base64Encoded: encoded) else { return [:] }
        return (try? object(data)) ?? [:]
    }
    static func needsRefresh(_ token: String, now: Date = Date(), unauthorized: Bool = false) -> Bool {
        guard !unauthorized, !token.isEmpty, let exp = tokenClaims(token)["exp"] as? NSNumber else { return true }
        return exp.doubleValue <= now.timeIntervalSince1970
    }
    static func unique(_ games: [StoreGame]) -> [StoreGame] {
        var seen = Set<Int>(); return games.filter { seen.insert($0.id).inserted }
    }
    static func search(_ data: Data) throws -> [StoreGame] { unique((try object(data)["items"] as? [[String: Any]] ?? []).compactMap { game($0) }) }
    static func details(_ data: Data, id: Int) throws -> StoreGame? {
        let row = try object(data)[String(id)] as? [String: Any] ?? [:]
        guard row["success"] as? Bool == true, let value = row["data"] as? [String: Any] else { return nil }
        return game(value, id: id)
    }
}

/// Actor-owned disk/memory cache and one paced queue for appdetails, including coalescing.
actor SteamStore {
    static let shared = SteamStore()
    static var country: String { Locale.current.region?.identifier.lowercased() ?? "us" }
    private struct Cached: Codable { let date: Date; let data: Data }
    private var memory: [String: Cached] = [:]
    private var pending: [String: Task<Data, Error>] = [:]
    private var detailsTail: Task<Data, Error>?
    private var lastDetail = Date.distantPast
    private let ttl: TimeInterval = 3 * 3600
    private let directory = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("MadeiraStore", isDirectory: true)

    func featured() async throws -> [String: [StoreGame]] { try StoreDecoding.featured(await request("featuredcategories")) }
    func carousel() async throws -> [StoreGame] { try StoreDecoding.carousel(await request("featured/")) }

    /// No credential-bearing URL or refresh response is placed in either cache.
    private let credentialSession: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: configuration)
    }()
    private enum AuthenticationError: Error { case unauthorized, invalid }

    /// Steam's review summary (count, percent positive) for many apps in one public
    /// IStoreBrowseService/GetItems call per hundred; apps it does not rate are absent.
    func ratings(_ ids: [Int]) async -> [Int: (count: Int, percent: Int)] {
        var out: [Int: (count: Int, percent: Int)] = [:]
        let unique = Array(Set(ids)).sorted()
        for start in stride(from: 0, to: unique.count, by: 100) {
            let chunk = Array(unique[start..<min(start + 100, unique.count)])
            let input: [String: Any] = ["ids": chunk.map { ["appid": $0] },
                "context": ["language": "english", "country_code": Self.country.uppercased()],
                "data_request": ["include_reviews": true]]
            guard let url = try? webURL("IStoreBrowseService/GetItems/v1/", input: input),
                  let data = try? await request(url: url),
                  let response = (try? StoreDecoding.object(data))?["response"] as? [String: Any] else { continue }
            for row in response["store_items"] as? [[String: Any]] ?? [] {
                guard let id = row["appid"] as? Int ?? row["id"] as? Int,
                      let reviews = row["reviews"] as? [String: Any],
                      let summary = reviews["summary_filtered"] as? [String: Any] ?? reviews["summary_unfiltered"] as? [String: Any]
                else { continue }
                out[id] = (summary["review_count"] as? Int ?? 0, summary["percent_positive"] as? Int ?? 0)
            }
        }
        return out
    }

    /// One of Steam's ranked store lists (its search: top sellers, specials, popular
    /// new and upcoming), up to 60 games in Steam's order, resolved to art and prices in
    /// one GetItems call. Empty when either step fails. `filter` is the search's query,
    /// e.g. "filter=topsellers" or "specials=1"; category1=998 keeps games only.
    func ranked(_ filter: String, count: Int = 60) async -> [StoreGame] {
        guard let url = URL(string: "https://store.steampowered.com/search/results/?\(filter)&json=1&count=\(count)&category1=998&cc=\(Self.country)&l=english"),
              let data = try? await request(url: url),
              let items = (try? StoreDecoding.object(data))?["items"] as? [[String: Any]] else { return [] }
        let ids = items.compactMap { item -> Int? in
            guard let logo = item["logo"] as? String,
                  let range = logo.range(of: #"/apps/(\d+)/"#, options: .regularExpression) else { return nil }
            return Int(logo[range].filter(\.isNumber))
        }
        guard !ids.isEmpty else { return [] }
        let input: [String: Any] = ["ids": ids.map { ["appid": $0] },
            "context": ["language": "english", "country_code": Self.country.uppercased()],
            "data_request": ["include_assets": true, "include_basic_info": true]]
        guard let browse = try? webURL("IStoreBrowseService/GetItems/v1/", input: input),
              let resolved = try? await request(url: browse) else { return [] }
        return (try? StoreDecoding.browse(resolved, order: ids)) ?? []
    }
    /// A game's real artwork addresses from Steam's store service. Newer games keep
    /// their art under hashed paths (apps/<id>/<hash>/header.jpg), so the plain
    /// addresses the library guesses 404 for them (Bills Must Be Paid). The tall
    /// capsule first, then the header.
    func artwork(_ id: Int, wide: Bool = false) async -> [URL] {
        let input: [String: Any] = ["ids": [["appid": id]],
            "context": ["language": "english", "country_code": Self.country.uppercased()],
            "data_request": ["include_assets": true, "include_basic_info": true]]
        guard let url = try? webURL("IStoreBrowseService/GetItems/v1/", input: input),
              let data = try? await request(url: url),
              let game = (try? StoreDecoding.browse(data, order: [id]))?.first else { return [] }
        return (wide ? [game.header, game.capsule] : [game.capsule, game.header]).compactMap { $0 }
    }

    /// Steam's "Popular Upcoming", ranked by wishlists.
    func popularUpcoming() async -> [StoreGame] { await ranked("filter=popularcomingsoon", count: 40) }

    func recommended() async -> [StoreGame] {
        guard var tokens = SteamTokenStore().loadTokens(), !tokens.refreshToken.isEmpty,
              let subject = StoreDecoding.tokenClaims(tokens.refreshToken)["sub"] as? String,
              let steamID = UInt64(subject) else { return [] }
        let refreshExpiry = StoreDecoding.tokenClaims(tokens.refreshToken)["exp"] as? NSNumber
        guard refreshExpiry.map({ $0.doubleValue > Date().timeIntervalSince1970 }) ?? true else { return [] }
        func stillSignedIn() -> Bool { SteamTokenStore().loadTokens()?.refreshToken == tokens.refreshToken }
        do {
            if StoreDecoding.needsRefresh(tokens.accessToken) { tokens = try await refresh(tokens, steamID: steamID) }
            let input: [String: Any] = ["queue_type": 0, "country_code": Self.country.uppercased(),
                "rebuild_queue": false, "settings": [String: String](), "include_coming_soon": false]
            let key = "discovery/\(subject)/\(Self.country)"
            var data: Data
            do { data = try await queue(input, token: tokens.accessToken, key: key) }
            catch AuthenticationError.unauthorized {
                tokens = try await refresh(tokens, steamID: steamID)
                data = try await queue(input, token: tokens.accessToken, key: key)
            }
            let ids = try StoreDecoding.discovery(data)
            guard !ids.isEmpty, stillSignedIn() else { return [] }
            let browseInput: [String: Any] = ["ids": ids.map { ["appid": $0] },
                "context": ["language": "english", "country_code": Self.country.uppercased()],
                "data_request": ["include_assets": true, "include_basic_info": true]]
            let url = try webURL("IStoreBrowseService/GetItems/v1/", input: browseInput)
            let items = try StoreDecoding.browse(await request(url: url), order: ids)
            guard stillSignedIn(), !Task.isCancelled else { return [] }
            return items
        } catch {
            // Sign-out/account changes and all API failures simply hide this shelf.
            return []
        }
    }
    private func webURL(_ path: String, input: [String: Any], token: String? = nil) throws -> URL {
        var c = URLComponents(string: "https://api.steampowered.com/" + path)!
        c.queryItems = [URLQueryItem(name: "input_json", value: String(decoding: try JSONSerialization.data(withJSONObject: input, options: .sortedKeys), as: UTF8.self))]
        if let token { c.queryItems?.append(URLQueryItem(name: "access_token", value: token)) }
        return c.url!
    }
    private func queue(_ input: [String: Any], token: String, key: String) async throws -> Data {
        try await request(url: webURL("IStoreService/GetDiscoveryQueue/v1/", input: input, token: token), cacheKey: key, privateResponse: true)
    }
    private func refresh(_ tokens: SteamTokenStore.StoredTokens, steamID: UInt64) async throws -> SteamTokenStore.StoredTokens {
        var req = URLRequest(url: URL(string: "https://api.steampowered.com/IAuthenticationService/GenerateAccessTokenForApp/v1/")!)
        req.httpMethod = "POST"; req.timeoutInterval = 25
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        var form = URLComponents()
        form.queryItems = [URLQueryItem(name: "refresh_token", value: tokens.refreshToken), URLQueryItem(name: "steamid", value: String(steamID))]
        req.httpBody = Data((form.percentEncodedQuery ?? "").replacingOccurrences(of: "+", with: "%2B").utf8)
        let (data, response) = try await credentialSession.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode),
              let result = try StoreDecoding.object(data)["response"] as? [String: Any],
              let access = result["access_token"] as? String, !access.isEmpty,
              SteamTokenStore().loadTokens()?.refreshToken == tokens.refreshToken else { throw AuthenticationError.invalid }
        var updated = tokens
        updated.accessToken = access
        if let refresh = result["refresh_token"] as? String, !refresh.isEmpty { updated.refreshToken = refresh }
        updated.steamID = steamID
        SteamTokenStore().saveTokens(accountName: updated.accountName, refreshToken: updated.refreshToken, accessToken: updated.accessToken, steamID: steamID)
        return updated
    }
    func search(_ term: String) async throws -> [StoreGame] { try StoreDecoding.search(await request("storesearch/", query: [URLQueryItem(name: "term", value: term)])) }
    func details(_ id: Int) async throws -> StoreGame? { try StoreDecoding.details(await request("appdetails", query: [URLQueryItem(name: "appids", value: String(id))], detail: true), id: id) }
    func reviews(_ id: Int) async throws -> StoreReviews {
        try StoreReviews.decode(await request("appreviews/\(id)", query: [
            URLQueryItem(name: "json", value: "1"), URLQueryItem(name: "language", value: "english"),
            URLQueryItem(name: "filter", value: "summary"), URLQueryItem(name: "purchase_type", value: "all"),
            URLQueryItem(name: "num_per_page", value: "6")]))
    }
    /// One page of every review, most helpful first; `cursor` from the previous page ("*" first).
    func reviewPage(_ id: Int, cursor: String) async throws -> StoreReviews {
        try StoreReviews.decode(await request("appreviews/\(id)", query: [
            URLQueryItem(name: "json", value: "1"), URLQueryItem(name: "language", value: "english"),
            URLQueryItem(name: "filter", value: "all"), URLQueryItem(name: "review_type", value: "all"),
            URLQueryItem(name: "purchase_type", value: "all"), URLQueryItem(name: "num_per_page", value: "20"),
            URLQueryItem(name: "cursor", value: cursor)]))
    }

    private func request(_ path: String, query: [URLQueryItem] = [], detail: Bool = false) async throws -> Data {
        let base = path.hasPrefix("appreviews/") ? "https://store.steampowered.com/" + path   // not under /api
            : "https://store.steampowered.com/api/" + path
        var c = URLComponents(string: base)!
        c.queryItems = [URLQueryItem(name: "cc", value: Self.country), URLQueryItem(name: "l", value: "english")] + query
        return try await request(url: c.url!, detail: detail)
    }
    private func request(url: URL, detail: Bool = false, cacheKey: String? = nil, privateResponse: Bool = false) async throws -> Data {
        let key = cacheKey ?? url.absoluteString
        // Stable filename without hashing dependencies; bounded even for long searches.
        let filename = key.utf8.reduce(UInt64(14695981039346656037)) { ($0 ^ UInt64($1)) &* 1099511628211 }
        let file = directory.appendingPathComponent(String(filename, radix: 16) + ".json")
        if let cached = memory[key], Date().timeIntervalSince(cached.date) < ttl { return cached.data }
        if !privateResponse, let data = try? Data(contentsOf: file), let cached = try? JSONDecoder().decode(Cached.self, from: data), Date().timeIntervalSince(cached.date) < ttl {
            memory[key] = cached; return cached.data
        }
        if let task = pending[key] { return try await task.value }
        let previous = detail ? detailsTail : nil
        let task = Task<Data, Error> {
            if let previous { _ = try? await previous.value }
            if detail { try await self.paceDetails() }
            var req = URLRequest(url: url); req.timeoutInterval = 25
            let (data, response) = try await (privateResponse ? credentialSession : URLSession.shared).data(for: req)
            if (response as? HTTPURLResponse)?.statusCode == 401 { throw AuthenticationError.unauthorized }
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { throw URLError(.badServerResponse) }
            _ = try StoreDecoding.object(data)
            return data
        }
        pending[key] = task
        if detail { detailsTail = task }
        defer { pending[key] = nil }
        let data = try await task.value
        let cached = Cached(date: Date(), data: data); memory[key] = cached
        if memory.count > 256 { memory = [key: cached] }
        if privateResponse { return data }
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        if let encoded = try? JSONEncoder().encode(cached) { try? encoded.write(to: file, options: .atomic) }
        return data
    }
    private func paceDetails() async throws {
        let delay = 1.6 - Date().timeIntervalSince(lastDetail)
        if delay > 0 { try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000)) }
        lastDetail = Date()
    }
}


/// A game's Steam reviews: the summary Steam shows ("Very Positive", 92% of 12,345)
/// and a few of the most helpful reviews.
struct StoreReviews {
    struct Review: Identifiable {
        let id: String
        let text: String
        let recommended: Bool
        let hours: Int
        let helpful: Int
        let date: Date
    }
    var summary = ""
    var positive = 0
    var total = 0
    var reviews: [Review] = []
    /// Steam's cursor for the next page.
    var cursor = ""
    var percent: Int? { total > 0 ? Int((Double(positive) / Double(total) * 100).rounded()) : nil }

    static func decode(_ data: Data) throws -> StoreReviews {
        let j = try StoreDecoding.object(data)
        var r = StoreReviews()
        let q = j["query_summary"] as? [String: Any] ?? [:]
        r.summary = q["review_score_desc"] as? String ?? ""
        r.positive = q["total_positive"] as? Int ?? 0
        r.total = q["total_reviews"] as? Int ?? 0
        r.cursor = j["cursor"] as? String ?? ""
        r.reviews = (j["reviews"] as? [[String: Any]] ?? []).compactMap { v in
            guard let text = (v["review"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
            let author = v["author"] as? [String: Any] ?? [:]
            let bare = text.replacingOccurrences(of: #"\[/?[a-zA-Z0-9*]+(=[^\]]*)?\]"#, with: "", options: .regularExpression)
            return Review(id: v["recommendationid"] as? String ?? UUID().uuidString, text: StoreDecoding.plainText(bare),
                          recommended: v["voted_up"] as? Bool ?? true,
                          hours: (author["playtime_forever"] as? Int ?? 0) / 60, helpful: v["votes_up"] as? Int ?? 0,
                          date: Date(timeIntervalSince1970: TimeInterval(v["timestamp_created"] as? Int ?? 0)))
        }
        return r
    }
}
