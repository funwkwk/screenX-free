import AppKit
import AVFoundation
import ScreenCaptureKit

/// Records an area of one display with ScreenCaptureKit into a .mov (H.264 + AAC):
/// video and system audio come from an SCStream, the microphone from an AVCaptureSession. Everything is
/// written by one AVAssetWriter on one serial queue. All sample times are on the host clock, so pausing
/// simply drops samples and shifts later ones back by the paused time.
/// With both system audio and microphone on, the file is written with two audio tracks and then mixed into
/// one (video copied as is), so every player hears both.
final class RecordingEngine: NSObject, SCStreamOutput, SCStreamDelegate, AVCaptureAudioDataOutputSampleBufferDelegate, @unchecked Sendable {
    struct Options {
        var displayID: CGDirectDisplayID
        var rect: CGRect          // points, top-left origin, relative to the display
        var scale: CGFloat
        var fps: Int
        var cursor: Bool
        var systemAudio: Bool
        var mic: Bool
    }

    enum Failure: LocalizedError {
        case noDisplay, nothingRecorded, writer(String)
        var errorDescription: String? {
            switch self {
            case .noDisplay: return "Could not find the display to record."
            case .nothingRecorded: return "Nothing was recorded."
            case .writer(let s): return s
            }
        }
    }

    /// Called on the main queue when the stream stops by itself (display gone, permission revoked).
    var onStreamError: ((Error) -> Void)?
    private(set) var micUsed = false

    let url: URL
    private let q = DispatchQueue(label: "screenX.recording")
    private var stream: SCStream?
    private var micSession: AVCaptureSession?
    private var writer: AVAssetWriter?
    private var videoIn: AVAssetWriterInput?
    private var systemIn: AVAssetWriterInput?
    private var micIn: AVAssetWriterInput?
    private var started = false
    private var lastVideo = CMTime.invalid
    // pause bookkeeping, host-clock times (touched on `q` only)
    private var paused = false
    private var pausedAt = CMTime.invalid
    private var offset = CMTime.zero
    private var finishing = false

    init(url: URL) {
        self.url = url
    }

    // =================================================================== start

    func start(_ o: Options) async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first(where: { $0.displayID == o.displayID }) else { throw Failure.noDisplay }
        // leave out screenX's own windows (record bar, frame, toasts)
        let me = content.applications.filter { $0.processID == ProcessInfo.processInfo.processIdentifier }
        let filter = SCContentFilter(display: display, excludingApplications: me, exceptingWindows: [])

        let w = Int(o.rect.width * o.scale) & ~1, h = Int(o.rect.height * o.scale) & ~1
        let cfg = SCStreamConfiguration()
        cfg.sourceRect = o.rect
        cfg.width = max(32, w)
        cfg.height = max(32, h)
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(max(1, o.fps)))
        cfg.showsCursor = o.cursor
        cfg.pixelFormat = kCVPixelFormatType_32BGRA
        cfg.queueDepth = 6
        cfg.capturesAudio = o.systemAudio
        cfg.excludesCurrentProcessAudio = true // the shutter / beeps of screenX itself stay out
        cfg.sampleRate = 48000
        cfg.channelCount = 2

        let writer = try AVAssetWriter(outputURL: url, fileType: .mov)
        let video = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: cfg.width,
            AVVideoHeightKey: cfg.height,
            AVVideoCompressionPropertiesKey: [
                AVVideoAverageBitRateKey: max(2_000_000, cfg.width * cfg.height * 4),
                AVVideoExpectedSourceFrameRateKey: o.fps,
                AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
            ],
        ])
        video.expectsMediaDataInRealTime = true
        guard writer.canAdd(video) else { throw Failure.writer("Could not set up the video encoder.") }
        writer.add(video)
        videoIn = video
        if o.systemAudio {
            let a = Self.aacInput(channels: 2)
            if writer.canAdd(a) { writer.add(a); systemIn = a }
        }
        if o.mic, let session = makeMicSession() {
            let a = Self.aacInput(channels: 1)
            if writer.canAdd(a) {
                writer.add(a)
                micIn = a
                micSession = session
                micUsed = true
            }
        }
        self.writer = writer

        let s = SCStream(filter: filter, configuration: cfg, delegate: self)
        try s.addStreamOutput(self, type: .screen, sampleHandlerQueue: q)
        if o.systemAudio { try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: q) }
        stream = s
        micSession?.startRunning()
        try await s.startCapture()
    }

    private static func aacInput(channels: Int) -> AVAssetWriterInput {
        let a = AVAssetWriterInput(mediaType: .audio, outputSettings: [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 48000,
            AVNumberOfChannelsKey: channels,
            AVEncoderBitRateKey: channels == 1 ? 96_000 : 160_000,
        ])
        a.expectsMediaDataInRealTime = true
        return a
    }

    private func makeMicSession() -> AVCaptureSession? {
        guard let device = AVCaptureDevice.default(for: .audio), let input = try? AVCaptureDeviceInput(device: device) else { return nil }
        let s = AVCaptureSession()
        let out = AVCaptureAudioDataOutput()
        // 48 kHz mono float, so the encoder never has to guess the layout of odd USB microphones
        out.audioSettings = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: 48000,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 32,
            AVLinearPCMIsFloatKey: true,
            AVLinearPCMIsNonInterleaved: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        out.setSampleBufferDelegate(self, queue: q)
        guard s.canAddInput(input), s.canAddOutput(out) else { return nil }
        s.addInput(input)
        s.addOutput(out)
        return s
    }

    // =================================================================== samples (on `q`)

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard sb.isValid else { return }
        switch type {
        case .screen:
            guard let attachments = CMSampleBufferGetSampleAttachmentsArray(sb, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
                  let raw = attachments.first?[.status] as? Int, SCFrameStatus(rawValue: raw) == .complete else { return }
            appendVideo(sb)
        case .audio:
            append(sb, to: systemIn)
        default:
            break
        }
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sb: CMSampleBuffer, from connection: AVCaptureConnection) {
        append(sb, to: micIn)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        DispatchQueue.main.async { self.onStreamError?(error) }
    }

    private func appendVideo(_ sb: CMSampleBuffer) {
        guard !paused, !finishing, let writer = writer, let input = videoIn, let out = retimed(sb) else { return }
        let pts = CMSampleBufferGetPresentationTimeStamp(out)
        if !started {
            guard writer.startWriting() else { return }
            writer.startSession(atSourceTime: pts)
            startTime = pts
            started = true
        }
        if lastVideo.isValid && pts <= lastVideo { return }
        if input.isReadyForMoreMediaData, input.append(out) { lastVideo = pts }
    }

    private func append(_ sb: CMSampleBuffer, to input: AVAssetWriterInput?) {
        guard started, !paused, !finishing, let input = input, writer?.status == .writing else { return }
        guard let out = retimed(sb), CMSampleBufferGetPresentationTimeStamp(out) >= startTime else { return }
        if input.isReadyForMoreMediaData { input.append(out) }
    }

    private var startTime = CMTime.zero

    /// Shifts a buffer back by the time spent paused.
    private func retimed(_ sb: CMSampleBuffer) -> CMSampleBuffer? {
        if offset == .zero { return sb }
        var count: CMItemCount = 0
        CMSampleBufferGetSampleTimingInfoArray(sb, entryCount: 0, arrayToFill: nil, entriesNeededOut: &count)
        var timing = [CMSampleTimingInfo](repeating: CMSampleTimingInfo(), count: max(1, count))
        CMSampleBufferGetSampleTimingInfoArray(sb, entryCount: count, arrayToFill: &timing, entriesNeededOut: &count)
        for i in 0..<Int(count) {
            timing[i].presentationTimeStamp = timing[i].presentationTimeStamp - offset
            if timing[i].decodeTimeStamp.isValid { timing[i].decodeTimeStamp = timing[i].decodeTimeStamp - offset }
        }
        var copy: CMSampleBuffer?
        CMSampleBufferCreateCopyWithNewTiming(allocator: nil, sampleBuffer: sb, sampleTimingEntryCount: count,
                                              sampleTimingArray: &timing, sampleBufferOut: &copy)
        return copy
    }

    // =================================================================== pause / stop

    func setPaused(_ p: Bool) {
        let now = CMClockGetTime(CMClockGetHostTimeClock())
        q.async {
            guard p != self.paused else { return }
            if p {
                self.pausedAt = now
            } else if self.pausedAt.isValid {
                self.offset = self.offset + (now - self.pausedAt)
                self.pausedAt = .invalid
            }
            self.paused = p
        }
    }

    /// Stops capturing and finishes the file. Calls back on the main queue with the error, if any.
    func stop(completion: @escaping (Error?) -> Void) {
        let s = stream
        stream = nil
        Task {
            try? await s?.stopCapture()
            self.micSession?.stopRunning()
            self.q.async {
                self.finishing = true
                guard let writer = self.writer, self.started, writer.status == .writing else {
                    self.writer?.cancelWriting()
                    DispatchQueue.main.async { completion(Failure.nothingRecorded) }
                    return
                }
                self.videoIn?.markAsFinished()
                self.systemIn?.markAsFinished()
                self.micIn?.markAsFinished()
                let endTime = self.lastVideo
                if endTime.isValid { writer.endSession(atSourceTime: endTime) }
                let twoTracks = self.systemIn != nil && self.micIn != nil
                writer.finishWriting {
                    if writer.status != .completed {
                        let msg = writer.error?.localizedDescription ?? "The file could not be written."
                        DispatchQueue.main.async { completion(Failure.writer(msg)) }
                        return
                    }
                    guard twoTracks else { DispatchQueue.main.async { completion(nil) }; return }
                    AudioMixdown.run(self.url) { _ in DispatchQueue.main.async { completion(nil) } }
                }
            }
        }
    }

    func cancel() {
        stop { [url] _ in try? FileManager.default.removeItem(at: url) }
    }
}

/// Rewrites a movie with several audio tracks so it has one: the video is copied unchanged, the audio tracks
/// are mixed (AVAssetReaderAudioMixOutput) and encoded to AAC. On any failure the original file is kept,
/// which still has every track.
enum AudioMixdown {
    static func run(_ url: URL, completion: @escaping (Bool) -> Void) {
        let asset = AVURLAsset(url: url)
        let tmp = url.deletingLastPathComponent().appendingPathComponent(".mix-\(UUID().uuidString).mov")
        let videoTracks = asset.tracks(withMediaType: .video)
        let audioTracks = asset.tracks(withMediaType: .audio)
        guard let vt = videoTracks.first, audioTracks.count > 1,
              let reader = try? AVAssetReader(asset: asset),
              let writer = try? AVAssetWriter(outputURL: tmp, fileType: .mov) else { completion(false); return }

        let vOut = AVAssetReaderTrackOutput(track: vt, outputSettings: nil)
        let aOut = AVAssetReaderAudioMixOutput(audioTracks: audioTracks, audioSettings: [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: 48000,
            AVNumberOfChannelsKey: 2,
            AVLinearPCMBitDepthKey: 32,
            AVLinearPCMIsFloatKey: true,
            AVLinearPCMIsNonInterleaved: false,
            AVLinearPCMIsBigEndianKey: false,
        ])
        let vIn = AVAssetWriterInput(mediaType: .video, outputSettings: nil,
                                     sourceFormatHint: vt.formatDescriptions.first.map { $0 as! CMFormatDescription })
        vIn.transform = vt.preferredTransform
        let aIn = AVAssetWriterInput(mediaType: .audio, outputSettings: [
            AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 48000, AVNumberOfChannelsKey: 2, AVEncoderBitRateKey: 192_000,
        ])
        guard reader.canAdd(vOut), reader.canAdd(aOut), writer.canAdd(vIn), writer.canAdd(aIn) else { completion(false); return }
        reader.add(vOut); reader.add(aOut); writer.add(vIn); writer.add(aIn)
        guard reader.startReading(), writer.startWriting() else { completion(false); return }
        writer.startSession(atSourceTime: .zero)

        let group = DispatchGroup()
        for (input, output, label) in [(vIn, vOut as AVAssetReaderOutput, "v"), (aIn, aOut as AVAssetReaderOutput, "a")] {
            group.enter()
            let queue = DispatchQueue(label: "screenX.mixdown.\(label)")
            input.requestMediaDataWhenReady(on: queue) {
                while input.isReadyForMoreMediaData {
                    if let sb = output.copyNextSampleBuffer() {
                        if !input.append(sb) { reader.cancelReading(); input.markAsFinished(); group.leave(); return }
                    } else {
                        input.markAsFinished()
                        group.leave()
                        return
                    }
                }
            }
        }
        group.notify(queue: .global()) {
            guard reader.status == .completed else {
                writer.cancelWriting()
                try? FileManager.default.removeItem(at: tmp)
                completion(false)
                return
            }
            writer.finishWriting {
                guard writer.status == .completed,
                      (try? FileManager.default.replaceItemAt(url, withItemAt: tmp)) != nil else {
                    try? FileManager.default.removeItem(at: tmp)
                    completion(false)
                    return
                }
                completion(true)
            }
        }
    }
}

extension RecordingEngine {
    /// `screenX --recordtest <file.mov> [--mic]`: records 3 s of the main display with system audio (and the
    /// microphone), pausing for 1 s in the middle, writes "<file>.log" with the result and quits.
    @MainActor
    static func selfTest(_ url: URL, mic: Bool) {
        let log = url.appendingPathExtension("log")
        func done(_ text: String) {
            try? text.write(to: log, atomically: true, encoding: .utf8)
            NSApp.terminate(nil)
        }
        guard let screen = NSScreen.main,
              let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber else { done("FAIL no screen"); return }
        try? FileManager.default.removeItem(at: url)
        let e = RecordingEngine(url: url)
        let o = Options(displayID: CGDirectDisplayID(number.uint32Value), rect: CGRect(x: 100, y: 100, width: 641, height: 401),
                        scale: screen.backingScaleFactor, fps: 30, cursor: true, systemAudio: true, mic: mic)
        Task { @MainActor in
            do { try await e.start(o) } catch { done("FAIL start: \(error.localizedDescription)"); return }
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            e.setPaused(true)
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            e.setPaused(false)
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            e.stop { error in
                if let error = error { done("FAIL stop: \(error.localizedDescription)"); return }
                let asset = AVURLAsset(url: url)
                let v = asset.tracks(withMediaType: .video).first
                let lines = [
                    "micUsed \(e.micUsed)",
                    "duration \(String(format: "%.2f", asset.duration.seconds))",
                    "video \(v.map { "\(Int($0.naturalSize.width))x\(Int($0.naturalSize.height))" } ?? "none")",
                    "audioTracks \(asset.tracks(withMediaType: .audio).count)",
                    "DONE",
                ]
                done(lines.joined(separator: "\n"))
            }
        }
    }
}
