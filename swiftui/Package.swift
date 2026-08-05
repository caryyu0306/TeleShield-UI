// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "TeleShieldSwiftUI",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(
            name: "TeleShieldApp",
            targets: ["TeleShieldApp"]
        ),
        .executable(
            name: "TeleShieldUpdater",
            targets: ["TeleShieldUpdater"]
        )
    ],
    targets: [
        .executableTarget(
            name: "TeleShieldApp",
            path: "Sources/TeleShieldApp"
        ),
        .executableTarget(
            name: "VisionOCR",
            path: "Sources/VisionOCR"
        ),
        .executableTarget(
            name: "TeleShieldUpdater",
            path: "Sources/TeleShieldUpdater"
        ),
        .testTarget(
            name: "TeleShieldAppTests",
            dependencies: ["TeleShieldApp"],
            path: "Tests/TeleShieldAppTests"
        )
    ]
)
