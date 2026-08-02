import AppKit
import CoreText
import Darwin
import Foundation
import ImageIO
import UniformTypeIdentifiers

guard CommandLine.arguments.count == 2 else {
    fputs("usage: generate_vision_ocr_fixture.swift OUTPUT.png\n", stderr)
    exit(EXIT_FAILURE)
}

let outputURL = URL(fileURLWithPath: CommandLine.arguments[1])
let width = 1200
let height = 300
let colorSpace = CGColorSpaceCreateDeviceRGB()
guard let context = CGContext(
    data: nil,
    width: width,
    height: height,
    bitsPerComponent: 8,
    bytesPerRow: 0,
    space: colorSpace,
    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
) else {
    fputs("unable to create image context\n", stderr)
    exit(EXIT_FAILURE)
}

context.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
context.fill(CGRect(x: 0, y: 0, width: width, height: height))

let font = CTFontCreateWithName("PingFangSC-Regular" as CFString, 96, nil)
let attributes: [CFString: Any] = [
    kCTFontAttributeName: font,
    kCTForegroundColorAttributeName: CGColor(red: 0, green: 0, blue: 0, alpha: 1),
]
let attributedString = CFAttributedStringCreate(
    nil,
    "投资稳赚" as CFString,
    attributes as CFDictionary
)
guard let attributedString else {
    fputs("unable to create attributed text\n", stderr)
    exit(EXIT_FAILURE)
}

let line = CTLineCreateWithAttributedString(attributedString)
let bounds = CTLineGetBoundsWithOptions(line, [])
context.textPosition = CGPoint(
    x: (CGFloat(width) - bounds.width) / 2,
    y: (CGFloat(height) - bounds.height) / 2
)
CTLineDraw(line, context)

guard let image = context.makeImage(),
      let destination = CGImageDestinationCreateWithURL(
          outputURL as CFURL,
          UTType.png.identifier as CFString,
          1,
          nil
      ) else {
    fputs("unable to encode OCR fixture\n", stderr)
    exit(EXIT_FAILURE)
}

CGImageDestinationAddImage(destination, image, nil)
guard CGImageDestinationFinalize(destination) else {
    fputs("unable to write OCR fixture\n", stderr)
    exit(EXIT_FAILURE)
}
