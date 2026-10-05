// SPDX-License-Identifier: GPL-3.0-or-later
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
//
// The account's Epic Games in Madeira's library: cards in the library's own style
// (tall box art, title, a small Epic tag), a section for the Library page's grid
// and Home's shelf, and a game page using the shared download and Play controls.

import SwiftUI

/// An Epic artwork URL, decoded once and cached like every library card's art.
struct EpicArtwork: View {
    let url: URL?
    /// Not on the device: a soft circle of blur in the middle for the download glyph,
    /// as Steam's not-downloaded cards have (SteamArtworkBlurSpot).
    var notDownloaded = false
    @State private var image: UIImage?
    @State private var blurred: UIImage?

    /// The cached cover from the first frame (ArtworkCache keeps it across launches).
    init(url: URL?, notDownloaded: Bool = false) {
        self.url = url
        self.notDownloaded = notDownloaded
        _image = State(initialValue: url.flatMap { ArtworkCache.cached($0) })
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                Color(uiColor: .secondarySystemFill)
                if let image {
                    Image(uiImage: image).resizable().scaledToFill()
                        .frame(width: geometry.size.width, height: geometry.size.height).clipped()
                        .overlay { if notDownloaded, let blurred { SteamArtworkBlurSpot(image: Image(uiImage: blurred), size: geometry.size) } }
                } else {
                    Image(systemName: "gamecontroller.fill").font(.largeTitle).foregroundStyle(.secondary)
                }
            }
            .frame(width: geometry.size.width, height: geometry.size.height).clipped()
        }
        .accessibilityHidden(true)
        .task(id: url) {
            guard let url else { return }
            if let hit = ArtworkCache.cached(url) { image = hit } else { image = await ArtworkCache.image(url) }
            if notDownloaded, image != nil { blurred = await ArtworkCache.blur(url, fraction: 0.03) }
        }
    }
}

/// A small tag naming where a game comes from.
struct LibrarySourceTag: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.caption2.weight(.medium)).lineLimit(1)
            .padding(.horizontal, 5).padding(.vertical, 4)
            .background(.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))
            .foregroundStyle(.secondary)
    }
}

/// An Epic game's card, or its row in the list layouts.
struct EpicGameCard: View {
    let game: EpicGame
    var list = false
    @ObservedObject private var installer = EpicInstaller.shared

    /// Neither installed nor downloading: the not-installed face, as Steam's cards.
    private var notDownloaded: Bool { installer.installed[game.appName] == nil && !installer.isDownloading(game.appName) }

    var body: some View {
        Group {
            if list {
                HStack(spacing: 14) {
                    EpicArtwork(url: game.artworkURL, notDownloaded: notDownloaded).frame(width: 48, height: 72)
                        .overlay { if notDownloaded { LibraryNotInstalledFace(font: .title3) } }
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 8) {
                        Text(game.title).font(.headline).lineLimit(2)
                        tags()
                        if let download = installer.installs[game.appName]?.download { SteamDownloadStatus(download: download) }
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
                }
                .padding(10)
                .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 16))
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    EpicArtwork(url: game.artworkURL, notDownloaded: notDownloaded).aspectRatio(2.0 / 3.0, contentMode: .fit)
                        .overlay { if notDownloaded { LibraryNotInstalledFace(font: .title) } }
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .overlay { downloadOverlay }
                        .modifier(LibraryCardArtworkPress())
                    // Fixed lines (LibraryEntryCard); the art's overlay shows a download.
                    Text(game.title).font(.footnote.weight(.semibold)).lineLimit(2, reservesSpace: true)
                        .multilineTextAlignment(.leading)
                    tags(oneRow: true).frame(height: LibraryLayout.pillRow, alignment: .leading)
                }
                .padding(4)
            }
        }
        .foregroundStyle(.primary)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var downloadOverlay: some View {
        if let download = installer.installs[game.appName]?.download {
            ZStack {
                Color.black.opacity(0.45)
                switch download.state {
                case .active: ProgressView(value: download.progress.fraction).progressViewStyle(.circular).tint(.white)
                case .queued: Image(systemName: "clock").font(.title2).foregroundStyle(.white)
                case .paused: Image(systemName: "pause.circle.fill").font(.title).foregroundStyle(.white)
                case .failed: Image(systemName: "exclamationmark.triangle.fill").font(.title2).foregroundStyle(.yellow)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 10))
        }
    }

    /// The same pills as a Steam card: once installed the store and the format pills of
    /// any library game (bits, graphics API, size), otherwise the store and the state.
    @ViewBuilder private func tags(oneRow: Bool = false) -> some View {
        if !installer.isDownloading(game.appName), let entry = installer.entry(game.appName) {
            LibraryBadges(entry: entry, store: "Epic", oneRow: oneRow).foregroundStyle(.secondary)
        } else {
            HStack(spacing: 4) {
                LibrarySourceTag(text: state)
                LibrarySourceTag(text: "Epic")
            }
        }
    }

    /// Steam's wording for the same states (SteamGamesRules.Status.badge).
    private var state: String {
        guard let download = installer.installs[game.appName]?.download else { return "Not installed" }
        switch download.state {
        case .queued: return "Waiting"
        case .active: return "Downloading \(Int(download.progress.fraction * 100))%"
        case .paused: return "Paused"
        case .failed: return "Download failed"
        }
    }
}

/// The account's Epic games: the Library page's grid (with its section title), or
/// Home's shelf when `shelf` is set. Opens a game's page.
struct EpicGamesSection: View {
    let search: String
    var layout = "cards"
    var width: CGFloat = 390
    /// Home: a shelf whose See all opens the Library on its Epic filter.
    var shelf: (() -> Void)? = nil
    var open: (LibraryEntry) -> Void
    @ObservedObject private var library = EpicLibrary.shared
    @ObservedObject private var auth = EpicAuth.shared
    @ObservedObject private var installer = EpicInstaller.shared
    @ObservedObject private var hidden = LibraryHidden.shared
    @State private var selected: EpicGame?

    /// Whether the library has Epic games to show at all.
    static var shown: Bool { !EpicInstaller.shared.installed.isEmpty || (EpicAuth.shared.signedIn && !EpicLibrary.shared.games.isEmpty) }

    private var games: [EpicGame] {
        var games = auth.signedIn ? library.games : []
        let known = Set(games.map(\.appName))
        games += installer.installed.values.map(\.game).filter { !known.contains($0.appName) }
        return games.filter { search.isEmpty || $0.title.localizedCaseInsensitiveContains(search) }
            .filter { !hidden.hides(LibraryHidden.epic($0.appName)) }
            .sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending }
    }

    var body: some View {
        Group {
            if !games.isEmpty {
                if let shelf {
                    LibraryShelf(title: "Epic Games", count: games.count, items: Array(games.prefix(20)),
                                 width: width, seeAll: shelf) { game in card(game, list: false) }
                } else {
                    VStack(alignment: .leading, spacing: 14) {
                        LibrarySectionHeader(title: "Epic Games", count: games.count) {
                            if library.isLoading { ProgressView().accessibilityLabel("Refreshing Epic library") }
                        }
                        LibraryCells(items: games, layout: layout, width: width) { game, list, _ in
                            card(game, list: list)
                        }
                    }
                }
            } else {
                Color.clear.frame(height: 0).accessibilityHidden(true)
            }
        }
        .onAppear { library.refreshIfStale() }
        .sheet(item: $selected) { game in EpicGameSheet(game: game, open: open) }
    }

    /// An installed game opens its Game details page directly, as Steam's do; any other
    /// opens its install page.
    private func openOrShow(_ game: EpicGame) {
        if !installer.isDownloading(game.appName), let entry = installer.entry(game.appName) { open(entry) }
        else { selected = game }
    }

    private func card(_ game: EpicGame, list: Bool) -> some View {
        Button { openOrShow(game) } label: { EpicGameCard(game: game, list: list) }
            .libraryCardButtonStyle(grid: !list)
            .libraryHideMenu(LibraryHidden.epic(game.appName))
    }
}

/// An Epic game's install page, laid out exactly as Steam's (SteamGameSheet): the cover
/// with the name and one action (Install, Pause, Resume, Try again, Open) over the blurred
/// art, the download, the sizes beside the free space, then the store link.
struct EpicGameSheet: View {
    let game: EpicGame
    /// Opens the installed game's Game details page (after this sheet closes).
    var open: (LibraryEntry) -> Void
    @ObservedObject private var installer = EpicInstaller.shared
    @ObservedObject private var sizes = EpicSizes.shared
    @Environment(\.dismiss) private var dismiss
    @State private var confirmCancel = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack(spacing: 20) {
                        EpicArtwork(url: game.artworkURL).frame(width: 120, height: 180)
                            .clipShape(RoundedRectangle(cornerRadius: 14))
                        VStack(alignment: .leading, spacing: 12) {
                            Text(game.title).font(.title2.bold())
                            primaryAction
                        }
                    }.padding(.vertical, 24)
                        .listRowBackground(
                            EpicArtwork(url: game.heroURL ?? game.artworkURL).blur(radius: 4)
                                .overlay(Color(uiColor: .secondarySystemGroupedBackground).opacity(0.55))
                                .clipped()
                        )
                }
                if let download = installer.installs[game.appName]?.download {
                    Section("Download") {
                        SteamDownloadStatus(download: download)
                        Button("Cancel download", role: .destructive) { confirmCancel = true }
                        if case .failed = download.state {
                            Text("Downloaded files are kept. Try again to continue where it stopped.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                Section {
                    // The game's sizes from its manifest, before Install, next to the free
                    // space (red when the installed game would not fit).
                    let installed = installer.installed[game.appName] != nil
                    let size = sizes.sizes[game.appName]
                    let free = Int64(clamping: EpicSizes.free)
                    if !installed {
                        if let size {
                            LabeledContent("Download size", value: EpicSizeRow.format(size.download))
                            LabeledContent("Installed size", value: EpicSizeRow.format(size.install))
                        } else {
                            LabeledContent("Download size") { ProgressView() }
                        }
                    }
                    let tooBig = !installed && size.map { Int64(clamping: $0.install) > free } == true
                    LabeledContent("Free space on this device") {
                        Text(EpicSizeRow.format(UInt64(max(0, free)))).foregroundStyle(tooBig ? Color.red : Color.secondary)
                    }
                }
                Section {
                    if let url = URL(string: "https://store.epicgames.com/browse?q=" + (game.title.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "")) {
                        Link(destination: url) { Label("View in the Epic Games Store", systemImage: "safari") }
                    }
                }
                if let error = installer.error {
                    Section { Text(error).font(.footnote).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Epic Games").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task { sizes.load(game) }
            .confirmationDialog("Cancel this download? Downloaded files are deleted.", isPresented: $confirmCancel, titleVisibility: .visible) {
                Button("Cancel download", role: .destructive) { installer.cancel(game.appName) }
                Button("Keep downloading", role: .cancel) {}
            }
        }
    }


    @ViewBuilder private var primaryAction: some View {
        if installer.installed[game.appName] != nil, !installer.isDownloading(game.appName),
           let entry = installer.entry(game.appName) {
            // Installed: its Game details page.
            Button { dismiss(); open(entry) } label: {
                HStack(spacing: 10) { Image(systemName: "play.fill"); Text("Open").fontWeight(.semibold) }.frame(minWidth: 100, minHeight: 30)
            }.buttonStyle(.borderedProminent)
        } else if let download = installer.installs[game.appName]?.download {
            switch download.state {
            case .active, .queued:
                Button { installer.pause(game.appName) } label: { steamActionLabel("Pause", symbol: "pause.fill") }
                    .buttonStyle(.bordered)
            case .paused:
                Button { installer.install(game) } label: { steamActionLabel("Resume", symbol: "arrow.down.circle.fill") }
                    .buttonStyle(.borderedProminent)
            case .failed:
                Button { installer.install(game) } label: { steamActionLabel("Try again", symbol: "arrow.clockwise") }
                    .buttonStyle(.borderedProminent)
            }
        } else {
            Button { installer.install(game) } label: { steamActionLabel("Install", symbol: "arrow.down.circle.fill") }
                .buttonStyle(.borderedProminent).disabled(!installer.ready)
        }
    }
}

/// "Download 2.1 GB · Installed 4.8 GB · 37 GB free", red when the game will not fit.
struct EpicSizeRow: View {
    let game: EpicGame
    @ObservedObject private var sizes = EpicSizes.shared

    var body: some View {
        let free = EpicSizes.free
        Group {
            if let size = sizes.sizes[game.appName] {
                let fits = free >= size.install + 128 * 1024 * 1024
                HStack(spacing: 6) {
                    Text("Download \(Self.format(size.download))")
                    Text("·")
                    Text("Installed \(Self.format(size.install))")
                    Text("·")
                    Text("\(Self.format(free)) free").foregroundStyle(fits ? Color.secondary : Color.red)
                }
            } else {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("Checking size · \(Self.format(free)) free")
                }
            }
        }
        .font(.footnote).foregroundStyle(.secondary).lineLimit(1).minimumScaleFactor(0.7)
        .task { sizes.load(game) }
    }

    static func format(_ bytes: UInt64) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(min(bytes, UInt64(Int64.max))), countStyle: .file)
    }
}

/// Game details › Epic Games, for an installed Epic game (as SteamEntrySection is for
/// Steam's): its version, its prerequisites installer, and Uninstall.
struct EpicEntrySection: View {
    let appName: String
    /// Starts a program as a library session (the prerequisites installer).
    var run: (LibraryEntry) -> Void
    /// Closes the details page (after Uninstall).
    var leave: () -> Void
    @ObservedObject private var installer = EpicInstaller.shared
    @State private var confirmUninstall = false

    var body: some View {
        if let record = installer.installed[appName] {
            Section {
                if !record.buildVersion.isEmpty { LabeledContent("Version", value: record.buildVersion) }
                if !record.prereqPath.isEmpty {
                    Button("Install prerequisites", systemImage: "shippingbox") {
                        var prerequisite = LibraryEntry(title: record.prereqName.isEmpty ? "Prerequisites" : record.prereqName,
                                                        relativePath: record.installDir + "/" + record.prereqPath.replacingOccurrences(of: "\\", with: "/"),
                                                        bits: 0)
                        prerequisite.arguments = record.prereqArgs
                        run(prerequisite)
                    }
                }
                Button("Uninstall", role: .destructive) { confirmUninstall = true }
            } header: { Text("Epic Games") }
            .confirmationDialog("Uninstall \(record.game.title)? Its files are deleted from this device.",
                                isPresented: $confirmUninstall, titleVisibility: .visible) {
                Button("Uninstall", role: .destructive) {
                    installer.uninstall(appName)
                    leave()
                }
            }
        }
    }
}

/// What an Epic game will take, shown on its page before Install: the download (the
/// compressed chunks) and the installed size (the files), from the game's manifest,
/// with the device's free space. Fetched when the page opens; kept per game.
@MainActor final class EpicSizes: ObservableObject {
    static let shared = EpicSizes()
    struct Size { var download: UInt64; var install: UInt64 }
    @Published private(set) var sizes: [String: Size] = [:]
    private var loading: Set<String> = []

    func load(_ game: EpicGame) {
        guard sizes[game.appName] == nil, !loading.contains(game.appName),
              let catalog = game.catalogItemId, !catalog.isEmpty else { return }
        loading.insert(game.appName)
        Task {
            defer { loading.remove(game.appName) }
            guard let token = try? await EpicAuth.shared.validAccessToken(),
                  let (manifest, _) = try? await EpicManifest.fetch(namespace: game.namespace, catalogItemID: catalog,
                                                                    appName: game.appName, token: token) else { return }
            sizes[game.appName] = Size(download: manifest.chunks.reduce(0) { $0 + UInt64(max(0, $1.fileSize)) },
                                       install: manifest.files.reduce(0) { $0 + $1.size })
        }
    }

    /// Free space where games are installed (the same measure the installer checks).
    nonisolated static var free: UInt64 {
        let values = try? LibraryModel.drive.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey,
                                                                      .volumeAvailableCapacityKey])
        return UInt64(max(0, max(values?.volumeAvailableCapacityForImportantUsage ?? 0,
                                 Int64(values?.volumeAvailableCapacity ?? 0))))
    }
}
