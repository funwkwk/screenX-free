import SwiftUI
import AppKit
import AVFoundation
import ServiceManagement

/// Settings laid out like Snipping Tool's: Snipping, Screen recording, Permissions, Shortcuts, About.
/// Every value is saved as soon as it changes and read by the app the next time it is used.
struct SettingsView: View {
    @AppStorage(Prefs.Key.autoCopyChanges) private var autoCopyChanges = true
    @AppStorage(Prefs.Key.autoSave) private var autoSave = true
    @AppStorage(Prefs.Key.askToSave) private var askToSave = false
    @AppStorage(Prefs.Key.multipleWindows) private var multipleWindows = false
    @AppStorage(Prefs.Key.addBorder) private var addBorder = false
    @AppStorage(Prefs.Key.borderColor) private var borderColor = "#E81123"
    @AppStorage(Prefs.Key.borderSize) private var borderSize = 2.0
    @AppStorage(Prefs.Key.quickMarkup) private var quickMarkup = true
    @AppStorage(Prefs.Key.snipMode) private var snipMode = SnipMode.rectangle.rawValue
    @AppStorage(Prefs.Key.snipDelay) private var snipDelay = 0
    @AppStorage(Prefs.Key.recordMic) private var recordMic = false
    @AppStorage(Prefs.Key.recordSystemAudio) private var recordSystemAudio = true
    @AppStorage(Prefs.Key.shutterSound) private var shutterSound = true
    @AppStorage(Prefs.Key.recordCountdown) private var recordCountdown = 3
    @AppStorage(Prefs.Key.showCursor) private var showCursor = true
    @AppStorage(Prefs.Key.fps) private var fps = 30
    @AppStorage(Prefs.Key.screenshotFolder) private var screenshotFolder = ""
    @AppStorage(Prefs.Key.videoFolder) private var videoFolder = ""

    @State private var loginStatus = SMAppService.mainApp.status
    @State private var loginError: String?
    @State private var screenAccess = CGPreflightScreenCaptureAccess()
    @State private var micAccess = AVCaptureDevice.authorizationStatus(for: .audio)
    @State private var folderError: String?

    private let borderColors = ["#000000", "#FFFFFF", "#7A7A7A", "#E81123", "#F7630C", "#FFB900", "#16C60C", "#00B7C3", "#0078D4", "#4F4BD9", "#B146C2", "#E3008C"]

    var body: some View {
        Form {
            Section("Snipping") {
                Picker("Default snip mode", selection: $snipMode) {
                    ForEach(SnipMode.allCases, id: \.rawValue) { Text($0.title).tag($0.rawValue) }
                }
                Picker("Snip delay", selection: $snipDelay) {
                    ForEach([0, 3, 5, 10], id: \.self) { Text($0 == 0 ? "No delay" : "\($0) seconds").tag($0) }
                }
                Toggle("Quick markup after a rectangle snip", isOn: $quickMarkup)
                Toggle("Play a sound when taking a snip", isOn: $shutterSound)
                    .onChange(of: shutterSound) { on in if on { Shutter.play() } }
                Toggle("Automatically copy changes", isOn: $autoCopyChanges)
                Toggle("Automatically save original screenshots", isOn: $autoSave)
                Toggle("Ask to save edited screenshots", isOn: $askToSave)
                Toggle("Multiple windows", isOn: $multipleWindows)
                Toggle("Add border to each screenshot", isOn: $addBorder)
                if addBorder {
                    HStack(spacing: 6) {
                        Text("Colour")
                        Spacer()
                        ForEach(borderColors, id: \.self) { hex in
                            Circle()
                                .fill(Color(nsColor: NSColor(hex: hex) ?? .red))
                                .frame(width: 18, height: 18)
                                .overlay(Circle().stroke(Color.primary.opacity(0.25), lineWidth: 0.5))
                                .overlay(Circle().stroke(Color.accentColor, lineWidth: borderColor == hex ? 2 : 0).padding(-3))
                                .onTapGesture { borderColor = hex }
                                .help(hex)
                        }
                        ColorPicker("", selection: customBorderColor, supportsOpacity: false)
                            .labelsHidden()
                            .help("Other colour")
                    }
                    HStack {
                        Text("Thickness")
                        Slider(value: $borderSize, in: 1...20, step: 1)
                        Text("\(Int(borderSize)) px").monospacedDigit().frame(width: 44, alignment: .trailing)
                    }
                }
                folderRow("Screenshots are saved to", path: $screenshotFolder, fallback: "Pictures/screenX")
            }
            Section("Screen recording") {
                Toggle("Record system audio (what's playing on this Mac)", isOn: $recordSystemAudio)
                Toggle("Record microphone", isOn: $recordMic)
                    .onChange(of: recordMic) { on in if on { requestMic() } }
                if recordMic && micAccess == .denied {
                    note("screenX may not use the microphone. Allow it in System Settings to record sound.",
                         button: "Open Settings", url: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
                }
                Picker("Countdown before recording", selection: $recordCountdown) {
                    ForEach([0, 3, 5, 10], id: \.self) { Text($0 == 0 ? "Off" : "\($0) seconds").tag($0) }
                }
                Toggle("Show the mouse pointer in recordings", isOn: $showCursor)
                Picker("Frame rate", selection: $fps) {
                    ForEach([15, 24, 30, 60], id: \.self) { Text("\($0) fps").tag($0) }
                }
                folderRow("Screen recordings are saved to", path: $videoFolder, fallback: "Movies/screenX")
            }
            Section("Permissions") {
                permissionRow("Screen Recording", granted: screenAccess,
                              detail: screenAccess ? "Allowed" : "Needed for snips and recordings",
                              url: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") {
                    CGRequestScreenCaptureAccess()
                }
                permissionRow("Microphone", granted: micAccess == .authorized,
                              detail: micStatusText, url: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone") {
                    requestMic()
                }
            }
            Section("Shortcuts") {
                LabeledContent("New snip") { ShortcutField(slot: .snip, defaultValue: Shortcut.defaultSnip) }
                LabeledContent("Record screen") { ShortcutField(slot: .record, defaultValue: nil) }
                Toggle("Start screenX when you log in", isOn: launchAtLogin)
                if loginStatus == .requiresApproval {
                    note("Turn screenX on under Login Items in System Settings to finish.", button: "Open Login Items") {
                        SMAppService.openSystemSettingsLoginItems()
                    }
                }
                if let e = loginError { Text(e).font(.caption).foregroundColor(.red) }
            }
            Section("About this app") {
                LabeledContent("screenX", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")
                Link("GitHub", destination: URL(string: "https://github.com/funwkwk/screenX-free")!)
            }
        }
        .formStyle(.grouped)
        .frame(minWidth: 560, minHeight: 480)
        .onAppear(perform: refresh)
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in refresh() }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didBecomeKeyNotification)) { _ in refresh() }
        .alert("Folder not writable", isPresented: Binding(get: { folderError != nil }, set: { if !$0 { folderError = nil } })) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(folderError ?? "")
        }
    }

    // =================================================================== state

    /// Permissions and the login item can change in System Settings while this window is open.
    private func refresh() {
        loginStatus = SMAppService.mainApp.status
        screenAccess = CGPreflightScreenCaptureAccess()
        micAccess = AVCaptureDevice.authorizationStatus(for: .audio)
    }

    private var micStatusText: String {
        switch micAccess {
        case .authorized: return "Allowed"
        case .denied, .restricted: return "Not allowed"
        default: return "Asked the first time a recording uses it"
        }
    }

    private func requestMic() {
        guard AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined else { refresh(); return }
        AVCaptureDevice.requestAccess(for: .audio) { _ in DispatchQueue.main.async { refresh() } }
    }

    private var launchAtLogin: Binding<Bool> {
        Binding(get: { loginStatus == .enabled || loginStatus == .requiresApproval }, set: { on in
            loginError = nil
            do {
                if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            } catch {
                loginError = "Could not change the login item: \(error.localizedDescription)"
            }
            loginStatus = SMAppService.mainApp.status
        })
    }

    private var customBorderColor: Binding<Color> {
        Binding(get: { Color(nsColor: NSColor(hex: borderColor) ?? .red) },
                set: { borderColor = NSColor($0).hexString })
    }

    // =================================================================== rows

    private func note(_ text: String, button: String, url: String? = nil, action: (() -> Void)? = nil) -> some View {
        HStack {
            Text(text).font(.caption).foregroundColor(.secondary)
            Spacer()
            Button(button) {
                if let a = action { a() } else if let u = url.flatMap(URL.init(string:)) { NSWorkspace.shared.open(u) }
            }
        }
    }

    private func permissionRow(_ title: String, granted: Bool, detail: String, url: String, request: @escaping () -> Void) -> some View {
        HStack {
            Image(systemName: granted ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .foregroundColor(granted ? .green : .orange)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text(detail).font(.caption).foregroundColor(.secondary)
            }
            Spacer()
            if !granted {
                Button("Allow…") {
                    request()
                    if let u = URL(string: url) { NSWorkspace.shared.open(u) }
                }
            }
        }
    }

    private func folderRow(_ title: String, path: Binding<String>, fallback: String) -> some View {
        let url = URL(fileURLWithPath: path.wrappedValue.isEmpty
            ? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(fallback).path : path.wrappedValue)
        return HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text((url.path as NSString).abbreviatingWithTildeInPath)
                    .font(.caption).foregroundColor(.secondary).lineLimit(1).truncationMode(.middle)
            }
            Spacer()
            Button {
                try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
                NSWorkspace.shared.open(url)
            } label: {
                Image(systemName: "folder")
            }
            .help("Show in Finder")
            Button("Change…") {
                let panel = NSOpenPanel()
                panel.canChooseDirectories = true
                panel.canChooseFiles = false
                panel.canCreateDirectories = true
                panel.prompt = "Choose"
                panel.directoryURL = url
                guard panel.runModal() == .OK, let chosen = panel.url else { return }
                if FileManager.default.isWritableFile(atPath: chosen.path) {
                    path.wrappedValue = chosen.path
                } else {
                    folderError = "screenX cannot save files in \(chosen.lastPathComponent). Choose another folder."
                }
            }
        }
    }
}

/// Click, then press the new combination (Esc cancels). Saved and registered at once; when another app owns
/// the combination the old one stays and a note says so.
struct ShortcutField: View {
    let slot: HotKey.Slot
    let defaultValue: Shortcut?

    @State private var current: Shortcut?
    @State private var recording = false

    init(slot: HotKey.Slot, defaultValue: Shortcut?) {
        self.slot = slot
        self.defaultValue = defaultValue
        _current = State(initialValue: slot == .snip ? Prefs.snipShortcut : Prefs.recordShortcut)
    }
    @State private var monitor: Any?
    @State private var problem: String?

    var body: some View {
        VStack(alignment: .trailing, spacing: 4) {
            HStack(spacing: 6) {
                Button(action: { recording ? stop() : start() }) {
                    Text(recording ? "Press a shortcut…" : current?.display ?? "None")
                        .foregroundColor(recording ? .accentColor : (current == nil ? .secondary : .primary))
                        .frame(minWidth: 130)
                }
                .help(recording ? "Press the new key combination, or Esc to cancel" : "Click to record a new shortcut")
                if current != defaultValue {
                    Button("Reset") { apply(defaultValue) }.help("Back to \(defaultValue?.display ?? "no shortcut")")
                }
                if current != nil && defaultValue == nil {
                    Button("Clear") { apply(nil) }
                }
            }
            if let p = problem {
                Text(p).font(.caption).foregroundColor(.orange)
            } else if AppDelegate.shared.takenShortcuts.contains(slot), let c = current {
                Text("\(c.display) is used by another app").font(.caption).foregroundColor(.orange)
            }
        }
        .onDisappear { if recording { stop() } }
    }

    private func start() {
        problem = nil
        HotKey.suspendAll() // the old combinations must not fire while recording
        recording = true
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { e in
            let mods = e.modifierFlags.intersection([.command, .option, .control, .shift])
            if e.keyCode == 53 && mods.isEmpty { stop(); return nil } // Esc
            if let sc = Shortcut(event: e) { apply(sc) } else { NSSound.beep() }
            return nil
        }
    }

    private func stop() {
        if let m = monitor { NSEvent.removeMonitor(m) }
        monitor = nil
        recording = false
        AppDelegate.shared.registerShortcuts()
    }

    private func apply(_ sc: Shortcut?) {
        stop()
        problem = nil
        let other: HotKey.Slot = slot == .snip ? .record : .snip
        let otherShortcut = other == .snip ? Prefs.snipShortcut : Prefs.recordShortcut
        if let sc = sc, sc == otherShortcut {
            problem = "\(sc.display) is already the \(other == .snip ? "New snip" : "Record screen") shortcut"
            return
        }
        if AppDelegate.shared.setShortcut(slot, sc) {
            current = sc
        } else if let sc = sc {
            problem = "\(sc.display) is used by another app"
        }
    }
}
