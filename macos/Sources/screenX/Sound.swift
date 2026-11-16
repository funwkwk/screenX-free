import AppKit

/// The camera-shutter click played when a snip is taken (shutter.wav in the app bundle, shared with the
/// Windows and Linux builds). Loaded once so it plays without delay.
enum Shutter {
    private static let sound: NSSound? = {
        guard let url = Bundle.main.url(forResource: "shutter", withExtension: "wav") else {
            return NSSound(named: "Grab") ?? NSSound(named: "Tink")
        }
        return NSSound(contentsOf: url, byReference: false)
    }()

    static func play() {
        guard Prefs.shutterSound, let s = sound else { return }
        s.stop()
        s.play()
    }
}
