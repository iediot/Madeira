#!/usr/bin/env python3
"""Host-test the production random selection policy; no network or app build."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'app/Madeira/Store/SteamStore.swift').read_text()
models = source[source.index('struct StoreGame:'):source.index('enum StoreAction:')]
harness = 'import Foundation\n' + models + r'''
let shot = URL(string:"https://example.test/screenshot.jpg")!
let poster = URL(string:"https://example.test/trailer.jpg")!
let url = URL(string:"https://example.test/trailer.mp4")!
var game = StoreGame(id:1,name:"Fixture",price:"",discount:0,isFree:false)
assert(game.randomBackdrop(allowsVideo:false).image == nil)
assert(game.randomBackdrop(allowsVideo:true).movie == nil)
game.movies = [StoreMovie(id:1,name:"Trailer",thumbnail:poster,url:url)]
assert(game.randomBackdrop(allowsVideo:false).image == nil)
assert(game.randomBackdrop(allowsVideo:false).movie == nil)
assert(game.randomBackdrop(allowsVideo:true).movie?.url == url)
assert(game.randomBackdrop(allowsVideo:true).image == poster)
game.screenshots = [StoreScreenshot(id:1,full:shot,thumbnail:shot)]
for _ in 0..<1000 {
    let loading = game.randomBackdrop(allowsVideo:false)
    assert(loading.image == shot && loading.movie == nil)
    let hero = game.randomBackdrop(allowsVideo:true)
    assert(hero.image == shot)
    assert(hero.movie == nil || hero.movie?.url == url)
}
game.movies = []
assert(game.randomBackdrop(allowsVideo:true).image == shot)
let shot2 = URL(string:"https://example.test/second.png")!
game.screenshots += [StoreScreenshot(id:2,full:shot2,thumbnail:shot2)]
game.movies = [StoreMovie(id:1,name:"Trailer",thumbnail:poster,url:url)]
for _ in 0..<1000 {
    let deck = game.shuffledBackdrops(allowsVideo:true, avoiding:.image(shot))
    assert(deck.count == 3 && deck.first != .image(shot))
    assert(deck.contains(.image(shot)) && deck.contains(.image(shot2)))
    assert(deck.contains(.movie(url,poster:poster)))
    assert(game.shuffledBackdrops(allowsVideo:false).allSatisfy { $0.movieURL == nil })
}
game.screenshots += [StoreScreenshot(id:3,full:shot,thumbnail:shot)]
assert(game.shuffledBackdrops(allowsVideo:false).count == 2)
print("PASS: screenshot-only loading, shuffled full decks, no boundary repeat, deduplication and posters")
'''
with tempfile.TemporaryDirectory(prefix='madeira-footage-test-') as directory:
    path = Path(directory)
    main = path / 'main.swift'
    main.write_text(harness)
    exe = path / 'test'
    subprocess.run(['xcrun','swiftc',str(main),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
view = (root / 'app/Madeira/Store/StoreView.swift').read_text()
assert 'queue.isMuted = true' in view
assert 'library.current == nil && library.startingJIT == nil' in view
assert 'pixels: 2560' in view
assert '.task(id: PlaybackKey(active: active, slideID: slide?.id, ended: advance))' in view
assert 'remaining = 10' in view
assert 'if slide.item.movieURL != nil' in view
assert 'if active { player?.play() } else { player?.pause() }' in view
assert '.AVPlayerItemDidPlayToEndTime' in view
assert 'AVPlayerLooper' not in view[view.index('private struct FootageVideo:'):view.index('struct StoreGameMedia:')]
assert 'static func dismantleUIView' in view
assert 'AVAudioSession' not in view[view.index('private struct FootageVideo:'):view.index('struct StoreGameMedia:')]

# Execute the production timer body with a scaled sleep (1ms instead of250ms).
# SwiftUI activity-key wiring above is structural; an iPad test is still needed.
start = view.index('while remaining > 0 {')
end = view.index('await nextSlide()', start) + len('await nextSlide()')
timer = view[start:end].replace('250_000_000', '1_000_000')
clock_test = '''import Foundation
@MainActor final class Clock {
    var remaining: Double = 10
    var transitions = 0
    func nextSlide() async { transitions += 1 }
    func run() async {
''' + timer + '''
    }
}
@main struct Test {
    @MainActor static func main() async throws {
        let clock = Clock()
        let first = Task { await clock.run() }
        while clock.remaining > 9 { try await Task.sleep(nanoseconds: 1_000_000) }
        first.cancel()
        await first.value
        let paused = clock.remaining
        assert(paused > 0 && clock.transitions == 0)
        try await Task.sleep(nanoseconds: 5_000_000)
        assert(clock.remaining == paused)
        await clock.run()
        assert(clock.remaining == 0 && clock.transitions == 1)
        for _ in 0..<3 {
            clock.remaining = 10
            await clock.run()
        }
        assert(clock.transitions == 4)
        print("PASS: screenshot timer advances repeatedly and preserves remaining time on cancellation/resume")
    }
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-footage-clock-') as directory:
    path = Path(directory)
    (path / 'main.swift').write_text(clock_test)
    subprocess.run(['xcrun','swiftc','-parse-as-library',str(path / 'main.swift'),'-o',str(path / 'test')],check=True)
    subprocess.run([str(path / 'test')],check=True,timeout=10)
hero = (root / 'app/Madeira/LibraryHome.swift').read_text()
hero = hero[hero.index('struct LibraryHero:'):hero.index('struct LibraryHome:')]
assert 'LibraryBadges(' not in hero
assert 'wide ? 30 : 25' in hero
assert hero.index('Label("Play"') < hero.index('Text("Played ')
