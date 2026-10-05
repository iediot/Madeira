import SwiftUI

@main
struct MadeiraApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .modifier(ClaimGamepadEvents())
                .overlay { JITProgressOverlay() }
                .overlay { WineMonoOverlay() }
                .onAppear {
                    GamepadInput.shared.start()
                    HardwareInput.shared.start()
                }
                // madeira://play?exe=... (Home Screen shortcuts, SavesAndShortcuts.swift).
                .onOpenURL { url in ShortcutRouter.shared.handle(url) }
        }
    }
}
