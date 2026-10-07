import SwiftUI
import UIKit

// Will Faust's ambient light around grid cards (40d5e748), restored behind a
// Settings toggle: Settings › Appearance › Card glow (LibraryGlowSetting), off by
// default. Only installed games' cards report themselves to it (LibraryEntryCard,
// SteamGames' installed cards), so the library's not-downloaded games stay plain.

/// Whether the cards glow: Settings › Appearance › Card glow, kept in madeira.cfg as
/// env.MADEIRA_LIBRARY_AMBIENT (off unless it is 1).
@MainActor final class LibraryGlowSetting: ObservableObject {
    static let shared = LibraryGlowSetting()
    @Published var on = MadeiraConfig.flag("MADEIRA_LIBRARY_AMBIENT", fallback: false) {
        didSet {
            guard on != oldValue else { return }
            MadeiraConfig.set("env.MADEIRA_LIBRARY_AMBIENT", on ? "1" : nil)
        }
    }
}

/// A pseudo-random sequence from a seed (SplitMix64), so each card's "movie" is its own
/// but stays the same for the whole run.
struct AmbientRandom {
    private var state: UInt64
    init(seed: Int) { state = UInt64(bitPattern: Int64(seed)) &+ 0x9E37_79B9_7F4A_7C15 }
    mutating func unit() -> Double {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        z ^= z >> 31
        return Double(z >> 11) / Double(1 << 53)
    }
}

/// The light a TV's ambient LED strip throws on the wall as a film plays, for a still
/// piece of artwork, sampled around the ring of light (`samples` points, clockwise).
///
/// - Brightness: arcs of the ring flare up or fall into shadow independently, each at
///   its own place, width and strength, rising (sometimes at once, like a cut),
///   drifting and fading over two to six seconds; six at a time, a third of them
///   shadows, over a moderate base level, so the ring is never lit uniformly.
/// - Colour: the ring's colours travel. Two soft zones sweep around it, one showing
///   the artwork turned half round (its colours from the opposite side), one showing it
///   mirrored (left and right swapped), and at each scene cut they jump and change
///   strength; the whole light also drifts slightly in hue.
struct AmbientMovie {
    static let samples = 36
    struct Frame {
        var hue: Double
        var light: [Double]
        var turned: [Double]
        var mirrored: [Double]
    }
    private struct Scene { var length, level, hue, turned, turnedShift, mirrored, mirroredShift: Double }
    private struct Slot { var period, offset: Double }
    private let seed: Int
    private let scenes: [Scene]
    private let slots: [Slot]
    private let total: Double
    private let phase: Double
    private let turnSpeed: Double
    private let mirrorSpeed: Double

    init(seed: Int) {
        var r = AmbientRandom(seed: seed)
        var scenes: [Scene] = []
        for _ in 0..<12 {
            scenes.append(Scene(length: 3.2 + r.unit() * 5.0, level: 0.45 + r.unit() * 0.3, hue: (r.unit() - 0.5) * 24,
                                turned: 0.1 + r.unit() * 0.5, turnedShift: r.unit() * 2 * .pi,
                                mirrored: 0.1 + r.unit() * 0.45, mirroredShift: r.unit() * 2 * .pi))
        }
        self.scenes = scenes
        slots = (0..<6).map { _ in Slot(period: 2.0 + r.unit() * 4.5, offset: r.unit() * 10) }
        total = scenes.reduce(0) { $0 + $1.length }
        phase = r.unit() * total
        turnSpeed = (r.unit() < 0.5 ? -1 : 1) * (0.12 + r.unit() * 0.23)
        mirrorSpeed = (r.unit() < 0.5 ? -1 : 1) * (0.1 + r.unit() * 0.2)
        self.seed = seed
    }

    /// One flare or shadow on the ring, for one life of its slot.
    private func event(slot: Int, life: Int) -> (center: Double, width: Double, amount: Double, drift: Double, rise: Double) {
        var r = AmbientRandom(seed: seed &* 1_000_003 &+ slot &* 7_919 &+ life)
        let center = r.unit() * 2 * .pi
        let width = 0.25 + r.unit() * 1.5
        let amount = r.unit() < 0.35 ? -(0.2 + r.unit() * 0.3) : 0.3 + r.unit() * 0.5
        return (center, width, amount, (r.unit() - 0.5) * 1.4, 0.04 + r.unit() * 0.3)
    }

    func frame(at time: Double) -> Frame {
        let t = (time + phase).truncatingRemainder(dividingBy: total)
        var start = 0.0, i = 0
        while i < scenes.count - 1 && start + scenes[i].length <= t { start += scenes[i].length; i += 1 }
        let now = scenes[i], before = scenes[(i + scenes.count - 1) % scenes.count]
        let x = min(1, (t - start) / 0.4)            // a scene cut, eased over 0.4 s
        let k = x * x * (3 - 2 * x)
        func mix(_ a: Double, _ b: Double) -> Double { a + (b - a) * k }
        func mixAngle(_ a: Double, _ b: Double) -> Double { a + remainder(b - a, 2 * .pi) * k }
        let p = phase
        let level = mix(before.level, now.level) + 0.035 * sin(time * 1.7 + p) + 0.025 * sin(time * 3.9 + p * 1.9)
        let hue = mix(before.hue, now.hue) + 3 * sin(time * 0.35 + p)
        let turnedAt = mixAngle(before.turnedShift, now.turnedShift) + turnSpeed * time
        let mirroredAt = mixAngle(before.mirroredShift, now.mirroredShift) + mirrorSpeed * time
        let turnedStrength = mix(before.turned, now.turned), mirroredStrength = mix(before.mirrored, now.mirrored)

        var bumps: [(center: Double, width: Double, amount: Double)] = []
        for (n, slot) in slots.enumerated() {
            let lives = (time + slot.offset) / slot.period
            let f = lives - lives.rounded(.down)
            let e = event(slot: n, life: Int(lives.rounded(.down)))
            let env: Double
            if f < e.rise { let y = f / e.rise; env = y * y * (3 - 2 * y) }
            else { env = pow(1 - (f - e.rise) / (1 - e.rise), 1.6) }
            bumps.append((e.center + e.drift * f, e.width, e.amount * env))
        }

        let n = Self.samples
        var light = [Double](repeating: 0, count: n), turned = light, mirrored = light
        for s in 0..<n {
            let a = Double(s) / Double(n) * 2 * .pi
            var v = level
            for b in bumps {
                let d = remainder(a - b.center, 2 * .pi), sigma = b.width / 2
                v += b.amount * exp(-(d * d) / (2 * sigma * sigma))
            }
            light[s] = max(0.12, min(1, v))
            turned[s] = turnedStrength * pow(0.5 + 0.5 * cos(a - turnedAt), 2)
            mirrored[s] = mirroredStrength * pow(0.5 + 0.5 * cos(2 * (a - mirroredAt)), 2)
        }
        return Frame(hue: hue, light: light, turned: turned, mirrored: mirrored)
    }

    /// Values around the ring as an angular mask, closed at the seam.
    static func mask(_ values: [Double]) -> AngularGradient {
        var stops = values.enumerated().map { Gradient.Stop(color: .black.opacity($1), location: Double($0) / Double(values.count)) }
        stops.append(.init(color: .black.opacity(values[0]), location: 1))
        return AngularGradient(stops: stops, center: .center)
    }
}

/// The faint rays in a card's light: beams fanning out from the artwork's centre,
/// each its own width and brightness (seeded, so a card keeps its own). Subtle on
/// purpose: the gaps between them stay nearly as bright as the beams, and they are
/// softened, so the glow reads as light with a little texture, not as stripes.
struct AmbientRays: View {
    let seed: Int
    /// Degrees the beams are turned by; the glow sways them slowly.
    var turn: Double = 0
    /// The same beams, crisp and contrasty: the light focused down while its card is pressed.
    var sharp = false
    private static let count = 60

    var body: some View {
        let floor = sharp ? 0.1 : 0.72
        var r = AmbientRandom(seed: seed &* 31 &+ 7)
        var stops: [Gradient.Stop] = []
        var at = 0.0
        while at < 1 {
            let width = (0.5 + r.unit()) / Double(Self.count)
            let level = floor + (1 - floor) * pow(r.unit(), 0.7)
            stops.append(.init(color: .black.opacity(level), location: at))
            stops.append(.init(color: .black.opacity(level), location: min(1, at + width * 0.55)))
            at += width
        }
        stops.append(.init(color: stops[0].color, location: 1))
        return AngularGradient(stops: stops, center: .center, angle: .degrees(turn))
            .blur(radius: sharp ? 0.6 : 3)
    }
}

/// Ambient light around a card's artwork, as an LED strip behind a TV lights the wall:
/// the artwork itself, a little larger and blurred, so each edge's colours spill from
/// that edge. Three versions of it are drawn once each (drawingGroup): as it is, turned
/// half round, and mirrored, which have the same outline but the colours in other
/// places; AmbientMovie blends them around the ring and lights arcs of it, and only
/// those masks, a slight hue drift and the rays' sway move, at 30 frames a second.
/// Dark appearance adds the light (plusLighter), as light does on a dark wall; light
/// appearance tints more softly. Reduce Motion holds it still.
struct AmbientGlow: View {
    let image: Image
    let size: CGSize
    let seed: Int
    var dimmed = false
    /// The card is pressed: the light closes like a spotlight's aperture, drawing in
    /// toward the artwork with its rays turning crisp, and goes out behind the shrunken
    /// artwork; on release it opens back up slowly.
    var pressed = false
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @ObservedObject private var scroll = LibraryScrollActivity.shared

    private func layer(_ angle: Double, _ mirror: Bool, frame: CGSize) -> some View {
        func art() -> some View {
            image.resizable().scaledToFill()
                .frame(width: size.width, height: size.height).clipped()
                .rotationEffect(.degrees(angle))
                .scaleEffect(x: mirror ? -1 : 1, y: 1)
        }
        // A soft wash on the wall, and the brighter band just past the edge where the
        // strip's light lands first.
        return ZStack {
            art().scaleEffect(1.09).blur(radius: size.width * 0.07)
            art().scaleEffect(1.03).blur(radius: size.width * 0.03).opacity(0.75)
        }
        .frame(width: frame.width, height: frame.height)
        // On a light page the blurred art's mid-tones read as grey: lifted and more
        // vivid there, it reads as coloured light.
        .saturation(dimmed ? 1.15 : (scheme == .dark ? 1.5 : 1.9))
        .brightness(scheme == .dark ? 0 : (dimmed ? 0.04 : 0.12))
        .drawingGroup()
    }

    var body: some View {
        // Kept tight, so neighbouring cards' light stays apart in the gaps between them.
        let spread = size.width * 0.17
        let frame = CGSize(width: size.width + spread * 2, height: size.height + spread * 2)
        let movie = AmbientMovie(seed: seed)
        let dark = scheme == .dark
        // A game that is not installed throws a fainter, less vivid light.
        let strength = (dark ? 0.92 : 0.8) * (dimmed ? 0.3 : 1)
        let plain = layer(0, false, frame: frame), turned = layer(180, false, frame: frame), mirrored = layer(0, true, frame: frame)
        // Still while the library scrolls: each glow is six blurred draws, a shader and
        // two masks, and redrawing every card's at 30 fps made scrolling stutter.
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: reduceMotion || scroll.scrolling)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            let f = movie.frame(at: reduceMotion ? 0 : t)
            ZStack {
                plain
                turned.mask { AmbientMovie.mask(f.turned) }
                mirrored.mask { AmbientMovie.mask(f.mirrored) }
            }
            .compositingGroup()
            .hueRotation(.degrees(f.hue))
            // In the dark the light adds to the page (plusLighter): a very bright artwork's
            // light is brought down at the top (AmbientGlow.metal) so it does not glare.
            // ml1219: off on a light page, where a knee of 0 left every pixel as it was.
            .colorEffect(ShaderLibrary.ambientKnee(.float(0.3)), isEnabled: dark)
            .mask { AmbientMovie.mask(f.light) }
            .mask {
                let turn = reduceMotion ? 0 : 1.2 * sin(t * 0.12 + Double(seed % 97))
                ZStack {
                    AmbientRays(seed: seed, turn: turn).opacity(pressed ? 0 : 1)
                    AmbientRays(seed: seed, turn: turn, sharp: true).opacity(pressed ? 1 : 0)
                }
                // The rays sharpen first, then the light draws in and goes out.
                .animation(pressed ? .easeOut(duration: 0.2) : .easeIn(duration: 0.9), value: pressed)
            }
            // Closed, the light's outer edge sits inside the shrunken artwork.
            .scaleEffect(pressed ? LibraryCardArtworkPress.pressedScale * size.width / frame.width : 1)
            .animation(pressed ? .easeInOut(duration: 0.55) : .easeInOut(duration: 1.6), value: pressed)
            .opacity(strength * (pressed ? 0 : 1))
            .animation(pressed ? .easeIn(duration: 0.6) : .easeOut(duration: 1.3), value: pressed)
            .blendMode(dark ? .plusLighter : .normal)
        }
        .frame(width: frame.width, height: frame.height)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// What a card's glow is made of: the same artwork the card shows.
enum AmbientArt {
    case steam(Int)
    case library(LibraryEntry)
    case url(URL?)   // a store's own artwork (Epic)
}

/// A grid card's artwork frame and what its glow is made of, reported up to the
/// library, which draws every glow in one layer behind all the cards
/// (AmbientGlowLayer): a card's own background would be painted over its
/// neighbours by the cards after it.
struct AmbientGlowItem: Identifiable {
    let id: String
    let seed: Int
    let art: AmbientArt
    var dimmed = false
    var pressed = false
    let bounds: Anchor<CGRect>
}

struct AmbientGlowKey: PreferenceKey {
    static let defaultValue: [AmbientGlowItem] = []
    static func reduce(value: inout [AmbientGlowItem], nextValue: () -> [AmbientGlowItem]) { value += nextValue() }
}

/// The library's ambient light: a glow behind every grid card that has reported
/// its frame (only the cards the lazy grids have built). MADEIRA_LIBRARY_AMBIENT=0
/// turns it off.
struct AmbientGlowLayer: View {
    @ObservedObject private var setting = LibraryGlowSetting.shared
    let items: [AmbientGlowItem]
    var body: some View {
        if setting.on {
            GeometryReader { proxy in
                var seen = Set<String>()
                let unique = items.filter { seen.insert($0.id).inserted }
                ForEach(unique) { item in AmbientGlowCard(item: item, frame: proxy[item.bounds]) }
            }
        }
    }
}

/// One card's glow, once its artwork is loaded.
private struct AmbientGlowCard: View {
    let item: AmbientGlowItem
    let frame: CGRect
    @State private var image: UIImage?
    var body: some View {
        ZStack {
            if let image = image ?? AmbientArtwork.cached(item.id) {
                AmbientGlow(image: Image(uiImage: image), size: frame.size, seed: item.seed, dimmed: item.dimmed, pressed: item.pressed)
            } else {
                Color.clear.frame(width: 1, height: 1)
            }
        }
        .position(x: frame.midX, y: frame.midY)
        .task(id: item.id) { if image == nil { image = await AmbientArtwork.load(item) } }
    }
}

/// A card's artwork for its glow: loaded once, shrunk (the glow blurs it anyway) and
/// kept, so the glow's three layers share one small bitmap instead of each decoding
/// the full artwork.
@MainActor
enum AmbientArtwork {
    private static let cache = NSCache<NSString, UIImage>()

    static func cached(_ id: String) -> UIImage? { cache.object(forKey: id as NSString) }

    static func load(_ item: AmbientGlowItem) async -> UIImage? {
        if let hit = cached(item.id) { return hit }
        var image: UIImage?
        switch item.art {
        case .steam(let appID):
            for url in SteamGamesRules.artwork(appID: appID, owned: { SteamOwnedLibrary.shared.game($0) }) {
                if let found = await fetch(url) { image = found; break }
            }
        case .library(let entry):
            if let name = entry.coverFile {
                image = UIImage(contentsOfFile: LibraryModel.documents
                    .appendingPathComponent("madeira-art/" + URL(fileURLWithPath: name).lastPathComponent).path)
            } else if let id = entry.steamID ?? entry.steamAppID, let url = SteamCatalog.cover(id) {
                image = await fetch(url)
            } else if let url = entry.epicArtworkURL {
                image = await fetch(url)
            }
        case .url(let url):
            if let url { image = await fetch(url) }
        }
        guard let image, image.size.width > 0 else { return nil }
        let size = CGSize(width: 96, height: (96 * image.size.height / image.size.width).rounded())
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let small = UIGraphicsImageRenderer(size: size, format: format).image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
        cache.setObject(small, forKey: item.id as NSString)
        return small
    }

    private static func fetch(_ url: URL) async -> UIImage? {
        await ArtworkCache.image(url)   // the card's own copy: one download per cover
    }
}



/// A button's glass, or with Liquid metal on (LiquidMetalSetting) the same flowing chrome
/// the toolbar pills and the Desktop button get: LiquidMetalFill clipped to the button's
/// shape, its label in black or white with a soft halo so it reads on the chrome.
/// Interactive Liquid Glass otherwise (iOS 26), a regular material before it.
struct LibraryMetalGlass<S: Shape>: ViewModifier {
    let shape: S
    @ObservedObject private var metal = LiquidMetalSetting.shared
    @Environment(\.colorScheme) private var scheme
    func body(content: Content) -> some View {
        if metal.on {
            let light = scheme == .light
            content
                .foregroundStyle(light ? Color.black : Color.white)
                .shadow(color: (light ? Color.white : Color.black).opacity(0.75), radius: 2.5)
                .background(LiquidMetalFill().clipShape(shape))
                .contentShape(shape)
        } else if #available(iOS 26.0, *) {
            content.glassEffect(.regular.interactive(), in: shape)
        } else {
            content.background(.regularMaterial, in: shape)
        }
    }
}
