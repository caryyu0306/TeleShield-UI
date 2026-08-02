import Darwin
import Foundation
import Vision

private struct OCRResponse: Encodable {
    let text: String
    let languages: [String]
}

@main
struct VisionOCRMain {
    private static let requestedLanguages = ["zh-Hant", "zh-Hans", "en-US"]
    private static let displayLanguages = ["zh-Hant", "zh-Hans", "en"]

    static func main() {
        do {
            try run(arguments: Array(ProcessInfo.processInfo.arguments.dropFirst()))
        } catch {
            let message = "VisionOCR: \(error.localizedDescription)\n"
            FileHandle.standardError.write(Data(message.utf8))
            Darwin.exit(EXIT_FAILURE)
        }
    }

    private static func run(arguments: [String]) throws {
        if arguments == ["--self-test"] {
            try writeResponse(OCRResponse(text: "", languages: displayLanguages))
            return
        }

        guard arguments.count == 2, arguments[0] == "--image" else {
            throw VisionOCRError.usage
        }

        let imageURL = URL(fileURLWithPath: arguments[1])
        guard FileManager.default.isReadableFile(atPath: imageURL.path) else {
            throw VisionOCRError.unreadableImage
        }

        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = requestedLanguages
        // Vision does not apply language correction to Chinese. Keeping this
        // disabled also avoids changing the user's local text before the
        // existing Python rule engine normalizes it.
        request.usesLanguageCorrection = false

        let handler = VNImageRequestHandler(url: imageURL, options: [:])
        try handler.perform([request])

        let observations = request.results ?? []
        let text = observations.compactMap { observation in
            observation.topCandidates(1).first?.string
        }.joined(separator: "\n")

        try writeResponse(OCRResponse(text: text, languages: displayLanguages))
    }

    private static func writeResponse(_ response: OCRResponse) throws {
        let data = try JSONEncoder().encode(response)
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([0x0A]))
    }
}

private enum VisionOCRError: LocalizedError {
    case usage
    case unreadableImage

    var errorDescription: String? {
        switch self {
        case .usage:
            return "usage: VisionOCR --image PATH | --self-test"
        case .unreadableImage:
            return "image path is missing or unreadable"
        }
    }
}
