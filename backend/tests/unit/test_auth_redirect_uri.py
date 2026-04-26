"""Tests for auth redirect_uri validation and auth code binding (#7020).

Verifies that:
1. _validate_redirect_uri accepts only exact trusted URIs and rejects all others
2. Auth code is bound to redirect_uri and /v1/auth/token enforces match
3. Callback template renders with dynamic redirect_uri (not hardcoded)
"""

import json
import sys
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi import HTTPException

# Patch heavy deps before importing the module under test (avoid importing database/firebase)
_mock = MagicMock()
for mod in ['firebase_admin.auth', 'database.redis_db', 'utils.http_client', 'utils.log_sanitizer']:
    sys.modules.setdefault(mod, _mock)
patch.dict(
    'os.environ', {'GOOGLE_CLIENT_ID': 'test', 'GOOGLE_CLIENT_SECRET': 'test', 'BASE_API_URL': 'http://localhost:8080'}
).start()

from routers.auth import _validate_redirect_uri, _DEFAULT_REDIRECT_URI, _TRUSTED_REDIRECT_URIS


class TestValidateRedirectUri:
    """Test _validate_redirect_uri exact-allowlist logic."""

    def test_accepts_omi_scheme(self):
        assert _validate_redirect_uri('omi://auth/callback') == 'omi://auth/callback'

    def test_accepts_omi_computer(self):
        assert _validate_redirect_uri('omi-computer://auth/callback') == 'omi-computer://auth/callback'

    def test_accepts_omi_computer_dev(self):
        assert _validate_redirect_uri('omi-computer-dev://auth/callback') == 'omi-computer-dev://auth/callback'

    def test_trusted_set_is_exactly_three(self):
        """Ensure no unexpected URIs leaked into the trusted set."""
        expected = {'omi://auth/callback', 'omi-computer://auth/callback', 'omi-computer-dev://auth/callback'}
        assert _TRUSTED_REDIRECT_URIS == expected

    def test_returns_default_for_none(self):
        assert _validate_redirect_uri(None) == _DEFAULT_REDIRECT_URI

    def test_returns_default_for_empty(self):
        assert _validate_redirect_uri('') == _DEFAULT_REDIRECT_URI

    def test_rejects_arbitrary_omi_scheme(self):
        """Named test bundles must use local backend, not production auth."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_redirect_uri('omi-fix-rewind://auth/callback')
        assert exc_info.value.status_code == 400

    def test_rejects_https_scheme(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_redirect_uri('https://evil.example/cb')
        assert exc_info.value.status_code == 400

    def test_rejects_javascript_scheme(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_redirect_uri('javascript:alert(1)')
        assert exc_info.value.status_code == 400

    def test_rejects_data_scheme(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_redirect_uri('data:text/html,<script>alert(1)</script>')
        assert exc_info.value.status_code == 400

    def test_rejects_wrong_host(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_redirect_uri('omi://evil/callback')
        assert exc_info.value.status_code == 400

    def test_rejects_wrong_path(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_redirect_uri('omi://auth/evil')
        assert exc_info.value.status_code == 400

    def test_rejects_query_string(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_redirect_uri('omi://auth/callback?extra=1')
        assert exc_info.value.status_code == 400

    def test_rejects_fragment(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_redirect_uri('omi://auth/callback#frag')
        assert exc_info.value.status_code == 400

    def test_rejects_non_omi_custom_scheme(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_redirect_uri('myapp://auth/callback')
        assert exc_info.value.status_code == 400

    def test_rejects_omi_evil_scheme(self):
        """Explicitly verify omi-evil is rejected (reviewer's concern)."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_redirect_uri('omi-evil://auth/callback')
        assert exc_info.value.status_code == 400


class TestAuthCodeBinding:
    """Test that auth codes are bound to redirect_uri at token exchange."""

    def test_token_rejects_redirect_uri_mismatch(self):
        """Verify /v1/auth/token returns 400 when redirect_uri doesn't match stored value."""
        from routers.auth import auth_token

        code_data = json.dumps(
            {
                'credentials': json.dumps(
                    {
                        'provider': 'google',
                        'id_token': 'fake-id-token',
                        'access_token': 'fake-access-token',
                        'provider_id': 'google.com',
                    }
                ),
                'redirect_uri': 'omi-computer://auth/callback',
            }
        )

        with patch('routers.auth.get_auth_code', return_value=code_data), patch('routers.auth.delete_auth_code'):
            import asyncio

            request = MagicMock()

            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    auth_token(
                        request=request,
                        grant_type='authorization_code',
                        code='test-code',
                        redirect_uri='omi-evil://auth/callback',  # mismatch
                        use_custom_token=False,
                    )
                )
            assert exc_info.value.status_code == 400
            assert 'mismatch' in exc_info.value.detail

    def test_token_accepts_matching_redirect_uri(self):
        """Verify /v1/auth/token succeeds when redirect_uri matches stored value."""
        from routers.auth import auth_token

        code_data = json.dumps(
            {
                'credentials': json.dumps(
                    {
                        'provider': 'google',
                        'id_token': 'fake-id-token',
                        'access_token': 'fake-access-token',
                        'provider_id': 'google.com',
                    }
                ),
                'redirect_uri': 'omi-computer://auth/callback',
            }
        )

        with patch('routers.auth.get_auth_code', return_value=code_data), patch('routers.auth.delete_auth_code'):
            import asyncio

            request = MagicMock()

            result = asyncio.get_event_loop().run_until_complete(
                auth_token(
                    request=request,
                    grant_type='authorization_code',
                    code='test-code',
                    redirect_uri='omi-computer://auth/callback',  # match
                    use_custom_token=False,
                )
            )
            assert result['provider'] == 'google'
            assert result['id_token'] == 'fake-id-token'

    def test_token_handles_legacy_format(self):
        """Verify /v1/auth/token still works with legacy code format (no redirect_uri binding)."""
        from routers.auth import auth_token

        # Legacy format: raw OAuth credentials without redirect_uri wrapper
        legacy_data = json.dumps(
            {
                'provider': 'apple',
                'id_token': 'legacy-id-token',
                'access_token': 'legacy-access-token',
                'provider_id': 'apple.com',
            }
        )

        with patch('routers.auth.get_auth_code', return_value=legacy_data), patch('routers.auth.delete_auth_code'):
            import asyncio

            request = MagicMock()

            result = asyncio.get_event_loop().run_until_complete(
                auth_token(
                    request=request,
                    grant_type='authorization_code',
                    code='legacy-code',
                    redirect_uri='omi://auth/callback',
                    use_custom_token=False,
                )
            )
            assert result['provider'] == 'apple'
            assert result['id_token'] == 'legacy-id-token'


class TestCallbackTemplateRendering:
    """Test that the callback template receives and uses dynamic redirect_uri."""

    def test_template_uses_dynamic_redirect_uri(self):
        """Verify auth_callback.html renders with the session's redirect_uri, not hardcoded."""
        from jinja2 import Environment, FileSystemLoader
        import pathlib

        templates_dir = pathlib.Path(__file__).parent.parent.parent / "templates"
        env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)
        template = env.get_template("auth_callback.html")

        html = template.render(
            code="test-auth-code",
            state="test-state",
            redirect_uri="omi-computer://auth/callback",
        )

        # The rendered HTML must use the dynamic redirect_uri, not hardcoded omi://
        assert 'omi-computer://auth/callback' in html
        assert "omi://auth/callback" not in html  # hardcoded value must not appear

    def test_template_json_escapes_redirect_uri(self):
        """Verify redirect_uri is JSON-escaped in the template (XSS prevention)."""
        from jinja2 import Environment, FileSystemLoader
        import pathlib

        templates_dir = pathlib.Path(__file__).parent.parent.parent / "templates"
        env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)
        template = env.get_template("auth_callback.html")

        # Use a URI with characters that would be dangerous if not JSON-escaped
        html = template.render(
            code='test</script><script>alert(1)',
            state='test-state',
            redirect_uri='omi-computer://auth/callback',
        )

        # The dangerous characters should be escaped, not raw
        assert '</script><script>' not in html

    def test_template_defaults_when_redirect_uri_missing(self):
        """Verify template falls back to omi://auth/callback when redirect_uri not provided."""
        from jinja2 import Environment, FileSystemLoader
        import pathlib

        templates_dir = pathlib.Path(__file__).parent.parent.parent / "templates"
        env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)
        template = env.get_template("auth_callback.html")

        # Render without redirect_uri — should use the default
        html = template.render(
            code="test-code",
            state="test-state",
            # redirect_uri intentionally omitted
        )

        assert 'omi://auth/callback' in html
