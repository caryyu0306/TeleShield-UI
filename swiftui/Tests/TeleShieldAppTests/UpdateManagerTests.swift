import XCTest
@testable import TeleShieldApp

final class UpdateManagerTests: XCTestCase {
    func testVersionParsingAcceptsTagsWithOptionalPatch() {
        XCTAssertEqual(UpdateVersion("v1.2"), UpdateVersion(major: 1, minor: 2, patch: 0))
        XCTAssertEqual(UpdateVersion("1.3.4")?.description, "1.3.4")
    }

    func testVersionOrderingUsesSemanticComponents() {
        XCTAssertTrue(UpdateVersion("1.10.0")! > UpdateVersion("1.9.9")!)
        XCTAssertFalse(UpdateVersion("1.2.0")! > UpdateVersion("v1.2")!)
    }

    func testInvalidVersionsAreRejected() {
        XCTAssertNil(UpdateVersion("v1"))
        XCTAssertNil(UpdateVersion("version-1.2.0"))
        XCTAssertNil(UpdateVersion("1.2.-1"))
    }
}
