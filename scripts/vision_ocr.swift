// Local-only menu evidence. OCR text does not establish focus or game playback.
import Foundation
import Vision
import ImageIO

// A focus outline must have four long, thin neutral-white edges, with darker
// pixels on both sides. Large card dimensions exclude keyboard keys and labels.
// This deliberately does not identify arbitrary highlighted controls.
func focusRectangles(_ image: CGImage) -> [[String: Any]] {
    let w = image.width, h = image.height
    guard w > 0, h > 0, w * h <= 20_000_000 else { return [] }
    var pixels = [UInt8](repeating: 0, count: w * h * 4)
    guard let context = CGContext(data: &pixels, width: w, height: h,
                                  bitsPerComponent: 8, bytesPerRow: w * 4,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return [] }
    context.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    func neutral(_ x: Int, _ y: Int) -> Bool {
        guard x >= 0, y >= 0, x < w, y < h else { return false }
        let i = (y * w + x) * 4
        let lo = min(pixels[i], pixels[i + 1], pixels[i + 2])
        let hi = max(pixels[i], pixels[i + 1], pixels[i + 2])
        return lo >= 195 && hi - lo <= 28
    }
    func dark(_ x: Int, _ y: Int) -> Bool {
        guard x >= 0, y >= 0, x < w, y < h else { return false }
        let i = (y * w + x) * 4
        return max(pixels[i], pixels[i + 1], pixels[i + 2]) < 185
    }
    // YouTube TV uses a substantially thicker outline than Prime. Sample beyond
    // either observed border width rather than treating its white interior as fill.
    let flank = max(4, w / 384), corner = max(14, w / 75)
    let contrastOffsets = [flank, max(6, w / 120)]
    let minW = max(120, w * 12 / 100), minH = max(60, h * 8 / 100)
    struct Edge { let x: Int; let end: Int; let y: Int }
    var edges: [Edge] = []
    for y in flank..<(h - flank) {
        var x = 0
        while x < w {
            if !neutral(x, y) { x += 1; continue }
            let start = x
            while x < w && neutral(x, y) { x += 1 }
            let length = x - start
            if length < minW || length > w * 7 / 10 { continue }
            var bestContrast = 0.0
            for offset in contrastOffsets {
                var contrast = 0, total = 0
                for sx in stride(from: start + 8, to: x - 8, by: 3) {
                    total += 1
                    if dark(sx, y - offset) && dark(sx, y + offset) { contrast += 1 }
                }
                if total > 0 { bestContrast = max(bestContrast, Double(contrast) / Double(total)) }
            }
            if bestContrast >= 0.88 {
                // Collapse adjacent scanlines representing the same thin edge.
                if !edges.contains(where: { abs($0.y - y) <= flank && abs($0.x - start) < 8 && abs($0.end - x) < 8 }) {
                    edges.append(Edge(x: start, end: x - 1, y: y))
                }
            }
        }
    }
    func vertical(_ x: Int, _ top: Int, _ bottom: Int) -> Double {
        var best = 0.0
        for offset in contrastOffsets {
            var valid = 0, total = 0
            for y in stride(from: top + corner, to: bottom - corner, by: 2) {
                total += 1
                if neutral(x, y) && dark(x - offset, y) && dark(x + offset, y) { valid += 1 }
            }
            if total > 0 { best = max(best, Double(valid) / Double(total)) }
        }
        return best
    }
    var candidates: [(Int, Int, Int, Int, Double)] = []
    for a in edges {
        for b in edges where b.y - a.y >= minH && b.y - a.y <= h / 2 {
            if abs(a.x - b.x) > corner || abs(a.end - b.end) > corner { continue }
            let top = a.y, bottom = b.y
            var left: (Int, Double) = (0, 0), right: (Int, Double) = (0, 0)
            for x in max(flank, min(a.x, b.x) - corner)...min(w - flank - 1, max(a.x, b.x) + 8) {
                let score = vertical(x, top, bottom)
                if score > left.1 { left = (x, score) }
            }
            for x in max(flank, min(a.end, b.end) - 8)...min(w - flank - 1, max(a.end, b.end) + corner) {
                let score = vertical(x, top, bottom)
                if score > right.1 { right = (x, score) }
            }
            let ratio = Double(right.0 - left.0) / Double(bottom - top)
            if left.1 < 0.9 || right.1 < 0.9 || ratio < 1.2 || ratio > 3.5 { continue }
            if !candidates.contains(where: { abs($0.0 - left.0) < flank * 2 && abs($0.1 - top) < flank * 2
                                            && abs($0.2 - right.0) < flank * 2 && abs($0.3 - bottom) < flank * 2 }) {
                candidates.append((left.0, top, right.0, bottom, min(left.1, right.1)))
            }
        }
    }
    var result: [[String: Any]] = candidates.map { left, top, right, bottom, score in
        ["x": Double(left) / Double(w), "y": Double(top) / Double(h),
         "width": Double(right - left + 1) / Double(w), "height": Double(bottom - top + 1) / Double(h),
         "confidence": score, "source": "neutral_rectangle_border"]
    }
    // Filled focus buttons are connected neutral regions. Text holes are allowed;
    // a downstream exact action-label check is mandatory before accepting one.
    var visited = [UInt8](repeating: 0, count: w * h)
    for y in 0..<h {
        for x in 0..<w {
            let seed = y * w + x
            if visited[seed] != 0 || !neutral(x, y) { continue }
            var queue = [seed], index = 0
            visited[seed] = 1
            var left = x, right = x, top = y, bottom = y
            while index < queue.count {
                let p = queue[index]; index += 1
                let px = p % w, py = p / w
                left = min(left, px); right = max(right, px)
                top = min(top, py); bottom = max(bottom, py)
                for (nx, ny) in [(px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)] {
                    if nx < 0 || ny < 0 || nx >= w || ny >= h { continue }
                    let q = ny * w + nx
                    if visited[q] == 0 && neutral(nx, ny) { visited[q] = 1; queue.append(q) }
                }
            }
            let bw = right - left + 1, bh = bottom - top + 1
            let ratio = Double(bw) / Double(bh), fill = Double(queue.count) / Double(bw * bh)
            if bw < w * 15 / 100 || bw > w * 7 / 10 || bh < h * 3 / 100 || bh > h / 5
                || ratio < 3 || ratio > 12 || fill < 0.68 { continue }
            // Require a darker perimeter outside the candidate, not a white image crop.
            var surround = 0, samples = 0
            for sx in stride(from: left + corner, to: right - corner, by: 5) {
                samples += 2
                if dark(sx, top - flank) { surround += 1 }
                if dark(sx, bottom + flank) { surround += 1 }
            }
            for sy in stride(from: top + 8, to: bottom - 8, by: 5) {
                samples += 2
                if dark(left - flank, sy) { surround += 1 }
                if dark(right + flank, sy) { surround += 1 }
            }
            if samples == 0 || Double(surround) / Double(samples) < 0.9 { continue }
            result.append(["x": Double(left) / Double(w), "y": Double(top) / Double(h),
                           "width": Double(bw) / Double(w), "height": Double(bh) / Double(h),
                           "confidence": fill, "source": "neutral_filled_button"])
        }
    }
    return result
}

func emit(_ value: [String: Any], code: Int32 = 0) -> Never {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
       let text = String(data: data, encoding: .utf8) { print(text) }
    exit(code)
}

let focusOnly = CommandLine.arguments.count == 3 && CommandLine.arguments[2] == "--focus-only"
guard CommandLine.arguments.count == 2 || focusOnly else {
    emit(["status": "error", "error": "one local PNG path is required"], code: 2)
}
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    emit(["status": "unreadable", "error": "image could not be decoded"], code: 2)
}
if focusOnly {
    emit(["status": "empty", "width": image.width, "height": image.height,
          "coordinate_system": "normalized_top_left", "observations": [],
          "focus_candidates": focusRectangles(image)])
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["en-US"]
// Avoid depending on a GPU/WindowServer session in a background automation process.
request.usesCPUOnly = true
// Names and short sports abbreviations must not be silently spell-corrected.
request.usesLanguageCorrection = false
do {
    try VNImageRequestHandler(cgImage: image, orientation: .up).perform([request])
    let rows: [[String: Any]] = (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first,
              !candidate.string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        let box = observation.boundingBox
        return ["text": candidate.string, "confidence": Double(candidate.confidence),
                // Vision uses a bottom-left origin; expose top-left screen coordinates.
                "box": ["x": Double(box.minX), "y": Double(1 - box.maxY),
                        "width": Double(box.width), "height": Double(box.height)]]
    }
    let focus = focusRectangles(image)
    emit(["status": rows.isEmpty ? "empty" : "ok", "width": image.width,
          "height": image.height, "coordinate_system": "normalized_top_left",
          "observations": rows, "focus_candidates": focus])
} catch {
    fputs("Vision: \(error.localizedDescription)\n", stderr)
    emit(["status": "error", "error": "Vision text recognition failed"], code: 1)
}
