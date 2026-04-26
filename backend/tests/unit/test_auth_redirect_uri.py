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

    def test_trusted_set_contains_expected_uris(self):
        """All three known app URIs are in the trusted set."""
        assert 'omi://auth/callback' in _TRUSTED_REDIRECT_URIS
        assert 'omi-computer://auth/callback' in _TRUSTED_REDIRECT_URIS
        assert 'omi-computer-dev://auth/callback' in _TRUSTED_REDIRECT_URIS

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


class TestAuthAuthorizeEndpoint:
    """Test auth_authorize rejects bad redirect_uri before session storage and stores valid ones."""

    def test_authorize_rejects_bad_redirect_uri_before_session_store(self):
        """Invalid redirect_uri must fail before set_auth_session is called."""
        from routers.auth import auth_authorize
        import asyncio

        request = MagicMock()
        with patch('routers.auth.set_auth_session') as mock_set:
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    auth_authorize(
                        request=request,
                        provider='google',
                        redirect_uri='omi-evil://auth/callback',
                        state='test-state',
                    )
                )
            assert exc_info.value.status_code == 400
            mock_set.assert_not_called()

    def test_authorize_stores_validated_uri_with_ttl(self):
        """Valid redirect_uri is stored in session with TTL 300."""
        from routers.auth import auth_authorize
        import asyncio

        request = MagicMock()
        with patch('routers.auth.set_auth_session') as mock_set, patch(
            'routers.auth._google_auth_redirect', new_callable=AsyncMock, return_value=MagicMock()
        ):
            asyncio.get_event_loop().run_until_complete(
                auth_authorize(
                    request=request,
                    provider='google',
                    redirect_uri='omi-computer://auth/callback',
                    state='test-state',
                )
            )
            mock_set.assert_called_once()
            args = mock_set.call_args
            session_data = args[0][1]
            ttl = args[0][2]
            assert session_data['redirect_uri'] == 'omi-computer://auth/callback'
            assert session_data['state'] == 'test-state'
            assert ttl == 300


class TestCallbackEndpoints:
    """Test Google and Apple callback endpoints bind auth codes correctly."""

    def test_google_callback_binds_redirect_uri_to_auth_code(self):
        """Google callback wraps credentials with redirect_uri and stores with TTL 300."""
        from routers.auth import auth_callback_google
        import asyncio

        session_data = {
            'provider': 'google',
            'redirect_uri': 'omi-computer://auth/callback',
            'state': 'test-state',
            'flow_type': 'user_auth',
        }
        fake_creds = json.dumps(
            {'provider': 'google', 'id_token': 'tok', 'access_token': 'at', 'provider_id': 'google.com'}
        )

        request = MagicMock()
        with patch('routers.auth.get_auth_session', return_value=session_data), patch(
            'routers.auth._exchange_provider_code_for_oauth_credentials',
            new_callable=AsyncMock,
            return_value=fake_creds,
        ), patch('routers.auth.set_auth_code') as mock_set_code, patch('routers.auth.templates') as mock_templates:
            mock_templates.TemplateResponse.return_value = MagicMock()
            asyncio.get_event_loop().run_until_complete(
                auth_callback_google(request=request, code='oauth-code', state='session-id')
            )
            mock_set_code.assert_called_once()
            stored_json = mock_set_code.call_args[0][1]
            ttl = mock_set_code.call_args[0][2]
            stored = json.loads(stored_json)
            assert stored['redirect_uri'] == 'omi-computer://auth/callback'
            assert 'credentials' in stored
            assert ttl == 300

    def test_apple_callback_binds_redirect_uri_to_auth_code(self):
        """Apple callback wraps credentials with redirect_uri and stores with TTL 300."""
        from routers.auth import auth_callback_apple_post
        import asyncio

        session_data = {
            'provider': 'apple',
            'redirect_uri': 'omi-computer-dev://auth/callback',
            'state': 'test-state',
            'flow_type': 'user_auth',
        }
        fake_creds = json.dumps(
            {'provider': 'apple', 'id_token': 'tok', 'access_token': 'at', 'provider_id': 'apple.com'}
        )

        request = MagicMock()
        with patch('routers.auth.get_auth_session', return_value=session_data), patch(
            'routers.auth._exchange_provider_code_for_oauth_credentials',
            new_callable=AsyncMock,
            return_value=fake_creds,
        ), patch('routers.auth.set_auth_code') as mock_set_code, patch('routers.auth.templates') as mock_templates:
            mock_templates.TemplateResponse.return_value = MagicMock()
            asyncio.get_event_loop().run_until_complete(
                auth_callback_apple_post(request=request, code='oauth-code', state='session-id', error=None)
            )
            mock_set_code.assert_called_once()
            stored_json = mock_set_code.call_args[0][1]
            stored = json.loads(stored_json)
            assert stored['redirect_uri'] == 'omi-computer-dev://auth/callback'

    def test_callback_template_receives_redirect_uri(self):
        """Callback passes redirect_uri to template context."""
        from routers.auth import auth_callback_google
        import asyncio

        session_data = {
            'provider': 'google',
            'redirect_uri': 'omi-computer://auth/callback',
            'state': 'test-state',
            'flow_type': 'user_auth',
        }
        fake_creds = json.dumps(
            {'provider': 'google', 'id_token': 't', 'access_token': 'a', 'provider_id': 'google.com'}
        )

        request = MagicMock()
        with patch('routers.auth.get_auth_session', return_value=session_data), patch(
            'routers.auth._exchange_provider_code_for_oauth_credentials',
            new_callable=AsyncMock,
            return_value=fake_creds,
        ), patch('routers.auth.set_auth_code'), patch('routers.auth.templates') as mock_templates:
            mock_templates.TemplateResponse.return_value = MagicMock()
            asyncio.get_event_loop().run_until_complete(auth_callback_google(request=request, code='c', state='s'))
            template_ctx = mock_templates.TemplateResponse.call_args[0][1]
            assert template_ctx['redirect_uri'] == 'omi-computer://auth/callback'

    def test_callback_defaults_redirect_uri_when_missing_from_session(self):
        """When session has no redirect_uri, callback falls back to default."""
        from routers.auth import auth_callback_google
        import asyncio

        session_data = {
            'provider': 'google',
            'state': 'test-state',
            'flow_type': 'user_auth',
            # redirect_uri intentionally missing (legacy session)
        }
        fake_creds = json.dumps(
            {'provider': 'google', 'id_token': 't', 'access_token': 'a', 'provider_id': 'google.com'}
        )

        request = MagicMock()
        with patch('routers.auth.get_auth_session', return_value=session_data), patch(
            'routers.auth._exchange_provider_code_for_oauth_credentials',
            new_callable=AsyncMock,
            return_value=fake_creds,
        ), patch('routers.auth.set_auth_code') as mock_set_code, patch('routers.auth.templates') as mock_templates:
            mock_templates.TemplateResponse.return_value = MagicMock()
            asyncio.get_event_loop().run_until_complete(auth_callback_google(request=request, code='c', state='s'))
            stored = json.loads(mock_set_code.call_args[0][1])
            assert stored['redirect_uri'] == _DEFAULT_REDIRECT_URI


class TestTokenEdgeCases:
    """Test token endpoint edge cases: malformed data, single-use codes, credentials-as-dict."""

    def test_token_deletes_code_on_use(self):
        """Auth code is single-use — delete_auth_code must be called."""
        from routers.auth import auth_token
        import asyncio

        code_data = json.dumps(
            {'provider': 'google', 'id_token': 't', 'access_token': 'a', 'provider_id': 'google.com'}
        )
        request = MagicMock()

        with patch('routers.auth.get_auth_code', return_value=code_data), patch(
            'routers.auth.delete_auth_code'
        ) as mock_delete:
            asyncio.get_event_loop().run_until_complete(
                auth_token(
                    request=request,
                    grant_type='authorization_code',
                    code='the-code',
                    redirect_uri='omi://auth/callback',
                    use_custom_token=False,
                )
            )
            mock_delete.assert_called_once_with('the-code')

    def test_token_rejects_expired_code(self):
        """Expired/missing code returns 400."""
        from routers.auth import auth_token
        import asyncio

        request = MagicMock()
        with patch('routers.auth.get_auth_code', return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    auth_token(
                        request=request,
                        grant_type='authorization_code',
                        code='gone',
                        redirect_uri='omi://auth/callback',
                        use_custom_token=False,
                    )
                )
            assert exc_info.value.status_code == 400
            assert 'expired' in exc_info.value.detail.lower()

    def test_token_rejects_malformed_json(self):
        """Malformed raw_code_data triggers generic error handler."""
        from routers.auth import auth_token
        import asyncio

        request = MagicMock()
        with patch('routers.auth.get_auth_code', return_value='not-json{{{'), patch('routers.auth.delete_auth_code'):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    auth_token(
                        request=request,
                        grant_type='authorization_code',
                        code='bad',
                        redirect_uri='omi://auth/callback',
                        use_custom_token=False,
                    )
                )
            assert exc_info.value.status_code == 400

    def test_token_handles_credentials_as_dict(self):
        """When credentials is already a dict (not JSON string), parsing succeeds."""
        from routers.auth import auth_token
        import asyncio

        code_data = json.dumps(
            {
                'credentials': {
                    'provider': 'google',
                    'id_token': 'dict-tok',
                    'access_token': 'dict-at',
                    'provider_id': 'google.com',
                },
                'redirect_uri': 'omi://auth/callback',
            }
        )
        request = MagicMock()

        with patch('routers.auth.get_auth_code', return_value=code_data), patch('routers.auth.delete_auth_code'):
            result = asyncio.get_event_loop().run_until_complete(
                auth_token(
                    request=request,
                    grant_type='authorization_code',
                    code='c',
                    redirect_uri='omi://auth/callback',
                    use_custom_token=False,
                )
            )
            assert result['provider'] == 'google'
            assert result['id_token'] == 'dict-tok'

    def test_token_rejects_new_format_without_redirect_uri(self):
        """New-format auth code (has 'credentials' key) must include redirect_uri — fail closed."""
        from routers.auth import auth_token
        import asyncio

        code_data = json.dumps(
            {
                'credentials': json.dumps(
                    {'provider': 'google', 'id_token': 't', 'access_token': 'a', 'provider_id': 'google.com'}
                ),
                # redirect_uri intentionally missing
            }
        )
        request = MagicMock()
        with patch('routers.auth.get_auth_code', return_value=code_data), patch('routers.auth.delete_auth_code'):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    auth_token(
                        request=request,
                        grant_type='authorization_code',
                        code='c',
                        redirect_uri='omi://auth/callback',
                        use_custom_token=False,
                    )
                )
            assert exc_info.value.status_code == 400
            assert 'malformed' in exc_info.value.detail.lower()

    def test_token_rejects_unsupported_grant_type(self):
        """Non-authorization_code grant type returns 400."""
        from routers.auth import auth_token
        import asyncio

        request = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                auth_token(
                    request=request,
                    grant_type='client_credentials',
                    code='c',
                    redirect_uri='omi://auth/callback',
                    use_custom_token=False,
                )
            )
        assert exc_info.value.status_code == 400
