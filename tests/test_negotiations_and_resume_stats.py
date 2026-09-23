from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from hh_applicant_tool.main import HHApplicantTool


class _FakeApiClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, endpoint, **params):
        self.calls.append((endpoint, params))
        return self.pages[params["page"]]


def test_get_negotiations_paginates_all_without_status():
    tool = HHApplicantTool()
    client = _FakeApiClient(
        [
            {
                "items": [{"id": "1"}, {"id": "2"}],
                "page": 0,
                "pages": 2,
                "found": 3,
                "per_page": 2,
            },
            {
                "items": [{"id": "3"}],
                "page": 1,
                "pages": 2,
                "found": 3,
                "per_page": 2,
            },
        ]
    )
    tool.__dict__["api_client"] = client

    items = list(tool.get_negotiations())

    assert [item["id"] for item in items] == ["1", "2", "3"]
    assert len(client.calls) == 2
    assert "status" not in client.calls[0][1]
    assert client.calls[0][1]["per_page"] == 50


def test_get_negotiations_keeps_explicit_status_filter():
    tool = HHApplicantTool()
    client = _FakeApiClient(
        [
            {
                "items": [{"id": "1"}],
                "page": 0,
                "pages": 1,
                "found": 1,
                "per_page": 1,
            }
        ]
    )
    tool.__dict__["api_client"] = client

    assert [item["id"] for item in tool.get_negotiations("active")] == ["1"]
    assert client.calls[0][1]["status"] == "active"


def test_get_negotiations_uses_found_when_pages_missing():
    tool = HHApplicantTool()
    client = _FakeApiClient(
        [
            {
                "items": [{"id": "1"}, {"id": "2"}],
                "page": 0,
                "found": 3,
                "per_page": 2,
            },
            {
                "items": [{"id": "3"}],
                "page": 1,
                "found": 3,
                "per_page": 2,
            },
        ]
    )
    tool.__dict__["api_client"] = client

    assert [item["id"] for item in tool.get_negotiations()] == ["1", "2", "3"]


def test_resume_statistics_parses_hh_initial_state():
    tool = HHApplicantTool()
    payload = {
        "applicantResumesStatistics": {
            "resumes": {
                "res1": {
                    "statistics": {
                        "searchShows": {"count": 31},
                        "views": {"count": 12, "countNew": 3},
                        "invitations": {"count": 4, "countNew": 1},
                    }
                }
            }
        }
    }
    import html
    import json

    raw = html.escape(json.dumps(payload))
    response = SimpleNamespace(
        status_code=200,
        url="https://hh.ru/applicant/resumes",
        text=(
            '<template class="lux-state" '
            'id="HH-Lux-InitialState" data-version="2">'
            + raw
            + "</template>"
        ),
    )
    session = SimpleNamespace(get=lambda url: response)
    tool.__dict__["session"] = session

    assert tool.get_resume_statistics() == {
        "res1": {
            "views": 12,
            "new_views": 3,
            "invitations": 4,
            "new_invitations": 1,
            "search_shows": 31,
        }
    }


def test_resume_statistics_maps_ssr_id_to_resume_hash():
    tool = HHApplicantTool()
    payload = {
        "applicantResumes": [
            {
                "hash": "resume-hash",
                "_attributes": {"id": "api-resume-id"},
            }
        ],
        "applicantResumesStatistics": {
            "resumes": {
                "api-resume-id": {
                    "statistics": {
                        "searchShows": {"count": 48},
                        "views": {"count": 9},
                    }
                }
            }
        },
    }
    import html
    import json

    response = SimpleNamespace(
        status_code=200,
        url="https://hh.ru/applicant/resumes",
        text=(
            '<template id="HH-Lux-InitialState" data-extra="1">'
            + html.escape(json.dumps(payload))
            + "</template>"
        ),
    )
    tool.__dict__["session"] = SimpleNamespace(get=lambda url: response)

    stats = tool.get_resume_statistics()

    assert stats["api-resume-id"]["search_shows"] == 48
    assert stats["resume-hash"]["search_shows"] == 48


def test_resume_views_last_days_counts_only_recent_items():
    tool = HHApplicantTool()
    now = datetime.now(timezone.utc)
    pages = [
        {
            "items": [
                {"created_at": (now - timedelta(days=1)).isoformat()},
                {"created_at": (now - timedelta(days=6)).isoformat()},
            ],
            "page": 0,
            "pages": 2,
        },
        {
            "items": [
                {"created_at": (now - timedelta(days=8)).isoformat()},
            ],
            "page": 1,
            "pages": 2,
        },
    ]
    client = _FakeApiClient(pages)
    tool.__dict__["api_client"] = client

    assert tool.get_resume_views_last_days("res1", days=7) == 2
    assert len(client.calls) == 2
    assert client.calls[0][0] == "/resumes/res1/views"
