import Foundation

/// Epic identifiers stay separate from Steam app IDs. The presentation adapter lets
/// both stores use the exact same price, credits and screenshot components.
struct EpicStoreGame: Identifiable {
    let offerID: String
    let namespace: String
    let catalogIDs: [String]
    let url: URL?
    var presentation: StoreGame
    var promotionStart: Date?
    var promotionEnd: Date?
    var id: String { namespace + ":" + offerID }
    var promotionLabel: String? {
        guard let start = promotionStart, let end = promotionEnd else { return nil }
        return start > Date() ? "Free \(start.formatted(date: .abbreviated, time: .omitted))" : "Free until \(end.formatted(date: .abbreviated, time: .omitted))"
    }
    func matches(namespace: String, catalogItemID: String?, title: String) -> Bool {
        if !namespace.isEmpty && namespace == self.namespace { return true }
        if let catalogItemID, !catalogItemID.isEmpty,
           catalogItemID == offerID || catalogIDs.contains(catalogItemID) { return true }
        func normalized(_ value: String) -> String {
            value.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "en_US_POSIX"))
                .split(whereSeparator: \.isWhitespace).joined(separator: " ")
        }
        let name = normalized(title)
        return !name.isEmpty && name == normalized(presentation.name)
    }
}

struct EpicStoreShelf: Identifiable {
    let title: String
    let games: [EpicStoreGame]
    var id: String { title }
}

enum EpicStoreDecoding {
    static let order = ["Featured", "Free Games", "Top Sellers", "Most Played", "Top Player Rated",
                        "Epic Savings Spotlight", "Top New Releases", "Trending", "Top Upcoming Wishlisted",
                        "Coming Soon", "Free to play", "Most Popular", "Featured from Epic First Run"]
    static func date(_ value: Any?) -> Date? {
        guard let value = value as? String else { return nil }
        let fractional = ISO8601DateFormatter(); fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }
    static func game(_ row: [String: Any]) -> EpicStoreGame? {
        guard let id = row["id"] as? String, !id.isEmpty,
              let title = row["title"] as? String, !title.isEmpty else { return nil }
        let type = (row["offerType"] as? String ?? "BASE_GAME").uppercased()
        guard type == "BASE_GAME" else { return nil }
        let images = row["keyImages"] as? [[String: Any]] ?? []
        func image(_ types: [String]) -> URL? {
            types.lazy.compactMap { type in
                images.first { ($0["type"] as? String)?.lowercased() == type.lowercased() }.flatMap { StoreDecoding.url($0["url"]) }
            }.first
        }
        let price = (row["price"] as? [String: Any])?["totalPrice"] as? [String: Any] ?? [:]
        let formatted = price["fmtPrice"] as? [String: Any] ?? [:]
        let amount = price["discountPrice"] as? Int
        let original = price["originalPrice"] as? Int
        let free = amount == 0
        let discount = original.flatMap { original -> Int? in
            guard original > 0, let amount, amount < original else { return nil }
            return Int((Double(original - amount) / Double(original) * 100).rounded())
        } ?? 0
        let label = free ? "Free" : formatted["discountPrice"] as? String ?? formatted["originalPrice"] as? String ?? "TBA"
        var presentation = StoreGame(id: 0, name: title,
            header: image(["OfferImageWide", "DieselStoreFrontWide", "Thumbnail", "OfferImageTall"]),
            capsule: image(["OfferImageTall", "DieselGameBoxTall", "Thumbnail", "OfferImageWide"]),
            price: label.isEmpty ? "TBA" : label, discount: discount, isFree: free)
        presentation.description = StoreDecoding.plainText(row["description"] as? String ?? "")
        let seller = (row["seller"] as? [String: Any])?["name"] as? String
        presentation.developers = [row["developerDisplayName"] as? String ?? seller ?? ""].filter { !$0.isEmpty }
        presentation.publishers = [row["publisherDisplayName"] as? String ?? ""].filter { !$0.isEmpty }
        if let release = date(row["releaseDate"] ?? row["effectiveDate"]) {
            presentation.release = release.formatted(date: .abbreviated, time: .omitted)
        }
        presentation.genres = (row["tags"] as? [[String: Any]] ?? []).compactMap { $0["name"] as? String }
        var media = images.filter { ($0["type"] as? String)?.lowercased() == "featuredmedia" }
        media += row["featuredMedia"] as? [[String: Any]] ?? []
        var seenImages = Set<URL>()
        presentation.screenshots = media.compactMap { StoreDecoding.url($0["url"]) }
            .filter { seenImages.insert($0).inserted }.enumerated().map { StoreScreenshot(id: $0.offset, full: $0.element, thumbnail: $0.element) }
        let catalog = row["catalogNs"] as? [String: Any] ?? [:]
        let mappings = (catalog["mappings"] as? [[String: Any]] ?? []) + (row["offerMappings"] as? [[String: Any]] ?? [])
        let mapping = mappings.first { ($0["pageType"] as? String ?? "productHome") == "productHome" && !($0["pageSlug"] as? String ?? "").isEmpty }
        let slug = [mapping?["pageSlug"] as? String, row["productSlug"] as? String, row["urlSlug"] as? String]
            .compactMap { $0 }.first { !$0.isEmpty }
        let url = slug.map { URL(string: "https://store.epicgames.com")!.appendingPathComponent("p").appendingPathComponent($0) }
        return EpicStoreGame(offerID: id, namespace: row["namespace"] as? String ?? "",
            catalogIDs: (row["items"] as? [[String: Any]] ?? []).compactMap { $0["id"] as? String },
            url: url, presentation: presentation)
    }
    static func unique(_ games: [EpicStoreGame]) -> [EpicStoreGame] {
        var seen = Set<String>(); return games.filter { seen.insert($0.id).inserted }
    }
    static func storefront(_ data: Data) throws -> [EpicStoreShelf] { try storefront(pages: [data]) }
    /// The front page in order from its pages; a page that does not parse is skipped
    /// while another one does (its shelves are missing, the rest still show).
    static func storefront(pages: [Data]) throws -> [EpicStoreShelf] {
        var modules: [[String: Any]] = [], parsed = 0
        for data in pages {
            guard let page = try? self.modules(data) else { continue }
            modules += page; parsed += 1
        }
        guard parsed > 0 else { throw URLError(.cannotParseResponse) }
        var shelves: [String: [EpicStoreGame]] = [:]
        var featured = false
        func visit(_ modules: [[String: Any]]) {
            for module in modules {
                let type = module["type"] as? String ?? ""
                if type == "subModules" { visit(module["modules"] as? [[String: Any]] ?? []); continue }
                guard type == "group" || type == "brandedList" else { continue }
                var title = (module["title"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                if title.isEmpty && type == "brandedList" && !featured { title = "Featured"; featured = true }
                if title == "Top Free to Play" { title = "Free to play" }
                guard order.contains(title) else { continue }
                let games = (module["offers"] as? [[String: Any]] ?? []).compactMap { game($0["offer"] as? [String: Any] ?? $0) }
                shelves[title] = unique((shelves[title] ?? []) + games)
            }
        }
        visit(modules)
        return order.compactMap { title in
            guard let games = shelves[title], !games.isEmpty else { return nil }
            return EpicStoreShelf(title: title, games: games)
        }
    }
    static func modules(_ data: Data) throws -> [[String: Any]] {
        let root = try StoreDecoding.object(data)
        // A partial GraphQL errors array does not invalidate usable data.
        guard let storefront = (root["data"] as? [String: Any])?["Storefront"] as? [String: Any],
              let page = storefront["storefrontModulesPaginated"] as? [String: Any],
              let modules = page["modules"] as? [[String: Any]] else { throw URLError(.cannotParseResponse) }
        return modules
    }
    static func elements(_ data: Data) throws -> [[String: Any]] {
        let root = try StoreDecoding.object(data)
        guard let catalog = (root["data"] as? [String: Any])?["Catalog"] as? [String: Any],
              let search = catalog["searchStore"] as? [String: Any],
              let elements = search["elements"] as? [[String: Any]] else { throw URLError(.cannotParseResponse) }
        return elements
    }
    static func search(_ data: Data) throws -> [EpicStoreGame] { unique(try elements(data).compactMap(game)) }
    static func promotions(_ data: Data, now: Date = Date()) throws -> [EpicStoreGame] {
        let offers = try EpicStoreOffer.decode(data, now: now)
        let rows = try elements(data)
        return unique(offers.compactMap { offer in
            guard let row = rows.first(where: { $0["id"] as? String == offer.id }), var game = game(row) else { return nil }
            game.promotionStart = offer.start; game.promotionEnd = offer.end
            if offer.start <= now { game.presentation.isFree = true; game.presentation.price = "Free" }
            return game
        })
    }
    static func searchBody(_ term: String, country: String) throws -> Data {
        func quoted(_ value: String) throws -> String {
            String(decoding: try JSONSerialization.data(withJSONObject: value, options: [.fragmentsAllowed]), as: UTF8.self)
        }
        let cc = try quoted(country), keywords = try quoted(term)
        let query = "query{Catalog{searchStore(category:\"games/edition/base\",count:40,country:\(cc),locale:\"en-US\",keywords:\(keywords)){elements{title id namespace offerType description keyImages{type url} seller{name} developerDisplayName publisherDisplayName effectiveDate productSlug urlSlug catalogNs{mappings(pageType:\"productHome\"){pageSlug}} price(country:\(cc)){totalPrice{discountPrice originalPrice discount fmtPrice(locale:\"en-US\"){originalPrice discountPrice}}}}}}}"
        return try JSONSerialization.data(withJSONObject: ["query": query], options: .sortedKeys)
    }
}

/// Public responses only, cached for three hours on this actor. Disk access and
/// decoding never run on the main actor; concurrent identical requests coalesce.
actor EpicStore {
    static let shared = EpicStore()
    static var country: String { Locale.current.region?.identifier.uppercased() ?? "US" }
    private struct Cached: Codable { let date: Date; let data: Data }
    private var memory: [String: Cached] = [:]
    private var pending: [String: Task<Data, Error>] = [:]
    private let ttl: TimeInterval = 3 * 3600
    private let directory = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("MadeiraEpicStore", isDirectory: true)

    /// Epic's server takes about 7 s to send the 20-module page (1.6 MB) in one piece and
    /// about 2.5 s as four pieces of five fetched together.
    func storefront() async throws -> [EpicStoreShelf] {
        let pages = try await withThrowingTaskGroup(of: (Int, Data?).self) { group in
            for start in stride(from: 0, to: 20, by: 5) {
                let req = get("https://store-site-backend-static-ipv4.ak.epicgames.com/storefrontLayout",
                              extra: [URLQueryItem(name: "start", value: String(start)), URLQueryItem(name: "count", value: "5")])
                group.addTask { (start, try? await self.request(req)) }
            }
            var pages: [(Int, Data?)] = []
            for try await page in group { pages.append(page) }
            return pages.sorted { $0.0 < $1.0 }.compactMap(\.1)
        }
        return try EpicStoreDecoding.storefront(pages: pages)
    }
    /// store-site-backend-static.epicgames.com no longer resolves; the -ipv4.ak host serves the same file.
    func freeGames() async throws -> [EpicStoreGame] {
        try EpicStoreDecoding.promotions(await request(get("https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions")))
    }
    func search(_ term: String) async throws -> [EpicStoreGame] {
        var req = URLRequest(url: URL(string: "https://launcher.store.epicgames.com/graphql")!)
        req.httpMethod = "POST"
        req.setValue("EpicGamesLauncher/16.0.0-Windows", forHTTPHeaderField: "User-Agent")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try EpicStoreDecoding.searchBody(term, country: Self.country)
        return try EpicStoreDecoding.search(await request(req))
    }
    private func get(_ path: String, extra: [URLQueryItem] = []) -> URLRequest {
        var url = URLComponents(string: path)!
        url.queryItems = [URLQueryItem(name: "locale", value: "en-US"), URLQueryItem(name: "country", value: Self.country)] + extra
        return URLRequest(url: url.url!)
    }
    private func request(_ request: URLRequest) async throws -> Data {
        let key = request.url!.absoluteString + "|" + String(decoding: request.httpBody ?? Data(), as: UTF8.self)
        let filename = key.utf8.reduce(UInt64(14695981039346656037)) { ($0 ^ UInt64($1)) &* 1099511628211 }
        let file = directory.appendingPathComponent(String(filename, radix: 16) + ".json")
        if let cached = memory[key], Date().timeIntervalSince(cached.date) < ttl { return cached.data }
        if let data = try? Data(contentsOf: file), let cached = try? JSONDecoder().decode(Cached.self, from: data), Date().timeIntervalSince(cached.date) < ttl {
            memory[key] = cached; return cached.data
        }
        if let task = pending[key] { return try await task.value }
        let task = Task<Data, Error> {
            var req = request; req.timeoutInterval = 30
            let (data, response) = try await URLSession.shared.data(for: req)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { throw URLError(.badServerResponse) }
            // Do not cache an errors-only GraphQL response as an empty store.
            if req.url?.path == "/storefrontLayout" { _ = try EpicStoreDecoding.storefront(data) }
            else { _ = try EpicStoreDecoding.elements(data) }
            return data
        }
        pending[key] = task
        defer { pending[key] = nil }
        let data = try await task.value
        let cached = Cached(date: Date(), data: data); memory[key] = cached
        if memory.count > 256 { memory = [key: cached] }
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        if let encoded = try? JSONEncoder().encode(cached) { try? encoded.write(to: file, options: .atomic) }
        return data
    }
}

struct EpicStoreOffer: Identifiable {
    let id: String
    let title: String
    let image: URL?
    let url: URL
    let start: Date
    let end: Date
    var upcoming: Bool { start > Date() }
    static func decode(_ data: Data, now: Date = Date()) throws -> [Self] {
        let root = try StoreDecoding.object(data)
        let catalog = (root["data"] as? [String: Any])?["Catalog"] as? [String: Any]
        let search = catalog?["searchStore"] as? [String: Any]
        let elements = search?["elements"] as? [[String: Any]] ?? []
        let formatter = ISO8601DateFormatter(); formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let simple = ISO8601DateFormatter()
        func date(_ s: Any?) -> Date? { guard let s = s as? String else { return nil }; return formatter.date(from: s) ?? simple.date(from: s) }
        var seen = Set<String>()
        return elements.compactMap { row in
            guard let id = row["id"] as? String, !seen.contains(id), let title = row["title"] as? String,
                  let promotions = row["promotions"] as? [String: Any] else { return nil }
            let groups = (promotions["promotionalOffers"] as? [[String: Any]] ?? []) + (promotions["upcomingPromotionalOffers"] as? [[String: Any]] ?? [])
            let offers = groups.flatMap { $0["promotionalOffers"] as? [[String: Any]] ?? [] }
            guard let offer = offers.first(where: {
                let discount = $0["discountSetting"] as? [String: Any]
                return discount?["discountPercentage"] as? Int == 0 && (date($0["endDate"]) ?? .distantPast) > now
            }), let start = date(offer["startDate"]), let end = date(offer["endDate"]) else { return nil }
            guard let game = EpicStoreDecoding.game(row), let url = game.url else { return nil }
            seen.insert(id)
            return Self(id: id, title: title, image: game.presentation.capsule ?? game.presentation.header, url: url, start: start, end: end)
        }
    }
}

