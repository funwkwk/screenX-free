import AppKit

/// Google Lens image search. screenX writes a small local page that posts the snip to lens.google.com/v3/upload
/// (the same request the "Search any image" box on google.com sends) and opens it in the default browser. The
/// upload therefore runs in the user's own browser session and Google redirects straight to the results.
/// If the page cannot be written, the snip is copied and the Lens box opened so it can be pasted by hand.
enum Lens {
    static func search(_ image: NSImage) {
        guard let data = jpeg(image) else { fallback(image); return }
        let lang = Locale.preferredLanguages.first?.split(separator: "-").first.map(String.init) ?? "en"
        let ms = Int(Date().timeIntervalSince1970 * 1000)
        let action = "https://lens.google.com/v3/upload?hl=\(lang)&st=\(ms)"
        let html = """
        <!doctype html>
        <html><head><meta charset="utf-8"><title>Google Lens · screenX</title>
        <style>body{font:15px -apple-system,system-ui,sans-serif;display:grid;place-items:center;height:90vh;color:#555}</style>
        </head><body>
        <p id="msg">Opening Google Lens…</p>
        <form id="f" method="POST" enctype="multipart/form-data" action="\(action)">
        <input type="file" name="encoded_image" id="i" hidden></form>
        <script>
        try {
          const bin = atob("\(data.base64EncodedString())");
          const bytes = new Uint8Array(bin.length);
          for (let k = 0; k < bin.length; k++) bytes[k] = bin.charCodeAt(k);
          const dt = new DataTransfer();
          dt.items.add(new File([bytes], "screenX.jpg", { type: "image/jpeg" }));
          document.getElementById("i").files = dt.files;
          document.getElementById("f").submit();
        } catch (e) {
          document.getElementById("msg").textContent =
            "This browser could not send the image. The snip is on the clipboard: open lens.google.com and press Command-V.";
        }
        </script></body></html>
        """
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("screenX-lens", isDirectory: true)
        let page = dir.appendingPathComponent("lens-\(ms).html")
        do {
            try? FileManager.default.removeItem(at: dir) // drop pages from earlier searches
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try Data(html.utf8).write(to: page)
        } catch {
            fallback(image)
            return
        }
        ImageTools.copy(image) // also on the clipboard, in case the browser blocks the upload
        NSWorkspace.shared.open(page)
        Toast.show("Searching with Google Lens…")
    }

    private static func fallback(_ image: NSImage) {
        ImageTools.copy(image)
        if let url = URL(string: "https://www.google.com/?olud") { NSWorkspace.shared.open(url) }
        Toast.show("Could not upload to Google Lens. The snip is copied: press Command-V in the Lens box.")
    }

    /// JPEG on white (freeform snips are transparent outside the shape), at most 2400 px on the long side.
    private static func jpeg(_ image: NSImage) -> Data? {
        guard let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }
        let k = min(1, 2400 / CGFloat(max(cg.width, cg.height)))
        let w = max(1, Int(CGFloat(cg.width) * k)), h = max(1, Int(CGFloat(cg.height) * k))
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
        ctx.setFillColor(CGColor(gray: 1, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
        ctx.interpolationQuality = .high
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
        guard let flat = ctx.makeImage() else { return nil }
        return NSBitmapImageRep(cgImage: flat).representation(using: .jpeg, properties: [.compressionFactor: 0.9])
    }
}
