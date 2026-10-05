import SwiftUI
import UIKit

// The touch-controls editor: the game dims behind a fine grid, a glass inspector
// docks to one side of the screen (away from the control being edited), and
// controls snap into line with each other while they are dragged. With nothing
// selected the inspector adds controls; with a control selected it sets its
// action, size and binding, duplicates or deletes it. Undo steps back through
// every change of this edit; Revert returns to how the layout was when it began.

// MARK: - Catalogue

/// What the editor offers, grouped as the inspector shows it.
enum ControlCatalog {
    enum Kind: String, CaseIterable, Identifiable {
        case controller, keyboard, mouse
        var id: String { rawValue }
        var title: String {
            switch self {
            case .controller: return "Controller"
            case .keyboard: return "Keyboard"
            case .mouse: return "Mouse"
            }
        }

        /// The tab a control's action belongs to.
        static func of(_ action: ControlAction) -> Kind {
            switch action {
            case .pad: return .controller
            case .mouseLeft, .mouseRight, .mouseLook, .keyboardToggle, .none: return .mouse
            default: return .keyboard
            }
        }
    }

    struct Group: Identifiable {
        let title: String
        let items: [(String, ControlAction)]
        var id: String { title }
    }

    static func groups(_ kind: Kind) -> [Group] {
        switch kind {
        case .controller:
            // Start and Select are XInput's Menu and View; the layout keeps the
            // XInput names, the chips read Start/Select.
            return [
                Group(title: "Sticks", items: [("LS", .pad("LS")), ("RS", .pad("RS")), ("L3", .pad("L3")), ("R3", .pad("R3"))]),
                Group(title: "Face", items: [("A", .pad("A")), ("B", .pad("B")), ("X", .pad("X")), ("Y", .pad("Y"))]),
                Group(title: "D-pad", items: [("D↑", .pad("D↑")), ("D↓", .pad("D↓")), ("D←", .pad("D←")), ("D→", .pad("D→"))]),
                Group(title: "Bumpers & triggers", items: [("LB", .pad("LB")), ("RB", .pad("RB")), ("LT", .pad("LT")), ("RT", .pad("RT"))]),
                Group(title: "System", items: [("Start", .pad("Menu")), ("Select", .pad("View")), ("Guide", .pad("Guide"))]),
            ]
        case .mouse:
            return [
                Group(title: "Pointer", items: [("Left click", .mouseLeft), ("Right click", .mouseRight), ("Mouse look", .mouseLook)]),
                Group(title: "Other", items: [("Keyboard", .keyboardToggle), ("None", .none)]),
            ]
        case .keyboard:
            // The main keys are on the drawn keyboard (KeyboardPicker); these are the rest.
            let fkeys = (0...11).map { ("F\($0 + 1)", ControlAction.key(Int32(0x70 + $0))) }
            let numpad = (0...9).map { ("N\($0)", ControlAction.key(Int32(0x60 + $0))) }
                + [("N*", .key(0x6A)), ("N+", .key(0x6B)), ("N−", .key(0x6D)), ("N.", .key(0x6E)), ("N/", .key(0x6F))]
            return [
                Group(title: "Function", items: fkeys),
                Group(title: "Navigation", items: [
                    ("Ins", .key(0x2D)), ("Del", .key(0x2E)), ("Home", .key(0x24)),
                    ("End", .key(0x23)), ("PgUp", .key(0x21)), ("PgDn", .key(0x22)),
                ]),
                Group(title: "Numpad", items: numpad),
            ]
        }
    }

    /// The keyboard's movement sticks, above the drawn keyboard.
    static let movement: Group = Group(title: "Movement", items: [("WASD stick", .joystickWASD), ("Arrows stick", .joystickArrows)])

    /// A control's name in the inspector's header.
    static func name(_ action: ControlAction) -> String {
        for kind in Kind.allCases {
            for group in groups(kind) + [movement] {
                if let hit = group.items.first(where: { $0.1 == action }) { return hit.0 }
            }
        }
        if case .key(let vk) = action, let key = KeyboardPicker.rows.joined().first(where: { $0.vk == vk }) {
            return key.name
        }
        return action.label
    }
}

// MARK: - Keyboard

/// A drawn PC keyboard to pick a key from: the five main rows in their real
/// shape and proportions (every row is 15 key units wide), the chosen key lit.
struct KeyboardPicker: View {
    struct Key: Identifiable {
        let label: String
        let vk: Int32
        var units: CGFloat = 1
        var id: String { "\(label)-\(vk)-\(units)" }
        /// The header's name: the label, or the word for a symbol-only one.
        var name: String {
            switch vk {
            case 0x20: return "Space"
            case 0x25: return "Left arrow"
            case 0x26: return "Up arrow"
            case 0x27: return "Right arrow"
            case 0x28: return "Down arrow"
            default: return label
            }
        }
    }

    static let rows: [[Key]] = {
        func letters(_ s: String) -> [Key] { s.map { Key(label: String($0), vk: Int32($0.asciiValue!)) } }
        return [
            [Key(label: "`", vk: 0xC0)] + letters("1234567890")
                + [Key(label: "-", vk: 0xBD), Key(label: "=", vk: 0xBB), Key(label: "Bksp", vk: 0x08, units: 2)],
            [Key(label: "Tab", vk: 0x09, units: 1.5)] + letters("QWERTYUIOP")
                + [Key(label: "[", vk: 0xDB), Key(label: "]", vk: 0xDD), Key(label: "\\", vk: 0xDC, units: 1.5)],
            [Key(label: "Caps", vk: 0x14, units: 1.75)] + letters("ASDFGHJKL")
                + [Key(label: ";", vk: 0xBA), Key(label: "'", vk: 0xDE), Key(label: "Enter", vk: 0x0D, units: 2.25)],
            [Key(label: "Shift", vk: 0x10, units: 2.25)] + letters("ZXCVBNM")
                + [Key(label: ",", vk: 0xBC), Key(label: ".", vk: 0xBE), Key(label: "/", vk: 0xBF), Key(label: "Shift", vk: 0x10, units: 2.75)],
            [Key(label: "Esc", vk: 0x1B, units: 1.25), Key(label: "Ctrl", vk: 0x11, units: 1.5), Key(label: "Win", vk: 0x5B, units: 1.25),
             Key(label: "Alt", vk: 0x12, units: 1.25), Key(label: "Space", vk: 0x20, units: 5.5),
             Key(label: "←", vk: 0x25), Key(label: "↑", vk: 0x26), Key(label: "↓", vk: 0x28), Key(label: "→", vk: 0x27, units: 1.25)],
        ]
    }()

    /// The key the selected control presses, lit.
    let selected: Int32?
    let pick: (Int32) -> Void

    var body: some View {
        GeometryReader { geo in
            let gap: CGFloat = 4
            let unit = (geo.size.width - 14 * gap) / 15
            VStack(spacing: gap) {
                ForEach(Array(Self.rows.enumerated()), id: \.offset) { _, row in
                    HStack(spacing: gap) {
                        ForEach(row) { key in keyView(key, width: unit * key.units + gap * (key.units - 1)) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .frame(height: 5 * 34 + 4 * 4)
    }

    private func keyView(_ key: Key, width: CGFloat) -> some View {
        let on = key.vk == selected
        return Button {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            pick(key.vk)
        } label: {
            Text(key.label)
                .font(.system(size: key.label.count > 1 ? 11 : 14, weight: .medium))
                .lineLimit(1).minimumScaleFactor(0.5)
                .foregroundStyle(.white.opacity(on ? 1 : 0.9))
                .frame(width: max(width, 1), height: 34)
                .background(RoundedRectangle(cornerRadius: 7).fill(on ? Color.accentColor : .white.opacity(0.13)))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(key.name)
    }
}

// MARK: - History

/// Undo for one edit: the controls before each change, newest last.
final class ControlEditorHistory: ObservableObject {
    static let shared = ControlEditorHistory()
    @Published private(set) var steps: [[TouchControl]] = []
    /// The layout when the edit began (Revert).
    private(set) var start: [TouchControl] = []

    func begin(_ controls: [TouchControl]) { steps = []; start = controls }

    /// Call before a change.
    func record() {
        let now = TouchControlsModel.shared.controls
        if steps.last != now { steps.append(now) }
        if steps.count > 60 { steps.removeFirst() }
    }

    func undo() {
        guard let last = steps.popLast() else { return }
        TouchControlsModel.shared.selected = nil
        TouchControlsModel.shared.controls = last
    }

    var canRevert: Bool { TouchControlsModel.shared.controls != start }

    func revert() {
        record()
        TouchControlsModel.shared.selected = nil
        TouchControlsModel.shared.controls = start
    }
}

// MARK: - Snapping

/// While a control is dragged it snaps into line with other controls' centres and
/// with the screen's middle; the lines it snapped to are drawn.
final class ControlSnap: ObservableObject {
    static let shared = ControlSnap()
    /// Screen x and y of the guides shown now.
    @Published private(set) var vertical: CGFloat?
    @Published private(set) var horizontal: CGFloat?
    /// A control is being dragged: the inspector fades out of the way meanwhile.
    @Published private(set) var dragging = false
    private let threshold: CGFloat = 8
    private let feedback = UISelectionFeedbackGenerator()

    /// The normalised position for `proposed` (normalised), snapped.
    func snap(_ proposed: CGPoint, moving id: UUID, in screen: CGSize) -> CGPoint {
        if !dragging { dragging = true }
        let p = CGPoint(x: proposed.x * screen.width, y: proposed.y * screen.height)
        let others = TouchControlsModel.shared.controls.filter { $0.id != id }
        let xs = others.map { CGFloat($0.nx) * screen.width } + [screen.width / 2]
        let ys = others.map { CGFloat($0.ny) * screen.height } + [screen.height / 2]
        let gx = xs.min { abs($0 - p.x) < abs($1 - p.x) }.flatMap { abs($0 - p.x) <= threshold ? $0 : nil }
        let gy = ys.min { abs($0 - p.y) < abs($1 - p.y) }.flatMap { abs($0 - p.y) <= threshold ? $0 : nil }
        if (gx != nil && gx != vertical) || (gy != nil && gy != horizontal) { feedback.selectionChanged() }
        if vertical != gx { vertical = gx }
        if horizontal != gy { horizontal = gy }
        return CGPoint(x: (gx ?? p.x) / screen.width, y: (gy ?? p.y) / screen.height)
    }

    func clear() {
        if dragging { dragging = false }
        if vertical != nil { vertical = nil }
        if horizontal != nil { horizontal = nil }
    }
}

// MARK: - Backdrop

/// Behind the controls while editing: the game dimmed under a fine grid, the
/// snap guides, and a tap on empty space clears the selection.
struct ControlEditorBackdrop: View {
    let screen: CGSize
    @ObservedObject private var snap = ControlSnap.shared
    @ObservedObject private var m = TouchControlsModel.shared

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.black.opacity(0.42)
            Canvas { context, size in
                let step: CGFloat = 32
                var grid = Path()
                var x = step
                while x < size.width { grid.move(to: CGPoint(x: x, y: 0)); grid.addLine(to: CGPoint(x: x, y: size.height)); x += step }
                var y = step
                while y < size.height { grid.move(to: CGPoint(x: 0, y: y)); grid.addLine(to: CGPoint(x: size.width, y: y)); y += step }
                context.stroke(grid, with: .color(.white.opacity(0.06)), lineWidth: 0.5)
            }
            if let x = snap.vertical {
                Rectangle().fill(Color.accentColor).frame(width: 1, height: screen.height).offset(x: x - 0.5)
            }
            if let y = snap.horizontal {
                Rectangle().fill(Color.accentColor).frame(width: screen.width, height: 1).offset(y: y - 0.5)
            }
        }
        .frame(width: screen.width, height: screen.height)
        .contentShape(Rectangle())
        .onTapGesture {
            withAnimation(.snappy(duration: 0.25)) { m.selected = nil }
        }
        .allowsHitTesting(true)
        .transition(.opacity)
    }
}

// MARK: - Toolbar

/// The edit's own bar, top centre: Done, Undo and Revert.
struct ControlEditorBar: View {
    @ObservedObject private var m = TouchControlsModel.shared
    @ObservedObject private var history = ControlEditorHistory.shared

    var body: some View {
        HStack(spacing: 10) {
            Button {
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
                withAnimation(.easeInOut(duration: 0.22)) { m.selected = nil; m.editing = false }
            } label: {
                Text("Done").font(.system(size: 16, weight: .semibold)).foregroundStyle(.white)
                    .padding(.horizontal, 20).frame(height: 44)
                    .background(Capsule().fill(Color.accentColor))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Done editing controls")
            icon("arrow.uturn.backward", label: "Undo", enabled: !history.steps.isEmpty) {
                withAnimation(.snappy(duration: 0.25)) { history.undo() }
            }
            icon("arrow.counterclockwise", label: "Revert all changes", enabled: history.canRevert) {
                withAnimation(.snappy(duration: 0.25)) { history.revert() }
            }
        }
        .padding(.top, 10)
    }

    private func icon(_ symbol: String, label: String, enabled: Bool, _ action: @escaping () -> Void) -> some View {
        Button {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            action()
        } label: {
            Image(systemName: symbol).font(.system(size: 17, weight: .medium))
                .foregroundStyle(.white.opacity(enabled ? 1 : 0.35))
                .frame(width: 44, height: 44)
                .background(GlassShape(circle: true))
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityLabel(label)
    }
}

// MARK: - Inspector

/// The glass panel docked to a side of the screen: Add with nothing selected, the
/// selected control's settings otherwise. It docks on the side away from the
/// selected control, so it never covers what is being edited.
struct ControlInspector: View {
    let screen: CGSize
    @ObservedObject private var m = TouchControlsModel.shared
    @ObservedObject private var input = InputSettings.shared
    @ObservedObject private var snap = ControlSnap.shared
    @State private var kind: ControlCatalog.Kind = .controller
    @State private var sizing = false
    /// Folded to a small handle at the screen's edge, so the whole layout shows.
    @AppStorage("madeiraControlInspectorFolded") private var folded = false

    /// The selected control, looked up by id on every read: a view must never keep an
    /// index, which a delete turns into a read past the end (a crash).
    private var selectedControl: TouchControl? { m.selected.flatMap { id in m.controls.first { $0.id == id } } }
    private func update(_ id: UUID, _ change: (inout TouchControl) -> Void) {
        guard let i = m.controls.firstIndex(where: { $0.id == id }) else { return }
        change(&m.controls[i])
    }
    /// Wider on the Keyboard tab, so the drawn keyboard's keys stay big enough to hit.
    private var width: CGFloat {
        kind == .keyboard ? min(560, screen.width * 0.62)
                          : min(screen.width >= 900 ? 340 : 290, screen.width * 0.42)
    }
    /// The key the selected control presses, for the drawn keyboard.
    private var selectedKey: Int32? {
        guard case .key(let vk) = selectedControl?.action else { return nil }
        return vk
    }
    /// Left when the selected control is on the right half, else right.
    private var dockLeft: Bool { (selectedControl?.nx ?? 0) > 0.5 }

    var body: some View {
        ZStack {
            if folded {
                handle
            } else {
                panel
                    // Out of the way while a control is dragged under it.
                    .opacity(snap.dragging ? 0.12 : 1)
                    .allowsHitTesting(!snap.dragging)
                    .transition(.move(edge: dockLeft ? .leading : .trailing).combined(with: .opacity))
            }
        }
        // ml2200: the folded tab stays findable while selection moves the open panel.
        .frame(width: screen.width, height: screen.height, alignment: !folded && dockLeft ? .leading : .trailing)
        // ml2200: keep the tab above dragged controls; only the open panel fades.
        .zIndex(1)
        .environment(\.colorScheme, .dark)
        .animation(.snappy(duration: 0.3), value: dockLeft)
        .animation(.snappy(duration: 0.3), value: kind)
        .animation(.snappy(duration: 0.3), value: folded)
        .animation(.easeOut(duration: 0.15), value: snap.dragging)
        .onAppear { syncKind() }
        .onChange(of: m.selected) { _, _ in syncKind() }
    }

    /// The folded inspector: a glass tab on the edge that opens it again.
    private var handle: some View {
        Button {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            folded = false
        } label: {
            Image(systemName: "chevron.left")
                .font(.system(size: 16, weight: .semibold)).foregroundStyle(.white)
                .frame(width: 36, height: 72)
                .background(GlassShape(cornerRadius: 18))
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 8)
        .accessibilityLabel("Show the control editor")
        .transition(.opacity)
    }

    private var panel: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                Button {
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    folded = true
                } label: {
                    Image(systemName: dockLeft ? "chevron.left" : "chevron.right")
                        .font(.system(size: 14, weight: .semibold)).foregroundStyle(.white.opacity(0.8))
                        .frame(width: 30, height: 36)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Hide the control editor")
                if let control = selectedControl {
                    Text(ControlCatalog.name(control.action)).font(.headline).foregroundStyle(.white).lineLimit(1)
                    Spacer(minLength: 4)
                    iconButton("plus.square.on.square", label: "Duplicate") { duplicate(control.id) }
                    iconButton("trash", label: "Delete", tint: .red) { delete(control.id) }
                } else {
                    Text("Add a control").font(.headline).foregroundStyle(.white)
                    Spacer(minLength: 0)
                }
            }
            if let control = selectedControl { settings(control) }
            Picker("Kind", selection: $kind) {
                ForEach(ControlCatalog.Kind.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if kind == .keyboard {
                        chips(ControlCatalog.movement)
                        VStack(alignment: .leading, spacing: 6) {
                            caption("Keys")
                            KeyboardPicker(selected: selectedKey) { choose(.key($0)) }
                        }
                    }
                    ForEach(ControlCatalog.groups(kind)) { group in chips(group) }
                }
                .padding(.bottom, 8)
            }
            .scrollIndicators(.hidden)
        }
        .padding(16)
        .frame(width: width)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(GlassShape(cornerRadius: 24))
        .padding(.vertical, 12)
        .padding(.horizontal, 12)
    }

    private func syncKind() {
        if let control = selectedControl { kind = ControlCatalog.Kind.of(control.action) }
    }

    // The selected control's Size, then the settings its action has (mouse-look
    // sensitivity, a controller binding). Every read and write goes by id.
    @ViewBuilder private func settings(_ control: TouchControl) -> some View {
        let id = control.id
        VStack(alignment: .leading, spacing: 4) {
            caption("Size")
            Slider(value: Binding(get: { m.controls.first { $0.id == id }?.scale ?? 1 },
                                  set: { value in update(id) { $0.scale = value } }),
                   in: 0.5...3.0) { editing in
                if editing && !sizing { ControlEditorHistory.shared.record() }
                sizing = editing
            }
        }
        if control.action == .mouseLook {
            VStack(alignment: .leading, spacing: 4) {
                caption("Look sensitivity")
                Slider(value: $input.sensRel, in: 0.1...8)
            }
        }
        if GamepadInput.keyboardMouseAvailable, !control.action.isPad, control.action != .none,
           control.action != .keyboardToggle, control.action != .mouseLook {
            binding(control)
        }
        caption("Action")
    }

    /// Keyboard-and-mouse controller mode: the physical controller input that also
    /// performs this control's action. Tapping the chosen one clears it.
    private func binding(_ control: TouchControl) -> some View {
        let id = control.id
        let names = control.action.stickKeys != nil ? PadBindings.stickNames : PadBindings.buttonNames
        return VStack(alignment: .leading, spacing: 6) {
            caption("Controller button")
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 46), spacing: 6)], spacing: 6) {
                ForEach(names, id: \.self) { name in
                    let on = control.padBinding == name
                    chip(name == "Menu" ? "Start" : name == "View" ? "Select" : name, on: on) {
                        ControlEditorHistory.shared.record()
                        update(id) { $0.padBinding = on ? nil : name }
                    }
                }
            }
        }
    }

    private func chips(_ group: ControlCatalog.Group) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            caption(group.title)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: group.items.contains { $0.0.count > 5 } ? 84 : 46), spacing: 6)], spacing: 6) {
                ForEach(Array(group.items.enumerated()), id: \.offset) { _, item in
                    let on = selectedControl?.action == item.1
                    chip(item.0, on: on) { choose(item.1) }
                }
            }
        }
    }

    private func chip(_ text: String, on: Bool, _ action: @escaping () -> Void) -> some View {
        Button {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            action()
        } label: {
            Text(text)
                .font(.system(size: 13, weight: .medium)).lineLimit(1).minimumScaleFactor(0.6)
                .foregroundStyle(on ? .white : .white.opacity(0.9))
                .frame(maxWidth: .infinity, minHeight: 34)
                .background(RoundedRectangle(cornerRadius: 9).fill(on ? Color.accentColor : .white.opacity(0.12)))
        }
        .buttonStyle(.plain)
    }

    private func iconButton(_ symbol: String, label: String, tint: Color = .white, _ action: @escaping () -> Void) -> some View {
        Button {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            action()
        } label: {
            Image(systemName: symbol).font(.system(size: 15, weight: .medium)).foregroundStyle(tint)
                .frame(width: 36, height: 36)
                .background(Circle().fill(.white.opacity(0.12)))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }

    private func caption(_ text: String) -> some View {
        Text(text).font(.system(size: 11, weight: .semibold)).foregroundStyle(.white.opacity(0.5))
    }

    // MARK: actions

    /// With a control selected, sets its action; otherwise adds a control with it
    /// in the free middle of the screen and selects it.
    private func choose(_ action: ControlAction) {
        ControlEditorHistory.shared.record()
        if let id = m.selected, m.controls.contains(where: { $0.id == id }) {
            update(id) {
                $0.action = action
                if action == .mouseLook { $0.padBinding = nil }
            }
            return
        }
        var c = TouchControl()
        c.action = action
        // On the side away from the inspector (docked right with nothing selected),
        // staggered so repeated adds do not stack.
        let n = m.controls.count
        c.nx = 0.38 + Double(n % 3) * 0.06
        c.ny = 0.45 + Double(n % 2) * 0.08
        withAnimation(.snappy(duration: 0.25)) {
            m.controls.append(c)
            m.selected = c.id
        }
    }

    private func duplicate(_ id: UUID) {
        guard var copy = m.controls.first(where: { $0.id == id }) else { return }
        ControlEditorHistory.shared.record()
        copy.id = UUID()
        copy.nx = min(copy.nx + 0.06, 0.97)
        copy.ny = min(copy.ny + 0.06, 0.97)
        withAnimation(.snappy(duration: 0.25)) {
            m.controls.append(copy)
            m.selected = copy.id
        }
    }

    /// Deselect first, so nothing still shows the control, then remove it.
    private func delete(_ id: UUID) {
        ControlEditorHistory.shared.record()
        m.selected = nil
        withAnimation(.snappy(duration: 0.25)) {
            m.controls.removeAll { $0.id == id }
        }
    }
}
