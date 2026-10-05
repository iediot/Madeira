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
            .font(.caption2.weight(.semibold)).lineLimit(1)
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
    private var notDownloaded: Bool { installer.installed[game.appName] == nil && installer.installs[game.appName] == nil }

    var body: some View {
        Group {
            if list {
                HStack(spacing: 14) {
                    EpicArtwork(url: game.artworkURL, notDownloaded: notDownloaded).frame(width: 48, height: 72)
                        .overlay { if notDownloaded { LibraryNotInstalledFace(font: .title3) } }
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 8) {
                        Text(game.title).font(.headline).lineLimit(2)
                        tags
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
                    Text(game.title).font(.footnote.weight(.semibold)).lineLimit(2)
                        .multilineTextAlignment(.leading)
                    tags
                    if let download = installer.installs[game.appName]?.download { SteamDownloadStatus(download: download) }
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

    private var tags: some View {
        HStack(spacing: 4) {
            LibrarySourceTag(text: "Epic")
            LibrarySourceTag(text: installer.installed[game.appName] == nil ? "Not installed" : "Installed")
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
    @State private var selected: EpicGame?

    /// Whether the library has Epic games to show at all.
    static var shown: Bool { !EpicInstaller.shared.installed.isEmpty || (EpicAuth.shared.signedIn && !EpicLibrary.shared.games.isEmpty) }

    private var games: [EpicGame] {
        var games = auth.signedIn ? library.games : []
        let known = Set(games.map(\.appName))
        games += installer.installed.values.map(\.game).filter { !known.contains($0.appName) }
        return games.filter { search.isEmpty || $0.title.localizedCaseInsensitiveContains(search) }
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

    private func card(_ game: EpicGame, list: Bool) -> some View {
        Button { selected = game } label: { EpicGameCard(game: game, list: list) }
            .libraryCardButtonStyle(grid: !list)
    }
}

/// An Epic game's page: the wide store art, its cover over the art's lower edge with
/// the name beside it, and the same download status as Steam's game page.
struct EpicGameSheet: View {
    let game: EpicGame
    var open: (LibraryEntry) -> Void
    @ObservedObject private var installer = EpicInstaller.shared
    @Environment(\.dismiss) private var dismiss
    @State private var confirmUninstall = false
    @State private var confirmCancel = false

    private func show(_ entry: LibraryEntry) {
        dismiss()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { open(entry) }
    }

    @ViewBuilder private var actions: some View {
        if let record = installer.installed[game.appName], let entry = installer.entry(game.appName) {
            Button { show(entry) } label: {
                Label("Play", systemImage: "play.fill").font(.headline).frame(maxWidth: .infinity, minHeight: 34)
            }
            .buttonStyle(LibraryPlayStyle(pending: false))
            if !record.prereqPath.isEmpty {
                Button("Install prerequisites", systemImage: "shippingbox") {
                    var prerequisite = LibraryEntry(title: record.prereqName.isEmpty ? "Prerequisites" : record.prereqName,
                                                    relativePath: record.installDir + "/" + record.prereqPath.replacingOccurrences(of: "\\", with: "/"), bits: 0)
                    prerequisite.arguments = record.prereqArgs
                    show(prerequisite)
                }
            }
            Button("Uninstall", role: .destructive) { confirmUninstall = true }
        } else if let download = installer.installs[game.appName]?.download {
            SteamDownloadStatus(download: download)
            switch download.state {
            case .active, .queued:
                Button("Pause", systemImage: "pause.fill") { installer.pause(game.appName) }
            case .paused, .failed:
                Button("Resume", systemImage: "arrow.down.circle") { installer.install(game) }
            }
            Button("Cancel download", role: .destructive) { confirmCancel = true }
        } else {
            EpicSizeRow(game: game)
            Button { installer.install(game) } label: {
                Label("Install", systemImage: "icloud.and.arrow.down")
                    .font(.headline).frame(maxWidth: .infinity, minHeight: 34)
            }
            .buttonStyle(LibraryPlayStyle(pending: !installer.ready))
            .disabled(!installer.ready)
        }
        if let error = installer.error { Text(error).font(.footnote).foregroundStyle(.red) }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    VStack(alignment: .leading, spacing: 16) {
                        EpicArtwork(url: game.heroURL ?? game.artworkURL)
                            .frame(maxWidth: .infinity).frame(height: 200)
                            .clipShape(RoundedRectangle(cornerRadius: 20))
                        HStack(alignment: .top, spacing: 16) {
                            EpicArtwork(url: game.artworkURL).frame(width: 96, height: 144)
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color(uiColor: .systemGroupedBackground), lineWidth: 3))
                                .shadow(color: .black.opacity(0.25), radius: 10, y: 5)
                            VStack(alignment: .leading, spacing: 8) {
                                Text(game.title).font(.title2.bold()).lineLimit(3)
                                HStack(spacing: 4) {
                                    LibrarySourceTag(text: "Epic Games")
                                    LibrarySourceTag(text: installer.installed[game.appName] == nil ? "Not installed" : "Installed")
                                }
                            }
                            .padding(.top, 84)
                        }
                        .padding(.horizontal, 12)
                        .padding(.top, -88)
                        actions
                    }
                    .padding(.bottom, 8)
                }
                .listRowInsets(EdgeInsets())
                .listRowBackground(Color.clear)
            }
            .navigationTitle("Game details").navigationBarTitleDisplayMode(.inline)
            .confirmationDialog("Uninstall \(game.title)?", isPresented: $confirmUninstall, titleVisibility: .visible) {
                Button("Uninstall", role: .destructive) { installer.uninstall(game.appName) }
            } message: { Text("This deletes the game's install folder, including any saves stored there.") }
            .confirmationDialog("Cancel this download?", isPresented: $confirmCancel, titleVisibility: .visible) {
                Button("Delete downloaded files", role: .destructive) { installer.cancel(game.appName) }
            }
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
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
