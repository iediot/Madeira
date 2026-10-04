// SPDX-License-Identifier: GPL-3.0-or-later
// Madeira Converter Exception: see LICENSE-EXCEPTION.md

import Foundation
import SwiftUI

/// Enables JIT through StikDebug. Built-in StikJIT (an app extension with its own
/// App ID) was removed in this fork; StikDebug is the only method.
@MainActor
final class JITCoordinator: ObservableObject {
    static let shared = JITCoordinator()

    enum CoordinatorError: LocalizedError {
        case setupRequired(String)

        var errorDescription: String? {
            switch self {
            case .setupRequired(let message): return message
            }
        }
    }

    @Published var showSetup = false
    @Published private(set) var busy = false
    @Published private(set) var status: String?
    @Published private(set) var error: String?

    private init() {}

    func enable(completion: @escaping (Result<Void, Error>) -> Void) {
        guard SigningStatus.current.debuggable else {
            completion(.failure(NSError(
                domain: "MadeiraJIT", code: 11,
                userInfo: [NSLocalizedDescriptionKey: SigningStatus.notDebuggableMessage])))
            return
        }
        if StikJITHelper.ready {
            // Attached but no pool yet (JIT enabled before this build, or a retry):
            // take it now while the debugger is here.
            StikJITHelper.preparePoolNow(completion: completion)
            return
        }
        error = nil
        status = nil
        guard StikJITHelper.isAvailable else {
            let message = "StikDebug is not installed. Install it to enable JIT."
            error = message
            showSetup = true
            completion(.failure(CoordinatorError.setupRequired(message)))
            return
        }
        busy = true
        status = "Waiting for StikDebug…"
        // Keep running while StikDebug has the screen. Without this iOS suspends Madeira the
        // moment StikDebug opens, so the attach is only noticed -- and the JIT pool only
        // prepared -- once the user comes back, by which time StikDebug is in the background
        // and throttled: preparing 448 MB then took up to 8.3 s with Madeira frozen. With
        // background time the preparation starts while StikDebug is still in front.
        let app = UIApplication.shared
        var bgTask = UIBackgroundTaskIdentifier.invalid
        let endBackground = {
            if bgTask != .invalid { app.endBackgroundTask(bgTask); bgTask = .invalid }
        }
        bgTask = app.beginBackgroundTask(withName: "madeira.jit") { endBackground() }
        StikJITHelper.enableJIT { [weak self] result in
            Task { @MainActor in
                guard case .success = result else {
                    self?.busy = false
                    if case .failure(let failure) = result { self?.error = failure.localizedDescription }
                    endBackground()
                    completion(result)
                    return
                }
                self?.status = "Setting up JIT memory…"
                StikJITHelper.preparePoolNow { pooled in
                    self?.busy = false
                    switch pooled {
                    case .success: self?.status = "JIT is ready."
                    case .failure(let failure): self?.error = failure.localizedDescription
                    }
                    endBackground()
                    completion(pooled)
                }
            }
        }
    }
}

/// LocalDevVPN, which StikDebug reaches the device through: open it to
/// connect when it is installed, otherwise its App Store page.
enum LocalDevVPN {
    static let appStore = URL(string: "https://apps.apple.com/us/app/localdevvpn/id6755608044")!
    /// `enable` connects the VPN; `scheme` has LocalDevVPN return to Madeira a second later.
    static let connect = URL(string: "localdevvpn://enable?scheme=madeira")!

    static var isInstalled: Bool { UIApplication.shared.canOpenURL(URL(string: "localdevvpn://")!) }
    static var actionTitle: String { isInstalled ? "Connect LocalDevVPN" : "Get LocalDevVPN" }

    static func open() {
        let installed = isInstalled
        LogStore.shared.log("[jit] LocalDevVPN \(installed ? "connect" : "app-store")")
        UIApplication.shared.open(installed ? connect : appStore)
    }
}

struct JITSettingsSection: View {
    @ObservedObject private var coordinator = JITCoordinator.shared
    @ObservedObject private var onboarding = OnboardingModel.shared

    var body: some View {
        Section {
            Button {
                coordinator.showSetup = true
            } label: {
                Label("JIT setup", systemImage: "bolt.badge.clock")
            }
            if onboarding.available {
                Button {
                    onboarding.rerun()
                } label: {
                    Label("Run setup again", systemImage: "wand.and.stars")
                }
            }
        } header: {
            Text("JIT")
        }
    }
}

struct JITSetupView: View {
    @ObservedObject private var coordinator = JITCoordinator.shared
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    LabeledContent("StikDebug",
                                   value: StikJITHelper.isAvailable ? "Installed" : "Not detected")
                    if StikJITHelper.isAvailable {
                        Button("Enable JIT with StikDebug") { coordinator.enable() { _ in } }
                            .disabled(coordinator.busy)
                    } else {
                        Link("Install StikDebug",
                             destination: URL(string: "https://github.com/StikDebug/StikDebug/releases/latest")!)
                    }
                    Button(LocalDevVPN.actionTitle) { LocalDevVPN.open() }
                } header: {
                    Text("StikDebug")
                }

                if coordinator.busy {
                    Section { HStack { ProgressView(); Text(coordinator.status ?? "Working…") } }
                } else if let error = coordinator.error {
                    Section {
                        Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red)
                    }
                } else if let status = coordinator.status {
                    Section { Label(status, systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
                }
            }
            .navigationTitle("JIT setup")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { coordinator.showSetup = false; dismiss() }
                }
            }
        }
    }
}
