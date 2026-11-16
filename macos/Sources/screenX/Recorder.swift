import AppKit
import AVFoundation

/// Borderless windows cannot become key by default; these need the keyboard (Esc / Return).
final class KeyWindow: NSWindow {
    override var canBecomeKey: Bool { true }
}

/// Full-screen layer for the recording area. Before recording the screen is dimmed outside the area, which can
/// be moved (drag inside), resized from its corners / edges or redrawn (drag outside). While recording only a
/// dashed red frame is shown.
final class RegionView: NSView {
    var region: NSRect
    var recording = false { didSet { needsDisplay = true; window?.invalidateCursorRects(for: self) } }
    /// During the countdown the area stays as it is.
    var locked = false
    var paused = false { didSet { needsDisplay = true } }
    var onChange: (() -> Void)?

    private enum Drag { case none, new, move, resize }
    private var drag = Drag.none
    private var edges: (l: Bool, r: Bool, t: Bool, b: Bool) = (false, false, false, false)
    private var startPoint = NSPoint.zero
    private var startRegion = NSRect.zero
    private let minSize: CGFloat = 40

    init(frame: NSRect, region: NSRect) {
        self.region = region
        super.init(frame: frame)
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    // the layer is never key, so the first click must already start a drag
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        if recording {
            (paused ? NSColor.systemOrange : NSColor.systemRed).setStroke()
            let p = NSBezierPath(rect: region.insetBy(dx: -1.5, dy: -1.5))
            p.lineWidth = 1.5
            p.setLineDash([3, 3], count: 2, phase: 0)
            p.stroke()
            return
        }
        NSColor(white: 0, alpha: 0.55).setFill()
        let dim = NSBezierPath(rect: bounds)
        dim.appendRect(region)
        dim.windingRule = .evenOdd
        dim.fill()
        // macOS passes clicks on fully transparent pixels through to the app below, so the area gets an
        // invisible tint: without it dragging inside the area (move) never reached this view.
        // The layer is excluded from capture (sharingType = .none), so this never shows in the video.
        NSColor(white: 0, alpha: 0.01).setFill()
        NSBezierPath(rect: region.insetBy(dx: -12, dy: -12)).fill()

        NSColor.white.setStroke()
        let frame = NSBezierPath(rect: region)
        frame.lineWidth = 1.5
        frame.setLineDash([5, 4], count: 2, phase: 0)
        frame.stroke()

        // corner brackets and edge bars
        let c = NSBezierPath()
        c.lineWidth = 5
        c.lineCapStyle = .round
        c.lineJoinStyle = .round
        let k: CGFloat = min(18, region.width / 3, region.height / 3)
        for (x, sx) in [(region.minX, CGFloat(1)), (region.maxX, CGFloat(-1))] {
            for (y, sy) in [(region.minY, CGFloat(1)), (region.maxY, CGFloat(-1))] {
                c.move(to: NSPoint(x: x, y: y + k * sy))
                c.line(to: NSPoint(x: x, y: y))
                c.line(to: NSPoint(x: x + k * sx, y: y))
            }
        }
        if region.height > 90 {
            for x in [region.minX, region.maxX] {
                c.move(to: NSPoint(x: x, y: region.midY - 16))
                c.line(to: NSPoint(x: x, y: region.midY + 16))
            }
        }
        if region.width > 90 {
            for y in [region.minY, region.maxY] {
                c.move(to: NSPoint(x: region.midX - 16, y: y))
                c.line(to: NSPoint(x: region.midX + 16, y: y))
            }
        }
        c.stroke()
        drawSizeLabel()
    }

    /// "1920 × 1080" (pixels) in the top-left corner of the area.
    private func drawSizeLabel() {
        let k = window?.backingScaleFactor ?? 2
        let text = "\(Int((region.width * k).rounded())) × \(Int((region.height * k).rounded()))"
        let attr = NSAttributedString(string: text, attributes: [
            .foregroundColor: NSColor.white, .font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .medium),
        ])
        let s = attr.size()
        let box = NSRect(x: region.minX + 8, y: region.maxY - s.height - 18, width: s.width + 16, height: s.height + 8)
        guard box.maxX < region.maxX - 4, box.minY > region.minY + 4 else { return }
        NSColor(white: 0.08, alpha: 0.72).setFill()
        NSBezierPath(roundedRect: box, xRadius: box.height / 2, yRadius: box.height / 2).fill()
        attr.draw(at: NSPoint(x: box.minX + 8, y: box.minY + 4))
    }

    // ---- cursor

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        for a in trackingAreas { removeTrackingArea(a) }
        addTrackingArea(NSTrackingArea(rect: bounds, options: [.mouseMoved, .cursorUpdate, .activeAlways, .inVisibleRect], owner: self, userInfo: nil))
    }

    /// Which edges are under `p` (`t` = the top edge on screen, region.maxY).
    private func edgeHit(_ p: NSPoint) -> (l: Bool, r: Bool, t: Bool, b: Bool)? {
        let s: CGFloat = 12
        let nearL = abs(p.x - region.minX) <= s, nearR = abs(p.x - region.maxX) <= s
        let nearB = abs(p.y - region.minY) <= s, nearT = abs(p.y - region.maxY) <= s
        guard region.insetBy(dx: -s, dy: -s).contains(p), nearL || nearR || nearT || nearB else { return nil }
        return (nearL, nearR && !nearL, nearT, nearB && !nearT)
    }

    private func cursor(at p: NSPoint) -> NSCursor {
        if recording || locked { return .arrow }
        switch drag {
        case .move: return .closedHand
        case .new: return .crosshair
        case .resize: return ResizeCursor.make(left: edges.l, right: edges.r, top: edges.t, bottom: edges.b)
        case .none: break
        }
        if let e = edgeHit(p) { return ResizeCursor.make(left: e.l, right: e.r, top: e.t, bottom: e.b) }
        return region.contains(p) ? .openHand : .crosshair
    }

    override func cursorUpdate(with event: NSEvent) { cursor(at: convert(event.locationInWindow, from: nil)).set() }
    override func mouseMoved(with event: NSEvent) { cursor(at: convert(event.locationInWindow, from: nil)).set() }

    // ---- mouse

    override func mouseDown(with event: NSEvent) {
        guard !recording, !locked else { return }
        let p = convert(event.locationInWindow, from: nil)
        startPoint = p
        startRegion = region
        if let e = edgeHit(p) {
            drag = .resize
            edges = e
        } else if region.contains(p) {
            drag = .move
        } else {
            drag = .new
        }
        cursor(at: p).set()
    }

    override func mouseDragged(with event: NSEvent) {
        guard drag != .none else { return }
        var p = convert(event.locationInWindow, from: nil)
        p.x = min(max(p.x, 0), bounds.width)
        p.y = min(max(p.y, 0), bounds.height)
        switch drag {
        case .new:
            region = NSRect(x: min(p.x, startPoint.x), y: min(p.y, startPoint.y), width: abs(p.x - startPoint.x), height: abs(p.y - startPoint.y))
        case .move:
            var r = startRegion.offsetBy(dx: p.x - startPoint.x, dy: p.y - startPoint.y)
            r.origin.x = min(max(r.origin.x, 0), bounds.width - r.width)
            r.origin.y = min(max(r.origin.y, 0), bounds.height - r.height)
            region = r
        case .resize:
            // the dragged edge stops short of the opposite one, so the area never flips or collapses
            var l = startRegion.minX, r = startRegion.maxX, b = startRegion.minY, t = startRegion.maxY
            if edges.l { l = min(p.x, r - minSize) }
            if edges.r { r = max(p.x, l + minSize) }
            if edges.t { t = max(p.y, b + minSize) }
            if edges.b { b = min(p.y, t - minSize) }
            region = NSRect(x: l, y: b, width: r - l, height: t - b)
        case .none:
            break
        }
        needsDisplay = true
        onChange?()
    }

    override func mouseUp(with event: NSEvent) {
        if drag == .new && (region.width < minSize || region.height < minSize) { region = startRegion }
        region = region.integral
        drag = .none
        cursor(at: convert(event.locationInWindow, from: nil)).set()
        needsDisplay = true
        onChange?()
    }
}

/// Region layer + floating control bar + RecordingEngine (ScreenCaptureKit).
/// Ready:      [● Start] 00:00:00 [system audio] [mic] [✕]
/// Recording:  [❚❚] [■] 00:00:09 [system audio] [mic] [trash]
@MainActor
final class Recorder: NSObject {
    static var current: Recorder?

    private let screen: NSScreen
    private let layer: KeyWindow
    private let view: RegionView
    private let bar: KeyWindow
    private let row = NSStackView()
    private let timeLabel = NSTextField(labelWithString: "00:00:00")
    private var micButton: NSButton!
    private var audioButton: NSButton!
    private var pauseButton: NSButton?
    private var engine: RecordingEngine?
    private var starting = false
    private var countdownTimer: Timer?
    private var countdownLeft = 0
    private var badge: CountdownBadge?
    private var counting: Bool { countdownTimer != nil }
    private var timer: Timer?
    private var discard = false
    // elapsed time without the paused stretches
    private var startedAt = Date()
    private var pausedAt: Date?
    private var pausedTotal: TimeInterval = 0
    private var keyMonitor: Any?
    private var backToSnip = false

    static func start(backToSnip: Bool = false) {
        if let r = current {
            r.bar.makeKeyAndOrderFront(nil)
            return
        }
        let mouse = NSEvent.mouseLocation
        guard let screen = NSScreen.screens.first(where: { NSMouseInRect(mouse, $0.frame, false) }) ?? NSScreen.main else { return }
        let r = Recorder(screen: screen)
        r.backToSnip = backToSnip
        current = r
        r.show()
    }

    private init(screen: NSScreen) {
        self.screen = screen
        let f = screen.frame
        let size = NSSize(width: (f.width * 0.6).rounded(), height: (f.height * 0.6).rounded())
        let region = NSRect(x: ((f.width - size.width) / 2).rounded(), y: ((f.height - size.height) / 2).rounded(), width: size.width, height: size.height)
        view = RegionView(frame: NSRect(origin: .zero, size: f.size), region: region)
        layer = KeyWindow(contentRect: f, styleMask: .borderless, backing: .buffered, defer: false)
        bar = KeyWindow(contentRect: NSRect(x: 0, y: 0, width: 360, height: 52), styleMask: .borderless, backing: .buffered, defer: false)
        super.init()

        layer.isOpaque = false
        layer.backgroundColor = .clear
        layer.hasShadow = false
        layer.level = .statusBar
        layer.sharingType = .none
        layer.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        layer.isReleasedWhenClosed = false
        layer.contentView = view
        layer.acceptsMouseMovedEvents = true
        view.onChange = { [weak self] in self?.placeBar() }

        bar.isOpaque = false
        bar.backgroundColor = .clear
        bar.hasShadow = true
        bar.level = NSWindow.Level(rawValue: NSWindow.Level.statusBar.rawValue + 1)
        bar.sharingType = .none
        bar.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        bar.isReleasedWhenClosed = false
        bar.isMovableByWindowBackground = true
        bar.appearance = NSAppearance(named: .darkAqua)
        let card = NSView()
        card.wantsLayer = true
        card.layer?.backgroundColor = NSColor(white: 0.17, alpha: 0.97).cgColor
        card.layer?.cornerRadius = 10
        card.layer?.borderColor = NSColor(white: 1, alpha: 0.2).cgColor
        card.layer?.borderWidth = 1
        bar.contentView = card
        row.spacing = 8
        row.edgeInsets = NSEdgeInsets(top: 8, left: 10, bottom: 8, right: 10)
        row.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(row)
        NSLayoutConstraint.activate([
            row.topAnchor.constraint(equalTo: card.topAnchor), row.bottomAnchor.constraint(equalTo: card.bottomAnchor),
            row.leadingAnchor.constraint(equalTo: card.leadingAnchor), row.trailingAnchor.constraint(equalTo: card.trailingAnchor),
        ])
        timeLabel.font = .monospacedDigitSystemFont(ofSize: 15, weight: .regular)
        timeLabel.textColor = .white
        micButton = icon("mic.slash", "Microphone", #selector(toggleMic))
        audioButton = icon("speaker.slash", "System audio", #selector(toggleSystemAudio))
        showIdle()
    }

    private func icon(_ symbol: String, _ tip: String, _ action: Selector, tint: NSColor? = nil) -> NSButton {
        let img = NSImage(systemSymbolName: symbol, accessibilityDescription: tip) ?? NSImage(size: NSSize(width: 16, height: 16))
        let b = NSButton(image: img, target: self, action: action)
        b.isBordered = false
        b.toolTip = tip
        b.setAccessibilityLabel(tip)
        b.contentTintColor = tint ?? .white
        b.widthAnchor.constraint(equalToConstant: 34).isActive = true
        b.heightAnchor.constraint(equalToConstant: 30).isActive = true
        return b
    }

    private func setRow(_ views: [NSView]) {
        for v in row.arrangedSubviews {
            row.removeArrangedSubview(v)
            v.removeFromSuperview()
        }
        for v in views { row.addArrangedSubview(v) }
        bar.setContentSize(row.fittingSize)
        placeBar()
    }

    private func refreshMic() {
        let on = Prefs.recordMic
        micButton.image = NSImage(systemSymbolName: on ? "mic" : "mic.slash", accessibilityDescription: "Microphone")
        micButton.contentTintColor = on ? .white : NSColor(white: 0.55, alpha: 1)
        micButton.toolTip = on ? "Microphone on" : "Microphone off"
        let sys = Prefs.recordSystemAudio
        audioButton.image = NSImage(systemSymbolName: sys ? "speaker.wave.2" : "speaker.slash", accessibilityDescription: "System audio")
        audioButton.contentTintColor = sys ? .white : NSColor(white: 0.55, alpha: 1)
        audioButton.toolTip = sys ? "System audio on" : "System audio off"
    }

    private func showIdle() {
        let start = NSButton(title: "Start", target: self, action: #selector(startClicked))
        start.image = NSImage(systemSymbolName: "record.circle", accessibilityDescription: "Start")
        start.imagePosition = .imageLeading
        start.bezelStyle = .rounded
        start.bezelColor = NSColor(hex: "#4CC2FF")
        start.keyEquivalent = "\r"
        start.toolTip = "Start recording (Return)"
        timeLabel.stringValue = "00:00:00"
        micButton.isEnabled = true
        audioButton.isEnabled = true
        refreshMic()
        setRow([start, timeLabel, audioButton, micButton, icon("xmark", "Close (Esc)", #selector(cancel))])
    }

    private func showRecording() {
        let pause = icon("pause.fill", "Pause", #selector(togglePause))
        pauseButton = pause
        micButton.isEnabled = false // the sources are fixed once recording started
        audioButton.isEnabled = false
        setRow([pause, icon("stop.fill", "Stop and save", #selector(stopAndSave), tint: .systemRed), timeLabel, audioButton, micButton,
                icon("trash", "Discard recording", #selector(stopAndDiscard))])
    }

    private func show() {
        layer.setFrame(screen.frame, display: true)
        layer.orderFrontRegardless()
        NSApp.activate(ignoringOtherApps: true)
        bar.makeKeyAndOrderFront(nil)
        placeBar()
        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] e in
            guard let self = self, self.engine == nil else { return e }
            if e.keyCode == 53 { // Esc: stops the countdown, otherwise closes
                if self.counting { self.cancelCountdown() } else { self.cancel() }
                return nil
            }
            if self.counting && (e.keyCode == 36 || e.keyCode == 76) { return nil } // Return does nothing while counting
            return e
        }
    }

    /// Centre the bar above the area (below or inside it when there is no room).
    private func placeBar() {
        // Below the area, like the macOS screenshot bar; above it only when there is no room below, and never
        // under the menu bar / camera notch. Inside the area's bottom edge when it fills the screen.
        let f = screen.frame, r = view.region, size = bar.frame.size
        let notch = max(screen.safeAreaInsets.top, f.maxY - screen.visibleFrame.maxY)
        let top = f.maxY - notch - 8, bottom = f.minY + 8
        var x = f.minX + r.midX - size.width / 2
        var y = f.minY + r.minY - size.height - 14
        if y < bottom {
            y = f.minY + r.maxY + 14
            if y + size.height > top { y = f.minY + r.minY + 14 }
        }
        y = min(max(y, bottom), top - size.height)
        x = min(max(x, f.minX + 8), f.maxX - size.width - 8)
        bar.setFrameOrigin(NSPoint(x: x.rounded(), y: y.rounded()))
    }

    // =================================================================== actions

    @objc private func toggleMic() {
        Prefs.recordMic.toggle()
        refreshMic()
    }

    @objc private func toggleSystemAudio() {
        Prefs.recordSystemAudio.toggle()
        refreshMic()
    }

    @objc private func cancel() {
        if engine != nil { stopAndDiscard(); return }
        close()
        if backToSnip { AppDelegate.shared.snip(Prefs.snipMode) }
    }

    @objc private func startClicked() {
        guard engine == nil, !starting, !counting else { return }
        if !CGPreflightScreenCaptureAccess() {
            CGRequestScreenCaptureAccess()
            Toast.show("Allow screenX under System Settings > Privacy & Security > Screen Recording, then press Start again.")
            return
        }
        if Prefs.recordMic {
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                DispatchQueue.main.async { self.countdown { self.begin(withMic: granted) } }
            }
        } else {
            countdown { self.begin(withMic: false) }
        }
    }

    // =================================================================== countdown

    /// "3, 2, 1" in the middle of the area, then `then`. Esc / ✕ goes back to the ready bar.
    private func countdown(then: @escaping () -> Void) {
        let total = Prefs.recordCountdown
        guard total > 0, Recorder.current === self, engine == nil else { then(); return }
        countdownLeft = total
        view.locked = true
        view.window?.invalidateCursorRects(for: view)
        let b = CountdownBadge(frame: NSRect(x: 0, y: 0, width: 150, height: 150))
        b.frame.origin = NSPoint(x: (view.region.midX - 75).rounded(), y: (view.region.midY - 75).rounded())
        view.addSubview(b)
        badge = b
        setRow([timeLabel, icon("xmark", "Cancel (Esc)", #selector(cancelCountdown))])
        showCount()
        countdownTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            DispatchQueue.main.async {
                guard let self = self, self.counting else { return }
                self.countdownLeft -= 1
                if self.countdownLeft > 0 {
                    self.showCount()
                    return
                }
                self.countdownTimer?.invalidate()
                self.countdownTimer = nil
                self.badge?.removeFromSuperview()
                self.badge = nil
                self.view.display() // the badge is gone from the screen before capture starts
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                    guard Recorder.current === self else { return }
                    then()
                    // starting failed: back to the ready bar
                    if self.engine == nil && !self.starting { self.view.locked = false; self.showIdle() }
                }
            }
        }
    }

    /// `screenX --countdowntest <png>`: opens the recorder, starts the countdown (without recording) and
    /// writes what the area layer shows half a second in.
    static func countdownSnapshot(_ url: URL) {
        start()
        guard let r = current else { NSApp.terminate(nil); return }
        r.countdown { }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            let v = r.view
            let k = r.screen.backingScaleFactor
            let w = Int(v.bounds.width * k), h = Int(v.bounds.height * k)
            if let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                   space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue),
               let layer = v.layer {
                ctx.setFillColor(NSColor.darkGray.cgColor)
                ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
                ctx.scaleBy(x: k, y: k)
                layer.render(in: ctx)
                if let img = ctx.makeImage() {
                    let rep = NSBitmapImageRep(cgImage: img)
                    try? rep.representation(using: .png, properties: [:])?.write(to: url)
                }
            }
            r.cancelCountdown()
            NSApp.terminate(nil)
        }
    }

    private func showCount() {
        timeLabel.stringValue = "Starting in \(countdownLeft)…"
        bar.setContentSize(row.fittingSize)
        placeBar()
        badge?.show(countdownLeft)
    }

    @objc private func cancelCountdown() {
        countdownTimer?.invalidate()
        countdownTimer = nil
        badge?.removeFromSuperview()
        badge = nil
        view.locked = false
        view.window?.invalidateCursorRects(for: view)
        showIdle()
    }

    private func begin(withMic: Bool) {
        guard engine == nil, !starting, Recorder.current === self else { return }
        guard let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber else {
            Toast.show("Could not find the display to record.")
            return
        }
        // even pixel sizes keep the H.264 encoder happy; ScreenCaptureKit wants a top-left origin
        let r = view.region.integral
        let rect = CGRect(x: r.minX, y: screen.frame.height - r.maxY, width: r.width, height: r.height)
        let url = Prefs.uniqueFile(in: Prefs.videoFolder, prefix: "Screen Recording", ext: "mov")
        let e = RecordingEngine(url: url)
        let options = RecordingEngine.Options(displayID: CGDirectDisplayID(number.uint32Value), rect: rect,
                                              scale: screen.backingScaleFactor, fps: Prefs.fps, cursor: Prefs.showCursor,
                                              systemAudio: Prefs.recordSystemAudio, mic: withMic)
        e.onStreamError = { [weak self] error in
            guard let self = self, self.engine === e else { return }
            Toast.show("Recording stopped: \(error.localizedDescription)")
            self.stopAndSave()
        }
        starting = true
        Task { @MainActor in
            do {
                try await e.start(options)
            } catch {
                self.starting = false
                self.view.locked = false
                self.showIdle()
                Toast.show("Could not start recording: \(error.localizedDescription)")
                return
            }
            self.starting = false
            guard Recorder.current === self else { e.cancel(); return }
            if Prefs.recordMic && !e.micUsed {
                Toast.show("Recording without the microphone (no access or no device).")
            }
            self.engine = e
            self.discard = false
            self.startedAt = Date()
            self.pausedAt = nil
            self.pausedTotal = 0
            self.view.recording = true
            self.layer.ignoresMouseEvents = true
            self.showRecording()
            self.timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
                DispatchQueue.main.async { self?.tick() }
            }
        }
    }

    private func tick() {
        let now = pausedAt ?? Date()
        let secs = max(0, Int(now.timeIntervalSince(startedAt) - pausedTotal))
        timeLabel.stringValue = String(format: "%02d:%02d:%02d", secs / 3600, (secs / 60) % 60, secs % 60)
    }

    @objc private func togglePause() {
        guard let e = engine else { return }
        let paused = !view.paused
        e.setPaused(paused)
        if paused {
            pausedAt = Date()
        } else if let p = pausedAt {
            pausedTotal += Date().timeIntervalSince(p)
            pausedAt = nil
        }
        view.paused = paused
        pauseButton?.image = NSImage(systemSymbolName: paused ? "play.fill" : "pause.fill", accessibilityDescription: paused ? "Resume" : "Pause")
        pauseButton?.toolTip = paused ? "Resume" : "Pause"
    }

    @objc func stopAndSave() {
        discard = false
        finish()
    }

    @objc private func stopAndDiscard() {
        discard = true
        finish()
    }

    private func finish() {
        guard let e = engine else { close(); return }
        timer?.invalidate()
        timeLabel.stringValue = "Saving…"
        pauseButton?.isEnabled = false
        let discard = self.discard
        e.onStreamError = nil
        e.stop { [weak self] error in
            self?.close()
            let url = e.url
            if discard {
                try? FileManager.default.removeItem(at: url)
            } else if let error = error {
                try? FileManager.default.removeItem(at: url)
                Toast.show("Recording failed: \(error.localizedDescription)")
            } else {
                let pb = NSPasteboard.general
                pb.clearContents()
                pb.writeObjects([url as NSURL])
                Toast.show("Screen recording saved: \(url.lastPathComponent)")
                NSWorkspace.shared.activateFileViewerSelecting([url])
            }
        }
    }

    private func close() {
        timer?.invalidate()
        countdownTimer?.invalidate()
        countdownTimer = nil
        if let m = keyMonitor { NSEvent.removeMonitor(m) }
        keyMonitor = nil
        engine = nil
        layer.orderOut(nil)
        bar.orderOut(nil)
        if Recorder.current === self { Recorder.current = nil }
    }
}

/// The big "3 / 2 / 1" of the countdown: a dark translucent disc with a white number. Each number scales and
/// fades in, then fades out just before the next one.
final class CountdownBadge: NSView {
    private let disc = CALayer()
    private let number = CATextLayer()
    private var serial = 0

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.masksToBounds = false
        disc.frame = bounds
        disc.cornerRadius = bounds.width / 2
        disc.backgroundColor = NSColor(white: 0, alpha: 0.6).cgColor
        disc.borderColor = NSColor(white: 1, alpha: 0.2).cgColor
        disc.borderWidth = 1
        disc.opacity = 0
        let font = NSFont.systemFont(ofSize: 72, weight: .semibold)
        number.font = font
        number.fontSize = 72
        number.foregroundColor = NSColor.white.cgColor
        number.alignmentMode = .center
        number.contentsScale = NSScreen.main?.backingScaleFactor ?? 2
        let h = font.ascender - font.descender
        number.frame = NSRect(x: 0, y: (bounds.height - h) / 2, width: bounds.width, height: h)
        disc.addSublayer(number)
        layer?.addSublayer(disc)
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    func show(_ n: Int) {
        serial += 1
        let mine = serial
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        number.string = "\(n)"
        disc.opacity = 1
        disc.transform = CATransform3DIdentity
        CATransaction.commit()

        let scale = CABasicAnimation(keyPath: "transform.scale")
        scale.fromValue = 1.3
        scale.toValue = 1.0
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = 0
        fade.toValue = 1
        let g = CAAnimationGroup()
        g.animations = [scale, fade]
        g.duration = 0.25
        g.timingFunction = CAMediaTimingFunction(controlPoints: 0.22, 1, 0.36, 1) // ease-out
        disc.add(g, forKey: "in")

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.85) { [weak self] in
            guard let self = self, self.serial == mine else { return }
            let out = CABasicAnimation(keyPath: "opacity")
            out.fromValue = 1
            out.toValue = 0
            out.duration = 0.15
            self.disc.opacity = 0
            self.disc.add(out, forKey: "out")
        }
    }
}
