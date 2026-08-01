import XCTest
@testable import TeleShieldApp

final class TimestampFormattingTests: XCTestCase {
    func testBlockRecordTimestampUsesRequestedLocalTimezone() {
        let taipei = TimeZone(secondsFromGMT: 8 * 60 * 60)!

        XCTAssertEqual(
            TimestampFormatter.localString(
                "2026-08-01T07:48:43.647254+00:00",
                timeZone: taipei
            ),
            "2026-08-01 15:48:43"
        )
    }
}
