import SwiftUI

enum StoreDestination: String, CaseIterable, Identifiable {
    case steam = "Steam", epic = "Epic Games"
    var id: String { rawValue }
    var subtitle: String {
        self == .steam ? "Discover your next favourite game." : "Explore new releases and free games."
    }
}

struct StoreView: View {
    @Binding var search: String
    @Binding var destination: StoreDestination?
    let wide: Bool
    let open: (LibraryEntry) -> Void
    /// Entering a store slides its page in from the trailing edge over the picker;
    /// going back slides it out again. The picker itself never moves.
    private static let move = Animation.smooth(duration: 0.38)
    private func go(_ store: StoreDestination?) {
        search = ""
        withAnimation(UIAccessibility.isReduceMotionEnabled ? .easeOut(duration: 0.2) : Self.move) { destination = store }
    }
    var body: some View {
        ZStack {
            if let destination {
                VStack(spacing: 0) {
                GeometryReader { geo in
                    HStack(spacing: 12) {
                        Button { go(nil) } label: {
                            Image(systemName: "chevron.left").font(.headline).frame(width: 40, height: 40).libraryRowGlass(Circle())
                        }.buttonStyle(.plain).accessibilityLabel("Choose a store")
                        Text(destination.rawValue).font(.title2.bold())
                        Spacer(minLength: 0)
                        if wide {
                            LibrarySearchField(text: $search, placeholder: "Search \(destination.rawValue)").frame(maxWidth: 340)
                        }
                    }.padding(.horizontal, LibraryLayout.margin(geo.size.width))
                }.frame(height: 48).padding(.top, 12)
                switch destination {
                case .steam: SteamStorePage(search: $search, open: open)
                case .epic: EpicStorePage(search: $search, open: open)
                }
                }
                .background(Color(uiColor: .systemGroupedBackground).ignoresSafeArea())
                .transition(.asymmetric(insertion: .move(edge: .trailing).combined(with: .opacity),
                                        removal: .move(edge: .trailing).combined(with: .opacity)))
                .zIndex(1)
            } else {
                // The two stores share the screen: side by side when it is wider than
                // tall, stacked on an upright phone.
                GeometryReader { geo in
                    let margin = LibraryLayout.margin(geo.size.width)
                    let side = geo.size.width > geo.size.height
                    VStack(alignment: .leading, spacing: 20) {
                        if wide { Text("Store").font(.largeTitle.bold()) }
                        let layout = side ? AnyLayout(HStackLayout(spacing: 0)) : AnyLayout(VStackLayout(spacing: 0))
                        // No panels: the two logos, split by a hairline that barely shows.
                        layout {
                            ForEach(StoreDestination.allCases) { store in
                                if store != StoreDestination.allCases.first {
                                    Rectangle().fill(.secondary.opacity(0.18))
                                        .frame(width: side ? 0.5 : nil, height: side ? nil : 0.5)
                                        .padding(side ? .vertical : .horizontal, 40)
                                }
                                Button { go(store) } label: { StorePickerCard(store: store) }
                                    .libraryCardButtonStyle(grid: true)
                            }
                        }
                    }.padding(.horizontal, margin).padding(.vertical, 20)
                }
                // The picker stays where it is under the sliding page and only fades.
                .transition(.opacity)
            }
        }.background(Color(uiColor: .systemGroupedBackground).ignoresSafeArea())
    }
}

/// One store's half of the picker, no panel: its logo centred, its name and line below.
private struct StorePickerCard: View {
    let store: StoreDestination
    var body: some View {
        GeometryReader { geo in
            VStack(spacing: 14) {
                Spacer(minLength: 0)
                Image(store == .steam ? "StoreLogoSteam" : "StoreLogoEpic")
                    .resizable().scaledToFit()
                    .frame(width: min(geo.size.width, geo.size.height) * 0.32, height: min(geo.size.width, geo.size.height) * 0.32)
                    .foregroundStyle(.primary)
                Text(store.rawValue).font(.title2.bold())
                Text(store.subtitle).font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
                Spacer(minLength: 0)
            }.padding(20).frame(width: geo.size.width, height: geo.size.height)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .foregroundStyle(.primary)
        .contentShape(Rectangle())
    }
}

struct EpicStorePage: View {
    /// Which content the page shows; a change crossfades it (shelves, a See all grid, search).
    private var phase: String { !query.isEmpty ? "search" : expanded.map { "all:" + $0.title } ?? "shelves" }
    @Binding var search: String
    let open: (LibraryEntry) -> Void
    @ObservedObject private var library = EpicLibrary.shared
    @ObservedObject private var installer = EpicInstaller.shared
    @ObservedObject private var auth = EpicAuth.shared
    @State private var shelves: [EpicStoreShelf] = []
    @State private var expanded: EpicStoreShelf?
    @State private var results: [EpicStoreGame] = []
    @State private var selection: EpicStoreGame?
    @State private var loading = true
    @State private var searching = false
    @State private var failure: String?
    @State private var searchError: String?
    @State private var retry = 0
    private var query: String { search.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        GeometryReader { geo in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 24) {
                    if !query.isEmpty {
                        if searching { ProgressView().frame(maxWidth: .infinity) }
                        else if let searchError { errorView(searchError) }
                        else if results.isEmpty { ContentUnavailableView.search(text: query) }
                        else { grid(results, width: geo.size.width) }
                    } else if let expanded {
                        HStack(spacing: 12) {
                            Button { withAnimation(.snappy) { self.expanded = nil } } label: {
                                Image(systemName: "chevron.left").font(.headline).frame(width: 40, height: 40).libraryRowGlass(Circle())
                            }.buttonStyle(.plain).accessibilityLabel("Back to Epic Games")
                            Text(expanded.displayTitle).font(.title2.bold())
                        }.padding(.horizontal, LibraryLayout.margin(geo.size.width))
                        grid(expanded.games, width: geo.size.width)
                    } else {
                        if loading { ProgressView().frame(maxWidth: .infinity) }
                        if let failure { errorView(failure) }
                        ForEach(shelves) { shelf in
                            LibraryShelf(title: shelf.displayTitle, items: shelf.games, width: geo.size.width,
                                         seeAll: { withAnimation(.snappy) { expanded = shelf } }) { card($0) }
                        }
                    }
                }.padding(.vertical, 16).padding(.bottom, 24)
                .animation(.easeOut(duration: 0.25), value: loading)
                .animation(.easeOut(duration: 0.2), value: searching)
                .id(phase).transition(.opacity)
            }
            .animation(.easeOut(duration: 0.22), value: phase)
            .refreshable { await load() }
        }
        .task { library.refreshIfStale(); await load() }
        .task(id: "\(query)|\(retry)") {
            let term = query
            guard !term.isEmpty else { results = []; searching = false; searchError = nil; return }
            searching = true; results = []; searchError = nil
            do {
                try await Task.sleep(nanoseconds: 300_000_000)
                let found = try await EpicStore.shared.search(term)
                try Task.checkCancellation()
                results = found; searching = false
            } catch {
                guard !Task.isCancelled else { return }
                searchError = "The Epic Games search could not load. Try again."; searching = false
            }
        }
        .sheet(item: $selection) { EpicStoreGameSheet(game: $0, open: open) }
    }
    private func errorView(_ message: String) -> some View {
        VStack(spacing: 12) {
            Text(message).foregroundStyle(.secondary)
            Button("Try again") { if query.isEmpty { Task { await load() } } else { retry += 1 } }
                .buttonStyle(.plain).padding(12).libraryRowGlass(Capsule())
        }.frame(maxWidth: .infinity).padding()
    }
    private func grid(_ games: [EpicStoreGame], width: CGFloat) -> some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: LibraryLayout.shelfCard(width)), spacing: LibraryLayout.shelfGap(width))], spacing: 20) {
            ForEach(games) { card($0) }
        }.padding(.horizontal, LibraryLayout.margin(width))
    }
    private func card(_ game: EpicStoreGame) -> some View {
        let installed = installer.installed.values.contains { game.matches($0.game) }
        let owned = auth.signedIn && library.games.contains { game.matches($0) }
        return Button { selection = game } label: {
            VStack(alignment: .leading, spacing: 6) {
                StoreArtwork(urls: [game.presentation.capsule, game.presentation.header].compactMap { $0 })
                    .aspectRatio(2.0 / 3, contentMode: .fit).clipShape(RoundedRectangle(cornerRadius: 10))
                    .modifier(LibraryCardArtworkPress())
                Text(game.presentation.name).font(.footnote.weight(.semibold)).lineLimit(2, reservesSpace: true).multilineTextAlignment(.leading)
                StorePrice(game: game.presentation, status: installed ? "Installed" : owned ? "Owned" : game.promotionLabel)
                    .frame(height: LibraryLayout.pillRow, alignment: .leading)
            }.padding(4).foregroundStyle(.primary)
        }.libraryCardButtonStyle(grid: true)
    }
    private func load() async {
        loading = shelves.isEmpty; failure = nil
        async let front = try? EpicStore.shared.storefront()
        async let free = try? EpicStore.shared.freeGames()
        let (loaded, promotions) = await (front, free)
        guard !Task.isCancelled else { return }
        var next = loaded ?? []
        if let promotions, !promotions.isEmpty { next.append(EpicStoreShelf(title: "Free Games", games: promotions)) }
        shelves = next.sorted { (EpicStoreDecoding.order.firstIndex(of: $0.title) ?? 99) < (EpicStoreDecoding.order.firstIndex(of: $1.title) ?? 99) }
        if loaded == nil || promotions == nil { failure = "Some Epic Games shelves could not load. Try again." }
        else if shelves.isEmpty { failure = "No Epic Games offers are available in your region." }
        loading = false
    }
}

private extension EpicStoreShelf {
    /// Epic's own module names, in the sentence case the Steam page uses ("Top sellers");
    /// Epic Savings Spotlight is a name and keeps its capitals.
    var displayTitle: String {
        guard title != "Epic Savings Spotlight", let first = title.first else { return title }
        return first.uppercased() + title.dropFirst().lowercased()
    }
}

private extension EpicStoreGame {
    /// A free game goes straight to Epic's checkout for its offer: one Place Order
    /// (the order itself needs Epic's own page and its captcha, so it cannot be placed
    /// from here). Signing in there is once; Safari keeps the session.
    var checkoutURL: URL? {
        guard !namespace.isEmpty, !offerID.isEmpty else { return nil }
        var url = URLComponents(string: "https://store.epicgames.com/purchase")!
        url.queryItems = [URLQueryItem(name: "offers", value: "1-\(namespace)-\(offerID)")]
        return url.url
    }
    func matches(_ game: EpicGame) -> Bool { matches(namespace: game.namespace, catalogItemID: game.catalogItemId, title: game.title) }
}

struct EpicStoreGameSheet: View {
    let game: EpicStoreGame
    let open: (LibraryEntry) -> Void
    @ObservedObject private var library = EpicLibrary.shared
    @ObservedObject private var installer = EpicInstaller.shared
    @ObservedObject private var auth = EpicAuth.shared
    @Environment(\.dismiss) private var dismiss
    @State private var buying = false
    @State private var ownedSelection: EpicGame?
    private var owned: EpicGame? {
        installer.installed.values.first { game.matches($0.game) }?.game
            ?? (auth.signedIn ? library.games.first { game.matches($0) } : nil)
    }
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 0) {
                    StoreArtwork(urls: [game.presentation.header, game.presentation.capsule].compactMap { $0 })
                        .aspectRatio(16.0 / 9, contentMode: .fit).frame(maxWidth: 1100)
                    VStack(alignment: .leading, spacing: 22) {
                        StoreGameHeader(game: game.presentation)
                        HStack(spacing: 12) {
                            if owned == nil { StorePrice(game: game.presentation, large: true) }
                            Button {
                                if let owned { ownedSelection = owned } else { buying = true }
                            } label: {
                                Text(owned != nil ? "In your library" : game.presentation.isFree ? "Get on Epic" : "Buy on Epic")
                                    .fontWeight(.semibold).frame(maxWidth: .infinity, minHeight: 48).libraryRowGlass(Capsule())
                            }.buttonStyle(.plain).disabled(owned == nil && game.url == nil)
                        }
                        if let promotion = game.promotionLabel { Text(promotion).font(.subheadline).foregroundStyle(.secondary) }
                        StoreMediaContent(game: game.presentation)
                        StoreGameAbout(game: game.presentation)
                    }.padding(.horizontal, 20).padding(.top, 20).padding(.bottom, 32).frame(maxWidth: 760)
                }.frame(maxWidth: .infinity)
            }.background(Color(uiColor: .systemGroupedBackground))
                .navigationTitle(game.presentation.name).navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
                .sheet(isPresented: $buying, onDismiss: { if auth.signedIn { library.refresh() } }) {
                    if let url = game.presentation.isFree ? game.checkoutURL ?? game.url : game.url { StoreSafari(url: url) }
                }
                .sheet(item: $ownedSelection) { owned in
                    EpicGameSheet(game: owned) { entry in
                        dismiss()
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) { open(entry) }
                    }
                }
        }
    }
}
