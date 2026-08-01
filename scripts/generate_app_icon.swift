import AppKit
import Foundation

guard CommandLine.arguments.count == 2 else {
    fputs("usage: generate_app_icon.swift <iconset-directory>\n", stderr)
    exit(2)
}

let outputDirectory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: outputDirectory, withIntermediateDirectories: true)

let sizes: [(Int, String)] = [
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png")
]

func makeColor(_ red: CGFloat, _ green: CGFloat, _ blue: CGFloat, _ alpha: CGFloat = 1) -> NSColor {
    NSColor(calibratedRed: red, green: green, blue: blue, alpha: alpha)
}

func renderIcon(size: Int) -> Data {
    let canvas = CGFloat(size)
    guard let representation = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: size,
        pixelsHigh: size,
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bitmapFormat: [],
        bytesPerRow: 0,
        bitsPerPixel: 32
    ) else {
        fatalError("Unable to create icon bitmap")
    }
    representation.size = NSSize(width: canvas, height: canvas)

    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: representation)
    defer { NSGraphicsContext.restoreGraphicsState() }

    let inset = canvas * 0.035
    let radius = canvas * 0.22
    let tileRect = NSRect(x: inset, y: inset, width: canvas - inset * 2, height: canvas - inset * 2)
    let tile = NSBezierPath(roundedRect: tileRect, xRadius: radius, yRadius: radius)

    let gradient = NSGradient(colors: [
        makeColor(0.16, 0.46, 0.86),
        makeColor(0.08, 0.22, 0.48),
        makeColor(0.035, 0.10, 0.24)
    ])!
    gradient.draw(in: tile, angle: -35)

    makeColor(1, 1, 1, 0.18).setStroke()
    tile.lineWidth = max(1, canvas * 0.012)
    tile.stroke()

    let emoji = "🛡️" as NSString
    let emojiFont = NSFont(name: "Apple Color Emoji", size: canvas * 0.62)
        ?? NSFont.systemFont(ofSize: canvas * 0.62)
    let attributes: [NSAttributedString.Key: Any] = [.font: emojiFont]
    let emojiSize = emoji.size(withAttributes: attributes)
    let emojiOrigin = NSPoint(
        x: (canvas - emojiSize.width) / 2,
        y: (canvas - emojiSize.height) / 2 - canvas * 0.015
    )
    emoji.draw(at: emojiOrigin, withAttributes: attributes)

    guard let png = representation.representation(using: .png, properties: [:]) else {
        fatalError("Unable to encode icon PNG")
    }
    return png
}

for (size, filename) in sizes {
    try renderIcon(size: size).write(to: outputDirectory.appendingPathComponent(filename))
}
