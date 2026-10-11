import SwiftUI

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
                .simultaneousGesture(DragGesture(minimumDistance: 0).onChanged { _ in
                    ControllerNavigation.shared.hideIndicator()
                })
                .onAppear {
                    GamepadInput.shared.start()
                    HardwareInput.shared.start()
                }
                // madeira://play?exe=... (Home Screen shortcuts, SavesAndShortcuts.swift).
                .onOpenURL { url in ShortcutRouter.shared.handle(url) }
        }
    }
}
