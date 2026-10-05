import SwiftUI

// The library's Home page, in the spirit of Steam's Big Picture and SteamOS: the
// game you were playing large at the top, then shelves of games that scroll
// sideways, edge to edge. Built from the library's own pieces (its artwork, cards,
// badges, Play button and glass capsules); the Library page keeps the full grid.

/// The page geometry Home and the Library page share, from the viewport's width.
enum LibraryLayout {
    /// The side margin, matching the navigation bar's (and so the search field and title).
    static func margin(_ width: CGFloat) -> CGFloat { width >= 700 ? 20 : 16 }

    /// A shelf card's width: about three and a half cards on a phone, more and larger
    /// ones on an iPad, so the next card always peeks in from the edge.
    static func shelfCard(_ width: CGFloat) -> CGFloat {
        if width < 500 { return max(96, (width - margin(width)) / 3.4) }
        let visible: CGFloat = width < 900 ? 5.3 : 6.6
        return min(200, max(130, (width - margin(width)) / visible))
    }

    static func shelfGap(_ width: CGFloat) -> CGFloat { width >= 700 ? 18 : 12 }
}

/// What the Library page lists: everything, the Steam games, or the games you added.
enum LibraryFilter: String, CaseIterable, Identifiable {
    case all, steam, other
    var id: String { rawValue }
    var title: String {
        switch self {
        case .all: return "All games"
        case .steam: return "Steam"
        case .other: return "Other games"
        }
    }
}

/// A shelf: a title with its count and See all, then one row of cards that scrolls
/// sideways, edge to edge, its first card on the page's margin.
struct LibraryShelf<Item: Identifiable, Cell: View, Trailing: View>: View {
    let title: String
    var count: Int? = nil
    let items: [Item]
    /// The viewport's width.
    let width: CGFloat
    var seeAll: (() -> Void)? = nil
    @ViewBuilder let trailing: () -> Trailing
    @ViewBuilder let cell: (Item) -> Cell

    var body: some View {
        let margin = LibraryLayout.margin(width)
        let card = LibraryLayout.shelfCard(width)
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(title).font(.title2.bold())
                if let count, count > 0 { Text("\(count)").font(.subheadline).foregroundStyle(.secondary) }
                Spacer()
                if let seeAll {
                    Button(action: seeAll) {
                        Text("See all").font(.subheadline.weight(.medium))
                            .padding(.horizontal, 14).frame(minHeight: 34)
                            .libraryRowGlass(Capsule())
                    }.buttonStyle(.plain)
                }
            }
            .accessibilityElement(children: .contain)
            .padding(.horizontal, margin)
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: LibraryLayout.shelfGap(width)) {
                    ForEach(items) { item in cell(item).frame(width: card) }
                    trailing()
                }
                .padding(.vertical, 6)
                .scrollTargetLayout()
            }
            .contentMargins(.horizontal, margin, for: .scrollContent)
            .scrollTargetBehavior(.viewAligned)
            // The cards' shadows and press spring are not cut at the row's edges.
            .scrollClipDisabled()
        }
    }
}

extension LibraryShelf where Trailing == EmptyView {
    init(title: String, count: Int? = nil, items: [Item], width: CGFloat, seeAll: (() -> Void)? = nil,
         @ViewBuilder cell: @escaping (Item) -> Cell) {
        self.init(title: title, count: count, items: items, width: width, seeAll: seeAll, trailing: { EmptyView() }, cell: cell)
    }
}

/// A library game's card: its artwork, name and format badges. The Library page's
/// grid and Home's shelves both use it, so a game looks the same everywhere.
struct LibraryEntryCard: View {
    let entry: LibraryEntry
    var badges = true
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            LibraryArtwork(entry: entry).aspectRatio(2.0 / 3.0, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .modifier(LibraryCardArtworkPress())
            Text(entry.title).font(.footnote.weight(.semibold)).lineLimit(2)
                .multilineTextAlignment(.leading)
            if badges { LibraryBadges(entry: entry).foregroundStyle(.secondary) }
        }
        .padding(4)
        .foregroundStyle(.primary)
    }
}

/// The last card of Home's Other games shelf: add a game, in the artwork's placeholder look.
struct LibraryAddCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ZStack {
                Color(uiColor: .secondarySystemFill)
                Image(systemName: "plus").font(.largeTitle.weight(.medium)).foregroundStyle(.secondary)
            }
            .aspectRatio(2.0 / 3.0, contentMode: .fit)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .modifier(LibraryCardArtworkPress())
            Text("Add a game").font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
        }
        .padding(4)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Add a game")
    }
}

/// The top of Home: a game's wide artwork across the whole page, fading into the
/// background, with its name, badges, Play and its Game page over the fade.
struct LibraryHero: View {
    let entry: LibraryEntry
    /// The viewport's width.
    let width: CGFloat
    let play: () -> Void
    let open: () -> Void
    @ObservedObject private var model = LibraryModel.shared

    var body: some View {
        let wide = width >= 700
        let height = wide ? min(max(width * 0.36, 320), 480) : min(max(width * 0.66, 240), 330)
        VStack(alignment: .leading, spacing: 0) {
            Color.clear.frame(height: height * (wide ? 0.5 : 0.48))
            info(wide: wide)
                .padding(.horizontal, LibraryLayout.margin(width))
                .frame(maxWidth: 760, alignment: .leading)
        }
        .frame(maxWidth: .infinity, minHeight: height, alignment: .topLeading)
        .background(alignment: .top) {
            LibraryArtwork(entry: entry, backdrop: true)
                .frame(maxWidth: .infinity).frame(height: height)
                .mask {
                    LinearGradient(stops: [.init(color: .clear, location: 0), .init(color: .black.opacity(0.35), location: 0.1),
                                           .init(color: .black, location: 0.25), .init(color: .black, location: 0.42),
                                           .init(color: .black.opacity(0.35), location: 0.75), .init(color: .clear, location: 1)],
                                   startPoint: .top, endPoint: .bottom)
                }
                .ignoresSafeArea(edges: .horizontal)   // under the side menu's glass too
                .accessibilityHidden(true)
        }
        .contentShape(Rectangle())
        .onTapGesture(perform: open)
    }

    private func info(wide: Bool) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(entry.lastPlayed != nil ? "CONTINUE PLAYING" : "READY TO PLAY")
                .font(.caption.weight(.bold)).tracking(1.2).foregroundStyle(.secondary)
            Text(entry.title)
                .font(wide ? .system(size: 46, weight: .bold) : .largeTitle.bold())
                .lineLimit(2).minimumScaleFactor(0.6)
            HStack(spacing: 10) {
                LibraryBadges(entry: entry).foregroundStyle(.secondary).fixedSize()
                if let last = entry.lastPlayed {
                    Text("Played \(last, format: .relative(presentation: .named))")
                        .font(.footnote).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            HStack(spacing: 10) {
                Button(action: play) {
                    Label("Play", systemImage: "play.fill").font(.headline)
                        .frame(minWidth: wide ? 150 : 116, minHeight: 30)
                }
                .buttonStyle(LibraryPlayStyle(pending: model.startingJIT != nil))
                .disabled(model.startingJIT != nil)
                Button(action: open) {
                    Label("Game page", systemImage: "info.circle")
                        .font(.subheadline.weight(.medium)).padding(.horizontal, 16).frame(minHeight: 50)
                        .libraryRowGlass(Capsule())
                }
                .buttonStyle(.plain)
            }
            .padding(.top, 6)
        }
    }
}

/// Home: the hero, then Recently played, the Steam shelves and Other games. While
/// the library is empty, a welcome in the hero's place.
struct LibraryHome: View {
    /// The viewport's width.
    let width: CGFloat
    let play: (LibraryEntry) -> Void
    let open: (LibraryEntry) -> Void
    let add: () -> Void
    let seeAll: (LibraryFilter) -> Void
    @ObservedObject private var model = LibraryModel.shared
    @ObservedObject private var steamGames = SteamGamesModel.shared
    @ObservedObject private var steamLibrary = SteamOwnedLibrary.shared
    @AppStorage("madeiraLibrarySort") private var sort = "played"

    /// Games played at least once, most recent first (Steam games included).
    private var played: [LibraryEntry] {
        model.entries.filter { $0.desktop != true && $0.lastPlayed != nil }
            .sorted { ($0.lastPlayed ?? .distantPast) > ($1.lastPlayed ?? .distantPast) }
    }

    /// The games you added, in the Library's order.
    private var others: [LibraryEntry] {
        let mine = model.entries.filter { $0.desktop != true && $0.steamAppID == nil }
        if sort == "added" { return mine.reversed() }
        if sort == "name" { return mine.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending } }
        return mine.sorted { ($0.lastPlayed ?? .distantPast) > ($1.lastPlayed ?? .distantPast) }
    }

    var body: some View {
        let recent = played
        let hero = recent.first ?? model.entries.first { $0.desktop != true }
        VStack(alignment: .leading, spacing: 32) {
            if let hero {
                LibraryHero(entry: hero, width: width, play: { play(hero) }, open: { open(hero) })
            } else {
                welcome
            }
            if recent.count > 1 {
                LibraryShelf(title: "Recently played", items: Array(recent.dropFirst().prefix(15)), width: width) { entry in
                    card(entry)
                }
            }
            if MadeiraDock.enabled {
                SteamGamesSection(search: "", sort: sort, width: width, part: .all, open: open,
                                  seeAll: { seeAll(.steam) })
            }
            otherGames
        }
        .padding(.bottom, 40)
    }

    private var otherGames: some View {
        let mine = others
        let desktop = model.entries.first(where: { $0.desktop == true }) ?? .desktopEntry
        return LibraryShelf(title: "Other games", count: mine.count, items: Array(mine.prefix(20)), width: width,
                            seeAll: mine.isEmpty ? nil : { seeAll(.other) }) {
            HStack(alignment: .top, spacing: LibraryLayout.shelfGap(width)) {
                Button { open(desktop) } label: { LibraryEntryCard(entry: desktop, badges: false) }
                    .libraryCardButtonStyle(grid: true)
                    .frame(width: LibraryLayout.shelfCard(width))
                Button(action: add) { LibraryAddCard() }
                    .libraryCardButtonStyle(grid: true)
                    .frame(width: LibraryLayout.shelfCard(width))
            }
            .fixedSize()
        } cell: { entry in
            card(entry)
        }
    }

    private func card(_ entry: LibraryEntry) -> some View {
        Button { open(entry) } label: { LibraryEntryCard(entry: entry) }
            .libraryCardButtonStyle(grid: true)
            .task(id: entry.id, priority: .utility) { await model.refreshMetadata(entry.id) }
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("WELCOME").font(.caption.weight(.bold)).tracking(1.2).foregroundStyle(.secondary)
            Text("Make yourself at home").font(width >= 700 ? .system(size: 46, weight: .bold) : .largeTitle.bold())
            Text("Copy a game's folder into Madeira › wine › drive_c in Files, then add it here.")
                .font(.body).foregroundStyle(.secondary)
            Button(action: add) {
                Label("Add a game", systemImage: "plus").font(.headline).frame(minWidth: 140, minHeight: 30)
            }
            .buttonStyle(LibraryPlayStyle(pending: false))
            .padding(.top, 6)
        }
        .padding(.horizontal, LibraryLayout.margin(width))
        .padding(.top, 24)
        .frame(maxWidth: 760, alignment: .leading)
    }
}

/// Whether the library shows its side menu (a wide screen), which then takes the
/// navigation bar's place: ContentView hides the bar meanwhile.
final class LibraryChrome: ObservableObject {
    static let shared = LibraryChrome()
    @Published var sideMenu = false
}

/// A search field in the row glass, for the pages of a wide screen (which have no
/// navigation bar for the system one).
struct LibrarySearchField: View {
    @Binding var text: String
    let placeholder: String
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            TextField(placeholder, text: $text)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .submitLabel(.search)
            if !text.isEmpty {
                Button { text = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary) }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Clear search")
            }
        }
        .padding(.horizontal, 14).frame(minHeight: 44)
        .libraryRowGlass(Capsule())
    }
}

/// The side menu of a wide screen, as SteamOS's: Madeira's name with the button that
/// folds the menu to its icons, the pages (the chosen one in the row glass with an
/// accent mark), then at the bottom JIT and Memory+, small, and the Windows desktop
/// (SteamOS's Switch to Desktop).
struct LibrarySideMenu: View {
    static let width: CGFloat = 230
    static let foldedWidth: CGFloat = 72
    @Binding var tab: Int
    /// Folded to its icons; kept by LibraryView, which animates the change.
    @Binding var folded: Bool
    let enableJIT: () -> Void
    let desktop: () -> Void
    @ObservedObject private var jitState = LibraryJITState.shared
    @Environment(\.displayScale) private var displayScale

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                if !folded {
                    Text(LibraryHeaderAlignment.title).font(.title2.bold()).lineLimit(1)
                        .padding(.leading, 6)
                        .accessibilityAddTraits(.isHeader)
                    Spacer(minLength: 0)
                }
                Button {
                    folded.toggle()
                } label: {
                    Image(systemName: "sidebar.left").font(.body.weight(.semibold))
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain).foregroundStyle(.secondary)
                .accessibilityLabel(folded ? "Show menu" : "Hide menu")
            }
            .frame(maxWidth: .infinity, alignment: folded ? .center : .leading)
            .padding(.bottom, 10)
            item("Home", "house.fill", 0)
            item("Library", "square.grid.2x2.fill", 1)
            item("Settings", "gearshape.fill", 2)
            Spacer(minLength: 16)
            status
            Button(action: desktop) { row("Desktop", "desktopcomputer", selected: false) }
                .buttonStyle(.plain)
        }
        .padding(.horizontal, folded ? 10 : 12).padding(.vertical, 12)
        .frame(width: folded ? Self.foldedWidth : Self.width, alignment: .leading)
        .frame(maxHeight: .infinity, alignment: .top)
        .clipped()   // the labels slide out of sight under the edge as it narrows
        .background { Rectangle().fill(.ultraThinMaterial).ignoresSafeArea() }
        .overlay(alignment: .trailing) {
            Rectangle().fill(Color(uiColor: .separator)).frame(width: 1 / displayScale).ignoresSafeArea()
        }
    }

    /// JIT and Memory+ as two small dots; while JIT is off, Enable JIT.
    @ViewBuilder private var status: some View {
        if !jitState.enabled {
            Button(action: enableJIT) {
                Label { Text("Enable JIT").font(.subheadline.weight(.semibold)) } icon: { Image(systemName: "bolt.fill") }
                    .labelStyle(FoldableLabel(folded: folded))
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .background(Color.accentColor, in: RoundedRectangle(cornerRadius: 14))
            }
            .buttonStyle(.plain)
            .padding(.bottom, 6)
        }
        Group {
            if folded {
                VStack(spacing: 6) { dot(jitState.enabled); dot(LibraryJITState.memory) }
                    .frame(maxWidth: .infinity)
            } else {
                HStack(spacing: 12) {
                    HStack(spacing: 5) { dot(jitState.enabled); Text("JIT") }
                    HStack(spacing: 5) { dot(LibraryJITState.memory); Text("Memory+") }
                }
                .padding(.horizontal, 14)
            }
        }
        .font(.caption.weight(.medium)).foregroundStyle(.secondary)
        .padding(.bottom, 8)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("JIT \(jitState.enabled ? "on" : "off"), Memory+ \(LibraryJITState.memory ? "on" : "off")")
    }

    private func dot(_ on: Bool) -> some View {
        Circle().fill(on ? Color.green : Color.red).frame(width: 7, height: 7)
    }

    private func item(_ title: String, _ symbol: String, _ index: Int) -> some View {
        Button {
            tab = index
        } label: { row(title, symbol, selected: tab == index) }
            .buttonStyle(.plain)
            .accessibilityLabel(title)
            .accessibilityAddTraits(tab == index ? .isSelected : [])
    }

    @ViewBuilder private func row(_ title: String, _ symbol: String, selected: Bool) -> some View {
        let label = Label {
            Text(title).font(.headline)
        } icon: {
            Image(systemName: symbol).font(.body.weight(.semibold)).frame(width: 26)
        }
        .labelStyle(FoldableLabel(folded: folded))
        .padding(.horizontal, folded ? 0 : 14)
        .frame(maxWidth: .infinity, minHeight: 50, alignment: folded ? .center : .leading)
        .contentShape(Rectangle())
        if selected {
            label.foregroundStyle(.primary)
                .libraryRowGlass(RoundedRectangle(cornerRadius: 14))
                .overlay(alignment: .leading) {
                    Capsule().fill(Color.accentColor).frame(width: 4, height: 24).padding(.leading, 4)
                }
        } else {
            label.foregroundStyle(.secondary)
        }
    }
}

/// A label that shows only its icon while the side menu is folded.
private struct FoldableLabel: LabelStyle {
    let folded: Bool
    func makeBody(configuration: Configuration) -> some View {
        if folded {
            configuration.icon
        } else {
            HStack(spacing: 12) { configuration.icon; configuration.title }
        }
    }
}
