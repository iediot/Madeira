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
        case cancelled

        var errorDescription: String? {
            switch self {
            case .setupRequired(let message): return message
            case .cancelled: return "Enabling JIT was cancelled."
            }
        }
    }

    @Published var showSetup = false
    @Published private(set) var busy = false
    @Published private(set) var status: String?
    @Published private(set) var error: String?
    /// The completions of the request waiting for StikDebug; nil when none is.
    private var pendingEnable: ((Result<Void, Error>) -> Void)?
    /// The request in progress; cancel() moves it on so a late answer is ignored.
    private var generation = 0

    /// Still waiting for StikDebug to attach (the overlay offers Cancel then).
    var waitingForStikDebug: Bool { busy && pendingEnable != nil && status == "Waiting for StikDebug…" }

    /// Give up waiting for StikDebug (the overlay's Cancel). A debugger that
    /// attaches later is still used: the next Play takes the pool then.
    func cancel() {
        guard waitingForStikDebug else { return }
        generation += 1
        busy = false
        status = nil
        let waiting = pendingEnable
        pendingEnable = nil
        waiting?(.failure(CoordinatorError.cancelled))
    }

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
            guard !StikJITHelper.poolTaken else { completion(.success(())); return }
            busy = true
            status = "Setting up JIT memory…"
            StikJITHelper.preparePoolNow { [weak self] pooled in
                self?.busy = false
                if case .failure(let failure) = pooled { self?.error = failure.localizedDescription }
                completion(pooled)
            }
            return
        }
        // A request is already waiting for StikDebug (Play tapped again): open
        // StikDebug again, but wait for the same attach. Two waiters both took a
        // JIT pool when StikDebug attached, and the second one's BAD POOL ended
        // the game ten seconds in.
        if let waiting = pendingEnable {
            pendingEnable = { result in waiting(result); completion(result) }
            StikJITHelper.enableJIT(wait: false) { _ in }
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
        pendingEnable = completion
        generation += 1
        let request = generation
        let finish: (Result<Void, Error>) -> Void = { [weak self] result in
            let waiting = self?.pendingEnable ?? completion
            self?.pendingEnable = nil
            waiting(result)
        }
        StikJITHelper.enableJIT { [weak self] result in
            Task { @MainActor in
                // Cancelled from the overlay: that already finished this request.
                guard self?.generation == request else { endBackground(); return }
                guard case .success = result else {
                    self?.busy = false
                    if case .failure(let failure) = result { self?.error = failure.localizedDescription }
                    endBackground()
                    finish(result)
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
                    finish(pooled)
                }
            }
        }
    }
}

/// Shown over the whole app from the moment JIT is requested until it is ready, so
/// the seconds Madeira spends waiting for StikDebug and then frozen while the
/// debugger sets up the JIT memory read as work in progress, not a hang.
struct JITProgressOverlay: View {
    @ObservedObject private var coordinator = JITCoordinator.shared

    var body: some View {
        ZStack {
            if coordinator.busy { card }
        }
        .animation(.easeInOut(duration: 0.2), value: coordinator.busy)
    }

    private var card: some View {
        ZStack {
            Color.black.opacity(0.35).ignoresSafeArea()
            VStack(spacing: 12) {
                ProgressView().controlSize(.large)
                Text("Enabling JIT").font(.headline)
                if let status = coordinator.status {
                    Text(status).font(.subheadline).foregroundStyle(.secondary)
                }
                if coordinator.waitingForStikDebug {
                    Button("Cancel") { coordinator.cancel() }.padding(.top, 4)
                }
            }
            .multilineTextAlignment(.center)
            .padding(24)
            .frame(minWidth: 220)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20))
        }
        .transition(.opacity)
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
    /// Settings › JIT: only Enable JIT automatically. Madeira opens StikDebug itself
    /// (the Enable JIT button, or this switch at start), so the old JIT setup page and
    /// the JIT/Memory+ rows had nothing left to do here.
    var autoEnable: Binding<Bool>

    var body: some View {
        Section {
            Toggle("Enable JIT automatically", isOn: autoEnable)
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
