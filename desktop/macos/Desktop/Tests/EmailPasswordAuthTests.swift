import XCTest

@testable import Omi_Computer

/// Covers the pure halves of the email/password sign-in path: parsing the
/// identitytoolkit response and reading credentials out of the environment.
/// The network call itself is proven by the live curl in the plan's Task 1.
@MainActor
final class EmailPasswordAuthTests: XCTestCase {

    // MARK: - Response parsing

    func testParsesSuccessfulResponseWithStringExpiresIn() throws {
        let json = """
        {"idToken":"id-abc","refreshToken":"refresh-abc","expiresIn":"3600","localId":"xLRrz1vnCuarV868qUwlNp96SJe2","email":"real.bsanson@gmail.com"}
        """
        let result = try AuthService.parseSignInWithPasswordResponse(json.data(using: .utf8)!)

        XCTAssertEqual(result.idToken, "id-abc")
        XCTAssertEqual(result.refreshToken, "refresh-abc")
        XCTAssertEqual(result.expiresIn, 3600)
        XCTAssertEqual(result.localId, "xLRrz1vnCuarV868qUwlNp96SJe2")
    }

    func testParsesNumericExpiresIn() throws {
        let json = """
        {"idToken":"id-abc","refreshToken":"refresh-abc","expiresIn":900,"localId":"uid-1"}
        """
        let result = try AuthService.parseSignInWithPasswordResponse(json.data(using: .utf8)!)

        XCTAssertEqual(result.expiresIn, 900)
    }

    func testDefaultsExpiresInWhenAbsent() throws {
        let json = """
        {"idToken":"id-abc","refreshToken":"refresh-abc","localId":"uid-1"}
        """
        let result = try AuthService.parseSignInWithPasswordResponse(json.data(using: .utf8)!)

        XCTAssertEqual(result.expiresIn, 3600)
    }

    func testThrowsOnErrorPayload() {
        // What Firebase returns when the Email/Password provider is disabled.
        let json = """
        {"error":{"code":400,"message":"OPERATION_NOT_ALLOWED"}}
        """
        XCTAssertThrowsError(
            try AuthService.parseSignInWithPasswordResponse(json.data(using: .utf8)!))
    }

    func testThrowsWhenLocalIdMissing() {
        // Without localId we would save tokens under an empty uid and every
        // later request would look like a different user.
        let json = """
        {"idToken":"id-abc","refreshToken":"refresh-abc","expiresIn":"3600"}
        """
        XCTAssertThrowsError(
            try AuthService.parseSignInWithPasswordResponse(json.data(using: .utf8)!))
    }

    func testThrowsOnGarbage() {
        XCTAssertThrowsError(
            try AuthService.parseSignInWithPasswordResponse(Data("not json".utf8)))
    }

    // MARK: - Environment reading

    func testEnvValueReadsSetVariable() {
        setenv("OMI_TEST_ENV_VALUE", "hello", 1)
        defer { unsetenv("OMI_TEST_ENV_VALUE") }

        XCTAssertEqual(AuthService.envValue("OMI_TEST_ENV_VALUE"), "hello")
    }

    func testEnvValueTrimsWhitespace() {
        setenv("OMI_TEST_ENV_VALUE", "  hello  ", 1)
        defer { unsetenv("OMI_TEST_ENV_VALUE") }

        XCTAssertEqual(AuthService.envValue("OMI_TEST_ENV_VALUE"), "hello")
    }

    func testEnvValueIsNilWhenUnsetOrBlank() {
        unsetenv("OMI_TEST_ENV_VALUE")
        XCTAssertNil(AuthService.envValue("OMI_TEST_ENV_VALUE"))

        setenv("OMI_TEST_ENV_VALUE", "   ", 1)
        defer { unsetenv("OMI_TEST_ENV_VALUE") }
        XCTAssertNil(AuthService.envValue("OMI_TEST_ENV_VALUE"))
    }
}
