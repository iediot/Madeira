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
                                .controllerFocusable(cornerRadius: 20, activate: { withAnimation(.snappy) { self.expanded = nil } })
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
        .sheet(item: $selection) { game in StoreGameSheet(game: game, open: open).controllerDismiss() }
        .onReceive(LibraryController.shared.commands) { command in
            if command == "back", expanded != nil { withAnimation(.snappy) { expanded = nil } }
        }
    }
    private func failure(_ message: String) -> some View {
        VStack(spacing: 12) {
            Text(message).foregroundStyle(.secondary)
            Button("Try again") { if query.isEmpty { Task { await load() } } else { retry += 1 } }
                .buttonStyle(.plain).padding(12).libraryRowGlass(Capsule())
                .controllerFocusable(cornerRadius: 20, activate: { if query.isEmpty { Task { await load() } } else { retry += 1 } })
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
            .controllerFocusable(cornerRadius: 12, primary: true, activate: { selection = game })
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
                .controllerFocusable(cornerRadius: 24, activate: act, context: act)
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

/// Store screenshots/trailers as decorative library media. This never changes
/// the cover artwork or stores a random screenshot as a game's resolved cover.
struct GameFootageBackdrop: View {
    var entry: LibraryEntry? = nil
    var steamAppID: Int? = nil
    var allowsVideo = false

    private var appID: Int? { steamAppID ?? entry?.steamAppID ?? entry?.steamID }
    private var mediaKey: String {
        if let epic = entry?.epicAppName { return "epic:\(epic)" }
        if let appID { return "steam:\(appID)" }
        return entry?.id.uuidString ?? "none"
    }
    var body: some View {
        GameFootageContent(entry: entry, appID: appID, allowsVideo: allowsVideo)
            .id(mediaKey + (allowsVideo ? ":hero" : ":loading"))
            .allowsHitTesting(false).accessibilityHidden(true)
    }
}

private struct GameFootageContent: View {
    let entry: LibraryEntry?
    let appID: Int?
    let allowsVideo: Bool
    private struct Slide: Identifiable {
        let id = UUID()
        let item: StoreBackdropItem
        let image: UIImage?
    }
    @State private var slide: Slide?
    @State private var fallback = false
    @State private var advance = false
    @State private var catalog: StoreGame?
    @State private var deck: [StoreBackdropItem] = []
    @State private var failures: [StoreBackdropItem] = []
    @State private var remaining: Double = 10
    @State private var visible = false
    @Environment(\.scenePhase) private var scenePhase
    @ObservedObject private var library = LibraryModel.shared
    private var available: Bool { library.current == nil && library.startingJIT == nil }
    private var active: Bool { visible && scenePhase == .active && available }
    private struct PlaybackKey: Equatable {
        let active: Bool
        let slideID: UUID?
        let ended: Bool
    }

    var body: some View {
        ZStack {
            // Neutral while fetching footage: never flash the old hero first.
            Color.clear
            if fallback, let entry {
                LibraryArtwork(entry: entry, backdrop: true)
            } else if fallback, let appID {
                CachedArtworkImage(url: SteamCatalog.hero(appID), fallbacks: SteamCatalog.heroFallbacks(appID),
                                   steamAppID: appID, wide: true)
            }
            if let slide {
                GeometryReader { geo in
                    ZStack {
                        if let image = slide.image {
                            Image(uiImage: image).resizable().scaledToFill()
                                .frame(width: geo.size.width, height: geo.size.height).clipped()
                        }
                        if let url = slide.item.movieURL {
                            FootageVideo(url: url, active: active, release: !available,
                                         ended: { if self.slide?.id == slide.id { advance = true } })
                        }
                    }
                }
                .id(slide.id).transition(.opacity)
            }
        }
        .clipped()
        .onAppear { visible = true }
        .onDisappear { visible = false }
        .task {
            let media: StoreGame?
            if let name = entry?.epicAppName,
               let game = EpicLibrary.shared.games.first(where: { $0.appName == name }) {
                let found = (try? await EpicStore.shared.search(game.title)) ?? []
                media = found.first { $0.matches(namespace: game.namespace, catalogItemID: game.catalogItemId, title: game.title) }?.presentation
            } else if let appID {
                media = try? await SteamStore.shared.details(appID)
            } else { media = nil }
            guard !Task.isCancelled else { return }
            guard let media else { fallback = true; return }
            catalog = media
            if slide == nil { await nextSlide() }
        }
        // Environment values captured by a long-running task do not refresh on
        // body updates. Key playback to current activity instead of polling a
        // captured scenePhase (which could leave a screenshot paused forever).
        .task(id: PlaybackKey(active: active, slideID: slide?.id, ended: advance)) {
            guard active, allowsVideo, let slide else { return }
            if slide.item.movieURL != nil {
                if advance { await nextSlide() }
                return // Trailers advance on completion, never the image timer.
            }
            while remaining > 0 {
                do { try await Task.sleep(nanoseconds: 250_000_000) } catch { return }
                guard !Task.isCancelled else { return }
                remaining = max(0, remaining - 0.25)
            }
            await nextSlide()
        }
    }

    @MainActor private func nextSlide() async {
        guard let catalog else { return }
        while !Task.isCancelled {
            if deck.isEmpty {
                deck = catalog.shuffledBackdrops(allowsVideo: allowsVideo, avoiding: slide?.item)
                    .filter { !failures.contains($0) }
            }
            guard !deck.isEmpty else { fallback = slide == nil; return }
            let item = deck.removeFirst()
            let image: UIImage?
            if let url = item.imageURL { image = await ArtworkCache.image(url, pixels: 2560) }
            else { image = nil }
            guard !Task.isCancelled else { return }
            // Keep the previous frame until its replacement is decoded.
            if image == nil && item.movieURL == nil {
                failures.append(item)
                continue
            }
            advance = false
            remaining = 10
            withAnimation(.easeInOut(duration: 1)) { slide = Slide(item: item, image: image) }
            return
        }
    }
}

/// Muted, aspect-fill footage without playback controls or audio-session changes.
/// Pause across brief backgrounding, preserving the timestamp; release for games.
private struct FootageVideo: UIViewRepresentable {
    let url: URL
    let active: Bool
    let release: Bool
    let ended: () -> Void
    final class Host: UIView {
        override class var layerClass: AnyClass { AVPlayerLayer.self }
        private var player: AVPlayer?
        private var observation: NSKeyValueObservation?
        private var statusObservation: NSKeyValueObservation?
        private var endObserver: NSObjectProtocol?
        private var onEnd: (() -> Void)?
        private var playingURL: URL?
        private var videoLayer: AVPlayerLayer { layer as! AVPlayerLayer }
        func update(url: URL, active: Bool, release: Bool, ended: @escaping () -> Void) {
            onEnd = ended
            if release { stop(); return }
            if playingURL == url {
                if active { player?.play() } else { player?.pause() }
                return
            }
            guard active else { return }
            stop()
            playingURL = url
            let queue = AVPlayer()
            queue.isMuted = true
            queue.preventsDisplaySleepDuringVideoPlayback = false
            let item = AVPlayerItem(url: url)
            // Use the store's full-quality stream, not the old 720p/2Mbps cap.
            player = queue
            videoLayer.videoGravity = .resizeAspectFill
            videoLayer.opacity = 0
            videoLayer.player = queue
            observation = videoLayer.observe(\.isReadyForDisplay, options: [.initial, .new]) { [weak self] _, _ in
                DispatchQueue.main.async { [weak self] in
                    guard let self else { return }
                    self.videoLayer.opacity = self.videoLayer.isReadyForDisplay ? 1 : 0
                }
            }
            endObserver = NotificationCenter.default.addObserver(forName: .AVPlayerItemDidPlayToEndTime,
                object: item, queue: .main) { [weak self] _ in self?.onEnd?() }
            statusObservation = item.observe(\.status, options: [.initial, .new]) { [weak self] item, _ in
                if item.status == .failed { DispatchQueue.main.async { [weak self] in self?.onEnd?() } }
            }
            queue.replaceCurrentItem(with: item)
            queue.play()
        }
        func stop() {
            observation?.invalidate(); observation = nil
            statusObservation?.invalidate(); statusObservation = nil
            if let endObserver { NotificationCenter.default.removeObserver(endObserver) }
            endObserver = nil
            player?.pause()
            player?.replaceCurrentItem(with: nil); player = nil
            videoLayer.player = nil
            videoLayer.opacity = 0
            playingURL = nil
        }
    }
    func makeUIView(context: Context) -> Host { Host() }
    func updateUIView(_ view: Host, context: Context) { view.update(url: url, active: active, release: release, ended: ended) }
    static func dismantleUIView(_ view: Host, coordinator: ()) { view.stop() }
}

/// Loaded only when the media section enters the view hierarchy; shared by all game
/// pages. A paged TabView in a Form row was re-measured on every scroll.
struct StoreGameMedia: View {
    let appID: Int
    @State private var game: StoreGame?
    var body: some View {
        VStack(alignment: .leading, spacing: 0) { if let game { StoreMediaContent(game: game) } }
            .task(id: appID) { game = try? await SteamStore.shared.details(appID) }
    }
}

/// An Epic library game's store media, as StoreGameMedia for Steam: Epic's store
/// search for its title, matched by namespace or catalog item (EpicStoreGame.matches),
/// so an owned game's page shows the same screenshots as its store page.
struct EpicStoreGameMedia: View {
    let game: EpicGame
    @State private var media: StoreGame?
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let media, !(media.screenshots.isEmpty && media.movies.isEmpty) { StoreMediaContent(game: media) }
        }
        .task(id: game.appName) {
            let found = (try? await EpicStore.shared.search(game.title)) ?? []
            media = found.first { $0.matches(namespace: game.namespace, catalogItemID: game.catalogItemId, title: game.title) }?.presentation
        }
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
