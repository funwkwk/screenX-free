import AppKit
import Carbon.HIToolbox

/// A key combination: a virtual key code plus Carbon modifier flags (cmdKey, optionKey, controlKey, shiftKey).
/// Stored in UserDefaults as "keyCode:modifiers"; an empty string means "no shortcut".
struct Shortcut: Equatable {
    var keyCode: UInt32
    var modifiers: UInt32

    static let defaultSnip = Shortcut(keyCode: UInt32(kVK_ANSI_S), modifiers: UInt32(controlKey | shiftKey))

    init(keyCode: UInt32, modifiers: UInt32) {
        self.keyCode = keyCode
        self.modifiers = modifiers
    }

    init?(storage: String) {
        let parts = storage.split(separator: ":")
        guard parts.count == 2, let k = UInt32(parts[0]), let m = UInt32(parts[1]) else { return nil }
        self.init(keyCode: k, modifiers: m)
    }

    var storage: String { "\(keyCode):\(modifiers)" }

    /// From a key-down event; nil unless it has a modifier (F-keys may go without one) and a non-modifier key.
    init?(event: NSEvent) {
        let f = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        var m: UInt32 = 0
        if f.contains(.command) { m |= UInt32(cmdKey) }
        if f.contains(.option) { m |= UInt32(optionKey) }
        if f.contains(.control) { m |= UInt32(controlKey) }
        if f.contains(.shift) { m |= UInt32(shiftKey) }
        let code = UInt32(event.keyCode)
        let isFunctionKey = Shortcut.functionKeys[Int(code)] != nil
        guard m & UInt32(cmdKey | optionKey | controlKey) != 0 || (isFunctionKey && Int(code) != kVK_Escape) else { return nil }
        self.init(keyCode: code, modifiers: m)
    }

    /// "⌃⇧S", the way macOS menus show shortcuts.
    var display: String {
        var s = ""
        if modifiers & UInt32(controlKey) != 0 { s += "⌃" }
        if modifiers & UInt32(optionKey) != 0 { s += "⌥" }
        if modifiers & UInt32(shiftKey) != 0 { s += "⇧" }
        if modifiers & UInt32(cmdKey) != 0 { s += "⌘" }
        return s + Shortcut.keyName(keyCode)
    }

    private static let functionKeys: [Int: String] = [
        kVK_F1: "F1", kVK_F2: "F2", kVK_F3: "F3", kVK_F4: "F4", kVK_F5: "F5", kVK_F6: "F6", kVK_F7: "F7",
        kVK_F8: "F8", kVK_F9: "F9", kVK_F10: "F10", kVK_F11: "F11", kVK_F12: "F12", kVK_F13: "F13",
        kVK_F14: "F14", kVK_F15: "F15", kVK_F16: "F16", kVK_F17: "F17", kVK_F18: "F18", kVK_F19: "F19", kVK_F20: "F20",
    ]

    private static let specialKeys: [Int: String] = [
        kVK_Space: "Space", kVK_Return: "↩", kVK_Tab: "⇥", kVK_Delete: "⌫", kVK_ForwardDelete: "⌦", kVK_Escape: "⎋",
        kVK_LeftArrow: "←", kVK_RightArrow: "→", kVK_UpArrow: "↑", kVK_DownArrow: "↓",
        kVK_Home: "↖", kVK_End: "↘", kVK_PageUp: "⇞", kVK_PageDown: "⇟",
    ]

    /// The character the key types on the current keyboard layout (so "Z" on a QWERTZ keyboard is right).
    static func keyName(_ code: UInt32) -> String {
        if let n = functionKeys[Int(code)] ?? specialKeys[Int(code)] { return n }
        if let source = TISCopyCurrentKeyboardLayoutInputSource()?.takeRetainedValue(),
           let ptr = TISGetInputSourceProperty(source, kTISPropertyUnicodeKeyLayoutData) {
            let data = Unmanaged<CFData>.fromOpaque(ptr).takeUnretainedValue() as Data
            var dead: UInt32 = 0
            var chars = [UniChar](repeating: 0, count: 4)
            var len = 0
            let status = data.withUnsafeBytes { raw -> OSStatus in
                guard let layout = raw.baseAddress?.assumingMemoryBound(to: UCKeyboardLayout.self) else { return -1 }
                return UCKeyTranslate(layout, UInt16(code), UInt16(kUCKeyActionDisplay), 0, UInt32(LMGetKbdType()),
                                      OptionBits(kUCKeyTranslateNoDeadKeysBit), &dead, chars.count, &len, &chars)
            }
            if status == noErr, len > 0 {
                let s = String(utf16CodeUnits: chars, count: len).uppercased().trimmingCharacters(in: .whitespacesAndNewlines)
                if !s.isEmpty { return s }
            }
        }
        return "Key \(code)"
    }
}

/// System-wide hotkeys through Carbon's RegisterEventHotKey (needs no Accessibility permission).
enum HotKey {
    enum Slot: UInt32, CaseIterable { case snip = 1, record = 2 }

    private static var actions: [UInt32: () -> Void] = [:]
    private static var refs: [UInt32: EventHotKeyRef] = [:]
    private static var installed = false

    private static func install() {
        guard !installed else { return }
        installed = true
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ -> OSStatus in
            var id = EventHotKeyID()
            let status = GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                                           nil, MemoryLayout<EventHotKeyID>.size, nil, &id)
            guard status == noErr else { return status }
            let slot = id.id
            DispatchQueue.main.async { HotKey.actions[slot]?() }
            return noErr
        }, 1, &spec, nil, nil)
    }

    /// Registers `shortcut` for `slot`, replacing what the slot had. nil clears the slot.
    /// Returns false when another app owns the combination (the slot is then empty).
    @discardableResult
    static func register(_ slot: Slot, _ shortcut: Shortcut?, action: @escaping () -> Void) -> Bool {
        install()
        unregister(slot)
        actions[slot.rawValue] = action
        guard let s = shortcut else { return true }
        var ref: EventHotKeyRef?
        let id = EventHotKeyID(signature: OSType(0x53584B31), id: slot.rawValue) // 'SXK1'
        let status = RegisterEventHotKey(s.keyCode, s.modifiers, id, GetApplicationEventTarget(), 0, &ref)
        guard status == noErr, let r = ref else { return false }
        refs[slot.rawValue] = r
        return true
    }

    static func unregister(_ slot: Slot) {
        if let r = refs.removeValue(forKey: slot.rawValue) { UnregisterEventHotKey(r) }
    }

    /// While a shortcut field records a new combination the old ones must not fire.
    static func suspendAll() {
        for slot in Slot.allCases { unregister(slot) }
    }
}
