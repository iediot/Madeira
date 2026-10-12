import SwiftUI
import UIKit.UIGestureRecognizerSubclass

@main
struct MadeiraApp: App {
    init() {
        // ml1172: read the screen on the main thread; library entries, whose
        // default Resolution comes from it, are also made on other threads.
        _ = ResolutionChoices.screen
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .modifier(ClaimGamepadEvents())
                .overlay { JITProgressOverlay() }
                .onReceive(LibraryController.shared.commands) { ControllerNavigation.shared.handle($0) }
                // Any touch hides the controller highlight. A window-level recognizer that
                // only watches: a root DragGesture took taps from list swipe actions (Log Out).
                .background { TouchWatcher { ControllerNavigation.shared.hideIndicator() } }
                .onAppear {
                    GamepadInput.shared.start()
                    HardwareInput.shared.start()
                }
                // madeira://play?exe=... (Home Screen shortcuts, SavesAndShortcuts.swift).
                .onOpenURL { url in ShortcutRouter.shared.handle(url) }
        }
    }
}

/// Calls `touched` on every touch in the window and never takes part in gesture
/// handling: it fails at once, cancels and delays nothing.
private struct TouchWatcher: UIViewRepresentable {
    let touched: () -> Void
    final class Recognizer: UIGestureRecognizer {
        var touched: (() -> Void)?
        override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
            touched?()
            state = .failed
        }
    }
    final class Host: UIView {
        let recognizer = Recognizer()
        override func didMoveToWindow() {
            super.didMoveToWindow()
            recognizer.view?.removeGestureRecognizer(recognizer)
            recognizer.cancelsTouchesInView = false
            recognizer.delaysTouchesBegan = false
            recognizer.delaysTouchesEnded = false
            window?.addGestureRecognizer(recognizer)
        }
    }
    func makeUIView(context: Context) -> Host {
        let host = Host()
        host.isUserInteractionEnabled = false
        host.recognizer.touched = touched
        return host
    }
    func updateUIView(_ host: Host, context: Context) { host.recognizer.touched = touched }
}
