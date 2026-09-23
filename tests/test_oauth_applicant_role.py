from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock

import pytest

from hh_applicant_tool.api.client import OAuthClient
from hh_applicant_tool.operations.authorize import Operation


def test_authorize_url_forces_applicant_role_and_fresh_login():
    client = OAuthClient(client_id="client-id", client_secret="client-secret")

    query = parse_qs(urlsplit(client.authorize_url).query)

    assert query["client_id"] == ["client-id"]
    assert query["response_type"] == ["code"]
    assert query["role"] == ["applicant"]
    assert query["force_role"] == ["true"]
    assert query["force_login"] == ["true"]


def test_applicant_role_validation_accepts_applicant():
    api_client = MagicMock()

    Operation._ensure_applicant_role(
        api_client,
        {"auth_type": "applicant"},
    )

    api_client.handle_access_token.assert_not_called()


def test_applicant_role_validation_rejects_employer_and_clears_token():
    api_client = MagicMock()

    with pytest.raises(ValueError, match="не как соискателя"):
        Operation._ensure_applicant_role(
            api_client,
            {"auth_type": "employer"},
        )

    api_client.handle_access_token.assert_called_once_with(
        {
            "access_token": None,
            "refresh_token": None,
            "access_expires_at": 0,
        }
    )


def test_session_cookie_from_playwright_is_not_marked_expired():
    op = Operation()
    jar = MagicMock()
    op._tool = SimpleNamespace(
        session=SimpleNamespace(cookies=jar)
    )

    op._set_session_cookies(
        [
            {
                "name": "hhtoken",
                "value": "cookie-value",
                "domain": ".hh.ru",
                "path": "/",
                "secure": True,
                "expires": -1,
                "httpOnly": True,
            }
        ]
    )

    cookie = jar.set_cookie.call_args.args[0]
    assert cookie.expires is None
    assert cookie.discard is True


def test_persistent_playwright_cookie_keeps_expiry():
    op = Operation()
    jar = MagicMock()
    op._tool = SimpleNamespace(
        session=SimpleNamespace(cookies=jar)
    )

    op._set_session_cookies(
        [
            {
                "name": "_xsrf",
                "value": "cookie-value",
                "domain": ".hh.ru",
                "path": "/",
                "secure": True,
                "expires": 1893456000,
                "httpOnly": False,
            }
        ]
    )

    cookie = jar.set_cookie.call_args.args[0]
    assert cookie.expires == 1893456000
    assert cookie.discard is False
