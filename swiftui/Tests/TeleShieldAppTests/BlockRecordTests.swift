import XCTest
@testable import TeleShieldApp

final class BlockRecordTests: XCTestCase {
    func testStructuredAnalysisDecodesAlongsideBlockRecord() throws {
        let data = """
        [{
          "time": "2026-08-04T03:42:15+00:00",
          "source": "private",
          "user_id": 134037075,
          "name": "Blocked User",
          "reason": "高信心垃圾訊息",
          "details": {
            "analysis": {
              "reason_code": "spam_high_confidence",
              "category_labels": ["投資", "廣告"],
              "intent_labels": ["導流", "立即操作"],
              "phishing_labels": [],
              "matched_rule_labels": ["保證獲利", "立即加入"],
              "score": 7,
              "threshold": 4,
              "score_type": "spam",
              "score_type_label": "垃圾訊息",
              "analysis_source": "text",
              "analysis_source_label": "文字",
              "content_excerpt": "投資穩賺立即",
              "sender_context_labels": ["新非聯絡人"]
            }
          }
        }]
        """.data(using: .utf8)!

        let record = try JSONDecoder().decode([BlockRecord].self, from: data)[0]

        XCTAssertEqual(record.reason, "高信心垃圾訊息")
        XCTAssertEqual(record.userID, "134037075")
        XCTAssertEqual(record.details?.analysis?.categoryLabels, ["投資", "廣告"])
        XCTAssertEqual(record.details?.analysis?.score, 7)
        XCTAssertEqual(record.details?.analysis?.scoreTypeLabel, "垃圾訊息")
        XCTAssertEqual(record.details?.analysis?.contentExcerpt, "投資穩賺立即")
    }

    func testLegacyBlockRecordStillDecodesWithoutAnalysis() throws {
        let data = """
        [{
          "time": "2026-08-04T03:42:15+00:00",
          "source": "private",
          "user_id": 42,
          "name": "Legacy User",
          "reason": "廣告內容"
        }]
        """.data(using: .utf8)!

        let record = try JSONDecoder().decode([BlockRecord].self, from: data)[0]

        XCTAssertEqual(record.reason, "廣告內容")
        XCTAssertNil(record.details?.analysis)
    }
}
