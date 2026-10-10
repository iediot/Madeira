import SwiftUI
import AVKit
import SafariServices

/// ArtworkCache with ordered fallbacks (downsized off the main thread). No
/// GeometryReader: the image fills whatever frame the caller gives it.
struct StoreArtwork: View {
    let urls: [URL]
    var fit = false
    @State private var image: UIImage?
    var body: some View {
        Rectangle().fill(Color(uiColor: .secondarySystemFill))
            .overlay {
                if let image {
                    Image(uiImage: image).resizable().aspectRatio(contentMode: fit ? .fit : .fill)
                }
            }
            .clipped()
            .task(id: urls) {
                if let hit = urls.lazy.compactMap({ ArtworkCache.cached($0) }).first { image = hit; return }
                image = nil
                for url in urls {
                    guard !Task.isCancelled else { return }
                    if let loaded = await ArtworkCache.image(url) { image = loaded; break }
                }
            }
    }
}

struct StoreExpandedShelf {
    let title: String
    let items: [StoreGame]
}

struct SteamStorePage: View {
    @Binding var search: String
    let open: (LibraryEntry) -> Void
    @ObservedObject private var steam = SteamOwnedLibrary.shared
    @ObservedObject private var installed = SteamGamesModel.shared
    @State private var shelves: [String: [StoreGame]] = [:]
    @State private var recommendations: [StoreGame] = []
    @State private var recommendationRevision = 0
    @State private var results: [StoreGame] = []
    @State private var selection: StoreGame?
    @State private var expanded: StoreExpandedShelf?
    @State private var loading = true
    @State private var searching = false
    @State private var error: String?
    @State private var searchError: String?
    @State private var retry = 0
    private var query: String { search.trimmingCharacters(in: .whitespacesAndNewlines) }
    /// Which content the page shows; a change crossfades it (shelves, a See all grid, search).
    private var phase: String { !query.isEmpty ? "search" : expanded.map { "all:" + $0.title } ?? "shelves" }

    var body: some View {
        GeometryReader { geo in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 24) {
                    if query.isEmpty, let expanded {
                        HStack(spacing: 12) {
                            Button { withAnimation(.snappy) { self.expanded = nil } } label: {
                                Image(systemName: "chevron.left").font(.headline).frame(width: 40, height: 40).libraryRowGlass(Circle())
                            }.buttonStyle(.plain).accessibilityLabel("Back to the store")
                            Text(expanded.title).font(.title2.bold())
                        }.padding(.horizontal, LibraryLayout.margin(geo.size.width))
                        grid(expanded.items, width: geo.size.width)
                    } else if !query.isEmpty {
                        if searching { ProgressView().frame(maxWidth: .infinity) }
                        else if let searchError { failure(searchError) }
                        else if results.isEmpty { ContentUnavailableView.search(text: query) }
                        else { grid(results, width: geo.size.width) }
                    } else {
                        if loading { ProgressView().frame(maxWidth: .infinity) }
                        if let error { failure(error) }
                        shelf("Featured & Recommended", key: "featured", width: geo.size.width)
                        if steam.signedIn && !recommendations.isEmpty {
                            LibraryShelf(title: "Recommended for you", items: recommendations, width: geo.size.width,
                                         seeAll: { show("Recommended for you", recommendations) }) { game in card(game) }
                        }
                        shelf("Specials", key: "specials", width: geo.size.width)
                        shelf("Top sellers", key: "top_sellers", width: geo.size.width)
                        shelf("New releases", key: "new_releases", width: geo.size.width)
                        shelf("Coming soon", key: "coming_soon", width: geo.size.width)
                        shelf("Free to play", key: "free_to_play", width: geo.size.width)
                    }
                }.padding(.vertical, 16).padding(.bottom, 24)
                .animation(.easeOut(duration: 0.25), value: loading)
                .animation(.easeOut(duration: 0.2), value: searching)
                .id(phase).transition(.opacity)
            }
            .animation(.easeOut(duration: 0.22), value: phase)
            .refreshable { recommendationRevision += 1; await load() }
        }
        .background(Color(uiColor: .systemGroupedBackground).ignoresSafeArea())
        .task { installed.refresh(); steam.start(); await load() }
        .onReceive(NotificationCenter.default.publisher(for: SteamSignIn.didChange)) { _ in
            recommendations = []; recommendationRevision += 1
        }
        .task(id: recommendationRevision) {
            let games = await SteamStore.shared.recommended()
            guard !Task.isCancelled else { return }
            recommendations = games
        }
        .task(id: "\(query)|\(retry)") {
            let term = query
            guard !term.isEmpty else { results = []; searching = false; searchError = nil; return }
            searching = true; searchError = nil; results = []
            do {
                try await Task.sleep(nanoseconds: 300_000_000)
                let found = try await SteamStore.shared.search(term)
                try Task.checkCancellation()
                results = found; searching = false
            } catch {
                guard !Task.isCancelled else { return }
                searchError = "The store search could not load. Try again."; searching = false
            }
        }
        .sheet(item: $selection) { game in StoreGameSheet(game: game, open: open) }
    }
    private func failure(_ message: String) -> some View {
        VStack(spacing: 12) {
            Text(message).foregroundStyle(.secondary)
            Button("Try again") { if query.isEmpty { Task { await load() } } else { retry += 1 } }
                .buttonStyle(.plain).padding(12).libraryRowGlass(Capsule())
        }.frame(maxWidth: .infinity).padding()
    }
    @ViewBuilder private func shelf(_ title: String, key: String, width: CGFloat, limit: Int? = nil) -> some View {
        let all = shelves[key] ?? []
        let items = Array(all.prefix(limit ?? Int.max))
        if !items.isEmpty {
            LibraryShelf(title: title, items: items, width: width, seeAll: { show(title, all) }) { game in card(game) }
        }
    }
    /// A shelf's See all: its every game as a grid, in place of the shelves.
    private func show(_ title: String, _ items: [StoreGame]) {
        withAnimation(.snappy) { expanded = StoreExpandedShelf(title: title, items: items) }
    }
    private func grid(_ games: [StoreGame], width: CGFloat) -> some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: LibraryLayout.shelfCard(width)), spacing: LibraryLayout.shelfGap(width))], spacing: 20) {
            ForEach(games) { game in card(game) }
        }.padding(.horizontal, LibraryLayout.margin(width))
    }
    private func card(_ game: StoreGame) -> some View {
        let isInstalled = installed.games.contains { $0.id == game.id }
        let owned = steam.game(game.id) != nil
        return Button { selection = game } label: {
            VStack(alignment: .leading, spacing: 6) {
                StoreArtwork(urls: SteamGamesRules.artwork(appID: game.id, owned: { steam.game($0) }) + [game.capsule, game.header].compactMap { $0 })
                    .aspectRatio(2.0 / 3, contentMode: .fit).clipShape(RoundedRectangle(cornerRadius: 10))
                    .modifier(LibraryCardArtworkPress())
                Text(game.name).font(.footnote.weight(.semibold)).lineLimit(2, reservesSpace: true).multilineTextAlignment(.leading)
                StorePrice(game: game, status: isInstalled ? "Installed" : owned ? "Owned" : nil)
                    .frame(height: LibraryLayout.pillRow, alignment: .leading)
            }.padding(4).foregroundStyle(.primary)
        }.libraryCardButtonStyle(grid: true)
    }
    /// Every shelf is filtered before it is shown, so no unrated game flashes up first.
    /// The featuredcategories lists hold about ten games each, too few once filtered, so
    /// Top sellers, Specials and New releases come from Steam's ranked search lists (60
    /// games each); Free to play still comes from featuredcategories.
    private func load() async {
        loading = shelves.isEmpty; error = nil
        async let carousel = try? SteamStore.shared.carousel()
        async let categories = try? SteamStore.shared.featured()
        async let top = SteamStore.shared.ranked("filter=topsellers")
        async let specials = SteamStore.shared.ranked("specials=1")
        async let new = SteamStore.shared.ranked("filter=popularnew&sort_by=Released_DESC")
        async let upcoming = SteamStore.shared.popularUpcoming()
        var next: [String: [StoreGame]] = [:]
        let loaded = await categories
        if let loaded {
            next["free_to_play"] = StoreDecoding.unique((loaded["free_to_play"] ?? []) + (loaded["free"] ?? []))
        }
        next["featured"] = await carousel ?? []
        let (topSellers, deals, recent, popular) = await (top, specials, new, upcoming)
        next["top_sellers"] = topSellers.isEmpty ? loaded?["top_sellers"] ?? [] : topSellers
        next["specials"] = deals.isEmpty ? loaded?["specials"] ?? [] : deals
        next["new_releases"] = recent.isEmpty ? loaded?["new_releases"] ?? [] : recent
        next["coming_soon"] = popular.isEmpty ? loaded?["coming_soon"] ?? [] : popular
        if next.values.allSatisfy(\.isEmpty) { error = "The Steam store could not load. Try again." }
        shelves = await keepWellRated(next)
        loading = false
    }
    /// Steam's public lists carry paid placements and every new or discounted game,
    /// slop included; its own front page leans on reviews and the account. Here the
    /// shelves keep games with plenty of reviews and a fair rating: the AAA titles and
    /// fan favourite indies, not the asset flips. Coming soon is Steam's wishlist-ranked
    /// Popular Upcoming (no reviews yet); the account's own recommendations stay as they
    /// are; when the ratings cannot load nothing is filtered. Featured & Recommended,
    /// Steam's short front carousel, is topped up with well-rated top sellers.
    private func keepWellRated(_ shelves: [String: [StoreGame]]) async -> [String: [StoreGame]] {
        let rules: [String: (count: Int, percent: Int)] = [
            "featured": (1000, 65), "specials": (1000, 65), "top_sellers": (1000, 60),
            "new_releases": (100, 60), "free_to_play": (1000, 60)]
        let ids = rules.keys.flatMap { (shelves[$0] ?? []).map(\.id) }
        guard !ids.isEmpty else { return shelves }
        let ratings = await SteamStore.shared.ratings(ids)
        guard !ratings.isEmpty else { return shelves }
        var out = shelves
        for (key, rule) in rules {
            guard let games = out[key] else { continue }
            out[key] = games.filter { game in
                guard let r = ratings[game.id] else { return false }
                return r.count >= rule.count && r.percent >= rule.percent
            }
        }
        let featured = out["featured"] ?? []
        if featured.count < 15 {
            let have = Set(featured.map(\.id))
            out["featured"] = featured + (out["top_sellers"] ?? []).filter { !have.contains($0.id) }.prefix(15 - featured.count)
        }
        return out
    }
}

struct StorePrice: View {
    let game: StoreGame
    var status: String? = nil
    /// The game page's price beside its button, larger and in the primary colour.
    var large = false
    var body: some View {
        HStack(spacing: 4) {
            if let status { Text(status) }
            else {
                if game.discount > 0 { Text("−\(game.discount)%").foregroundStyle(.green) }
                Text(game.price.isEmpty ? "TBA" : game.price)
            }
        }.font(large ? .title3.weight(.semibold) : .caption.weight(.medium))
            .foregroundStyle(large ? .primary : .secondary).lineLimit(1)
    }
}

struct StoreGameSheet: View {
    let game: StoreGame
    let open: (LibraryEntry) -> Void
    @ObservedObject private var steam = SteamOwnedLibrary.shared
    @ObservedObject private var installed = SteamGamesModel.shared
    @Environment(\.dismiss) private var dismiss
    @State private var details: StoreGame?
    @State private var failure: String?
    @State private var busy = false
    @State private var buying = false
    @State private var library = false
    @State private var retry = 0
    private var current: StoreGame { details ?? game }
    private var owned: Bool { steam.game(game.id) != nil || installed.games.contains { $0.id == game.id } }
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 0) {
                    StoreArtwork(urls: [SteamCatalog.hero(game.id), current.header].compactMap { $0 })
                        .aspectRatio(16.0 / 9, contentMode: .fit)
                        .frame(maxWidth: 1100)
                    VStack(alignment: .leading, spacing: 22) {
                        StoreGameHeader(game: current)
                        actionRow
                        if let failure {
                            HStack {
                                Text(failure).font(.subheadline).foregroundStyle(.secondary)
                                if details == nil { Button("Try again") { retry += 1 }.buttonStyle(.plain).padding(.horizontal, 12).padding(.vertical, 6).libraryRowGlass(Capsule()) }
                            }
                        }
                        if details == nil && failure == nil { ProgressView().frame(maxWidth: .infinity) }
                        StoreMediaContent(game: current)
                        StoreGameAbout(game: current)
                        StoreReviewsView(appID: game.id)
                    }
                    .padding(.horizontal, 20).padding(.top, 20).padding(.bottom, 32)
                    // One readable column, centred on a wide screen.
                    .frame(maxWidth: 760)
                    // Genres, media and the description fade in when the details arrive.
                    .animation(.easeOut(duration: 0.25), value: details != nil)
                }
                .frame(maxWidth: .infinity)
            }.background(Color(uiColor: .systemGroupedBackground))
                .navigationTitle(current.name).navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
                .task(id: retry) {
                    failure = nil
                    do {
                        details = try await SteamStore.shared.details(game.id)
                        if details == nil { failure = "This game is unavailable in your region." }
                    } catch { failure = "Game details could not load. Try again." }
                }
                .sheet(isPresented: $buying, onDismiss: { Task { await steam.refreshLibrary(interactive: false) } }) {
                    StoreSafari(url: URL(string: "https://store.steampowered.com/app/\(game.id)/")!)
                }
                .sheet(isPresented: $library) {
                    SteamGameSheet(appID: game.id) { entry in
                        dismiss()
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) { open(entry) }
                    }
                }
        }
    }
    /// The price and the button side by side, the button taking the room left.
    private var actionRow: some View {
        HStack(spacing: 12) {
            if !owned { StorePrice(game: current, large: true) }
            Button { act() } label: {
                HStack(spacing: 8) {
                    if busy { ProgressView() }
                    Text(owned ? "In your library" : current.isFree ? "Get" : "Buy on Steam").fontWeight(.semibold)
                }
                .frame(maxWidth: .infinity, minHeight: 48).libraryRowGlass(Capsule())
            }.buttonStyle(.plain).disabled(busy || (!owned && details == nil))
        }
    }

    private func act() {
        switch StoreAction.decide(owned: owned, isFree: current.isFree) {
        case .owned: library = true
        case .buy: buying = true
        case .get:
            busy = true; failure = nil
            Task {
                defer { busy = false }
                do {
                    let info = try await steam.resolveApp(String(game.id))
                    guard try await steam.addApp(info) else { failure = "Steam could not grant a free license for this game."; return }
                    library = true
                } catch { failure = error.localizedDescription }
            }
        }
    }
}

struct StoreSafari: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> SFSafariViewController { SFSafariViewController(url: url) }
    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}

/// Loaded only when the media section enters the view hierarchy; shared by all game
/// pages (the existing ones put it in a Form row, so it is a plain scroll row: a paged
/// TabView in a Form row was re-measured on every scroll and made the pages stutter).
struct StoreGameMedia: View {
    let appID: Int
    @State private var game: StoreGame?
    var body: some View {
        VStack(alignment: .leading, spacing: 0) { if let game { StoreMediaContent(game: game) } }
            .task(id: appID) { game = try? await SteamStore.shared.details(appID) }
    }
}

/// One media strip as on Steam: the trailers first, then the screenshots, as cards the
/// same height; a trailer plays in a player sheet, a screenshot opens full screen.
struct StoreMediaContent: View {
    let game: StoreGame
    @State private var screenshot: StoreScreenshot?
    @State private var movie: StoreMovie?
    private enum Item: Identifiable {
        case movie(StoreMovie), shot(StoreScreenshot)
        var id: String { switch self { case .movie(let m): "m\(m.id)"; case .shot(let s): "s\(s.id)" } }
    }
    private var items: [Item] { game.movies.map(Item.movie) + game.screenshots.map(Item.shot) }
    var body: some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                Text("Media").font(.title3.bold())
                ScrollView(.horizontal, showsIndicators: false) {
                    LazyHStack(spacing: 10) {
                        ForEach(items) { item in tile(item) }
                    }
                    .scrollTargetLayout()
                }
                .scrollTargetBehavior(.viewAligned)
                .frame(height: 158)
            }
            .fullScreenCover(item: $screenshot) { shot in StoreScreenshotViewer(shots: game.screenshots, initial: shot.id) }
            .sheet(item: $movie) { StoreTrailer(movie: $0) }
        }
    }
    @ViewBuilder private func tile(_ item: Item) -> some View {
        switch item {
        case .movie(let trailer):
            Button { movie = trailer } label: {
                StoreArtwork(urls: [trailer.thumbnail].compactMap { $0 })
                    .frame(width: 280, height: 158)
                    .overlay { Image(systemName: "play.circle.fill").font(.system(size: 40)).foregroundStyle(.white).shadow(radius: 3) }
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }.buttonStyle(.plain).accessibilityLabel("Play \(trailer.name)")
        case .shot(let shot):
            Button { screenshot = shot } label: {
                StoreArtwork(urls: [shot.thumbnail]).frame(width: 280, height: 158)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }.buttonStyle(.plain).accessibilityLabel("Open screenshot \(shot.id + 1)")
        }
    }
}

/// Steam's review summary and a few of the most helpful reviews; See all reviews opens
/// the full list, which loads more pages as it scrolls.
struct StoreReviewsView: View {
    let appID: Int
    @State private var reviews: StoreReviews?
    @State private var all = false
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let reviews, reviews.total > 0 {
                HStack {
                    Text("Reviews").font(.title3.bold())
                    Spacer()
                    Button("See all") { all = true }
                        .font(.subheadline.weight(.medium)).buttonStyle(.plain)
                        .padding(.horizontal, 14).frame(minHeight: 34).libraryRowGlass(Capsule())
                }
                StoreReviewSummary(reviews: reviews)
                ForEach(reviews.reviews.prefix(4)) { StoreReviewCard(review: $0) }
            }
        }
        .animation(.easeOut(duration: 0.25), value: reviews != nil)
        .task(id: appID) { reviews = try? await SteamStore.shared.reviews(appID) }
        .sheet(isPresented: $all) { StoreAllReviews(appID: appID, summary: reviews) }
    }
}

struct StoreReviewSummary: View {
    let reviews: StoreReviews
    var body: some View {
        let percent = reviews.percent ?? 0
        HStack(spacing: 8) {
            Image(systemName: percent >= 70 ? "hand.thumbsup.fill" : percent >= 40 ? "hand.raised.fill" : "hand.thumbsdown.fill")
                .foregroundStyle(percent >= 70 ? Color.accentColor : .secondary)
            Text(reviews.summary).font(.headline)
            if let p = reviews.percent {
                Text("\(p)% of \(reviews.total.formatted())").font(.subheadline).foregroundStyle(.secondary)
            }
        }
    }
}

/// One review: six lines, with Read more for the rest.
struct StoreReviewCard: View {
    let review: StoreReviews.Review
    @State private var open = false
    private var long: Bool { review.text.count > 360 || review.text.filter { $0 == "\n" }.count > 5 }
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: review.recommended ? "hand.thumbsup.fill" : "hand.thumbsdown.fill")
                    .foregroundStyle(review.recommended ? Color.accentColor : .secondary)
                Text(review.recommended ? "Recommended" : "Not recommended").font(.subheadline.weight(.semibold))
                Spacer(minLength: 0)
                Text("\(review.hours) h played").font(.caption).foregroundStyle(.secondary)
            }
            // No text selection (on a long review it made Read more stutter), and a short
            // animation scoped to this card rather than the whole page's layout.
            Text(review.text).font(.subheadline).foregroundStyle(.secondary)
                .lineLimit(open ? nil : 6).fixedSize(horizontal: false, vertical: true)
            if long {
                Button(open ? "Show less" : "Read more") { open.toggle() }
                    .font(.subheadline.weight(.medium)).buttonStyle(.plain).foregroundStyle(Color.accentColor)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 14))
        // On the whole card, so the text, its button and the card's edge move together
        // (on the text alone the button jumped ahead of it).
        .animation(.easeOut(duration: 0.2), value: open)
    }
}

/// Every review, most helpful first, twenty at a time as the list scrolls (Steam's
/// cursor paging).
struct StoreAllReviews: View {
    let appID: Int
    let summary: StoreReviews?
    @Environment(\.dismiss) private var dismiss
    @State private var reviews: [StoreReviews.Review] = []
    @State private var cursor = "*"
    @State private var done = false
    @State private var loading = false
    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if let summary { StoreReviewSummary(reviews: summary).padding(.bottom, 4) }
                    ForEach(reviews) { StoreReviewCard(review: $0) }
                    if !done {
                        ProgressView().frame(maxWidth: .infinity).padding()
                            .onAppear { Task { await more() } }
                    }
                }
                .padding(20).frame(maxWidth: 760).frame(maxWidth: .infinity)
            }
            .background(Color(uiColor: .systemGroupedBackground))
            .navigationTitle("Reviews").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }
    private func more() async {
        guard !loading, !done else { return }
        loading = true; defer { loading = false }
        guard let page = try? await SteamStore.shared.reviewPage(appID, cursor: cursor) else { done = true; return }
        let seen = Set(reviews.map(\.id))
        let fresh = page.reviews.filter { !seen.contains($0.id) }
        reviews += fresh
        if fresh.isEmpty || page.cursor.isEmpty || page.cursor == cursor { done = true } else { cursor = page.cursor }
    }
}

/// Wraps its children onto as many lines as they need (the genre pills).
struct StoreFlow: Layout {
    var spacing: CGFloat = 6
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, line: CGFloat = 0, widest: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0 && x + size.width > width { y += line + spacing; x = 0; line = 0 }
            x += size.width + spacing; line = max(line, size.height); widest = max(widest, x - spacing)
        }
        return CGSize(width: min(widest, width), height: y + line)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, line: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > bounds.minX && x + size.width > bounds.maxX { y += line + spacing; x = bounds.minX; line = 0 }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing; line = max(line, size.height)
        }
    }
}

private struct StoreScreenshotViewer: View {
    let shots: [StoreScreenshot]
    @State private var selected: Int
    @Environment(\.dismiss) private var dismiss
    init(shots: [StoreScreenshot], initial: Int) { self.shots = shots; _selected = State(initialValue: initial) }
    var body: some View {
        NavigationStack {
            TabView(selection: $selected) {
                ForEach(shots) { shot in StoreArtwork(urls: [shot.full], fit: true).tag(shot.id) }
            }.tabViewStyle(.page).background(.black).preferredColorScheme(.dark)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }
}
private struct StoreTrailer: View {
    let movie: StoreMovie
    @State private var player: AVPlayer
    @Environment(\.dismiss) private var dismiss
    init(movie: StoreMovie) { self.movie = movie; _player = State(initialValue: AVPlayer(url: movie.url)) }
    var body: some View {
        NavigationStack {
            VideoPlayer(player: player).navigationTitle(movie.name).navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
                // The app sets the playback category only when a game starts; until then iOS's
                // default category follows the silent switch, so a trailer played mute.
                .onAppear { try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .moviePlayback); try? AVAudioSession.sharedInstance().setActive(true) }
                .onDisappear { player.pause() }
        }
    }
}

struct StoreGameHeader: View {
    let game: StoreGame
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(game.name).font(.largeTitle.bold()).fixedSize(horizontal: false, vertical: true)
            let credits = [game.developers.joined(separator: ", "),
                           game.publishers.isEmpty || game.publishers == game.developers ? "" : "Published by " + game.publishers.joined(separator: ", "),
                           game.release].filter { !$0.isEmpty }
            if !credits.isEmpty {
                Text(credits.joined(separator: " · ")).font(.subheadline).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !game.genres.isEmpty {
                StoreFlow(spacing: 6) {
                    ForEach(game.genres, id: \.self) { genre in
                        Text(genre).font(.caption.weight(.medium)).padding(.horizontal, 10).padding(.vertical, 5)
                            .background(Color(uiColor: .tertiarySystemFill), in: Capsule())
                    }
                }
            }
        }
    }
}

struct StoreGameAbout: View {
    let game: StoreGame
    var body: some View {
        if !game.description.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("About this game").font(.title3.bold())
                Text(game.description).font(.body).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            }
        }
    }
}
