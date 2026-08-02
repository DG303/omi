import XCTest

@testable import Omi_Computer

/// The self-hosted Mac build runs with the microphone denied in TCC — the
/// iPhone stays the room mic, and the Mac contributes only what the phone
/// cannot hear (the far end of calls). Two mics on one room would interleave
/// into a single in-progress conversation, since the backend keys that by uid
/// alone. These tests pin the start decision that makes mic-less capture legal.
@MainActor
final class CaptureStartDecisionTests: XCTestCase {

    func testMicGrantedCapturesBothSources() {
        XCTAssertEqual(
            AppState.captureStartDecision(
                micGranted: true, systemAudioMode: .always, systemAudioSupported: true),
            .micAndSystem)
    }

    func testMicGrantedStillStartsWhenSystemAudioIsOff() {
        // Unchanged legacy behaviour: a granted mic alone is a valid session.
        XCTAssertEqual(
            AppState.captureStartDecision(
                micGranted: true, systemAudioMode: .never, systemAudioSupported: true),
            .micAndSystem)
        XCTAssertEqual(
            AppState.captureStartDecision(
                micGranted: true, systemAudioMode: .always, systemAudioSupported: false),
            .micAndSystem)
    }

    func testMicDeniedFallsBackToSystemOnly() {
        XCTAssertEqual(
            AppState.captureStartDecision(
                micGranted: false, systemAudioMode: .always, systemAudioSupported: true),
            .systemOnly)
    }

    func testMicDeniedInMeetingModeIsStillSystemOnly() {
        // reconcileCapture() gates the system tap on meeting state; the session
        // is still legal to arm.
        XCTAssertEqual(
            AppState.captureStartDecision(
                micGranted: false, systemAudioMode: .onlyDuringMeetings, systemAudioSupported: true),
            .systemOnly)
    }

    func testMicDeniedAndSystemAudioNeverIsBlocked() {
        // No source at all — prompt for the mic rather than "record" silence.
        XCTAssertEqual(
            AppState.captureStartDecision(
                micGranted: false, systemAudioMode: .never, systemAudioSupported: true),
            .blocked)
    }

    func testMicDeniedOnUnsupportedOSIsBlocked() {
        XCTAssertEqual(
            AppState.captureStartDecision(
                micGranted: false, systemAudioMode: .always, systemAudioSupported: false),
            .blocked)
    }
}
