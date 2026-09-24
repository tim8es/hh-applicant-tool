"""Тесты для Api — Python↔JS моста в UI.

Api оборачивает HHApplicantTool и предоставляет методы, вызываемые
из JavaScript через pywebview.api.*. Каждый метод возвращает
сериализуемый dict/list/str, который pywebview передаёт в JS как Promise.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from hh_applicant_tool.storage import StorageFacade
from hh_applicant_tool.ui.api import Api


class MockConfig(dict):
    """Мок Config — dict с методом save(), как настоящий Config."""

    def save(self, **kwargs):
        self.update(kwargs)


@pytest.fixture
def mock_tool():
    """Мок HHApplicantTool с реальным StorageFacade (in-memory SQLite)."""
    tool = MagicMock()
    tool.config = MockConfig({
        "client_id": "test_id",
        "client_secret": "secret_123",
        "token": {"access_token": "tok_abc", "refresh_token": "ref_xyz"},
        "proxy_url": "socks5://user:pass@localhost:1080",
        "openai_cover_letter": {
            "api_key": "sk-test-00000000000000000000",
            "base_url": "https://api.openai.com",
            "model": "gpt-4",
        },
        "openai_captcha": {
            "api_key": "sk-captcha-test",
            "base_url": "https://api.openai.com/v1/chat/completions",
            "model": "gpt-4o-mini",
        },
        "smtp": {
            "host": "smtp.example.com",
            "port": 587,
            "user": "me@example.com",
            "password": "smtp-secret-pass",
        },
    })
    tool.get_resumes.return_value = [
        {
            "id": "res1",
            "title": "Python Dev",
            "status": {"name": "published"},
            "total_views": 27,
            "new_views": 3,
        },
        {
            "id": "res2",
            "title": "Go Dev",
            "status": {"name": "blocked"},
            "total_views": 9,
            "new_views": 1,
        },
    ]
    tool.get_me.return_value = {
        "auth_type": "applicant",
        "first_name": "Иван",
        "last_name": "Петров",
        "email": "test@example.com",
    }
    tool.get_resume_statistics.return_value = {}
    # Реальный storage для тестирования пресетов через Api
    conn = sqlite3.connect(":memory:")
    tool.storage = StorageFacade(conn)
    return tool


@pytest.fixture
def api(mock_tool):
    return Api(mock_tool)


class TestGetStatus:
    def test_authorized(self, api):
        status = api.get_status()
        assert status["authorized"] is True
        assert status["user"]["first_name"] == "Иван"

    def test_unauthorized_when_get_me_fails(self, api, mock_tool):
        mock_tool.get_me.side_effect = Exception("no token")
        status = api.get_status()
        assert status["authorized"] is False
        assert status["user"] is None

    def test_rejects_employer_token_and_clears_it(self, api, mock_tool):
        mock_tool.get_me.return_value = {
            "auth_type": "employer",
            "first_name": "Иван",
            "last_name": "Петров",
        }
        mock_tool.api_client.access_token = "USER employer-token"
        mock_tool.api_client.refresh_token = "refresh-token"
        mock_tool.api_client.access_expires_at = 123

        status = api.get_status()

        assert status == {
            "authorized": False,
            "user": None,
            "reason": "wrong_role",
        }
        assert mock_tool.config["token"] == {}
        assert mock_tool.api_client.access_token is None
        assert mock_tool.api_client.refresh_token is None
        assert mock_tool.api_client.access_expires_at == 0


class TestGetResumes:
    def test_returns_list_fast_without_web_statistics(self, api, mock_tool):
        resumes = api.get_resumes()

        assert len(resumes) == 2
        assert resumes[0]["id"] == "res1"
        assert resumes[0]["counters"]["total_views"] == 27
        assert resumes[0]["counters"]["new_views"] == 3
        assert resumes[1]["title"] == "Go Dev"
        mock_tool.get_resume_statistics.assert_not_called()
        mock_tool.api_client.get.assert_not_called()

    def test_returns_empty_on_error(self, api, mock_tool):
        mock_tool.get_resumes.side_effect = Exception("network error")
        assert api.get_resumes() == []

    def test_adds_negotiation_count_without_web_request(
        self,
        api,
        mock_tool,
    ):
        mock_tool.storage.negotiations.conn.execute(
            """
            INSERT INTO negotiations
                (id, state, vacancy_id, chat_id, resume_id)
            VALUES
                (1, 'response', 101, 1001, 'res1'),
                (2, 'discard', 102, 1002, 'res1')
            """
        )
        mock_tool.storage.negotiations.conn.commit()

        resumes = api.get_resumes()

        assert resumes[0]["negotiations_count"] == 2
        assert resumes[1]["negotiations_count"] == 0
        mock_tool.get_resume_statistics.assert_not_called()

    def test_resume_metrics_are_loaded_separately(
        self,
        api,
        mock_tool,
    ):
        mock_tool.get_resume_statistics.return_value = {
            "res1": {
                "views": 12,
                "new_views": 3,
                "invitations": 4,
                "new_invitations": 1,
                "search_shows": 50,
            }
        }

        metrics = api.get_resume_metrics()

        assert metrics == {
            "res1": {
                "views_7d": 12,
                "new_views_7d": 3,
                "invitations": 4,
                "new_invitations": 1,
                "search_shows": 50,
            }
        }

class TestConfig:
    def test_get_config_masks_top_level_secrets(self, api):
        """client_secret, token, proxy_url замаскированы на top-level."""
        config = api.get_config()
        assert config["client_secret"] == "***"
        assert config["token"] == "***"
        assert config["proxy_url"] == "***"
        # Публичные ключи видны
        assert config["client_id"] == "test_id"

    def test_get_config_masks_nested_secrets(self, api):
        """Вложенные api_key, password маскируются рекурсивно."""
        config = api.get_config()
        assert config["openai_cover_letter"]["api_key"] == "***"
        assert config["openai_captcha"]["api_key"] == "***"
        assert config["openai_captcha"]["model"] == "gpt-4o-mini"
        # Несекретные поля внутри вложенного dict остаются видны
        assert config["openai_cover_letter"]["base_url"] == "https://api.openai.com"
        assert config["openai_cover_letter"]["model"] == "gpt-4"
        assert config["smtp"]["password"] == "***"
        assert config["smtp"]["host"] == "smtp.example.com"
        assert config["smtp"]["user"] == "me@example.com"

    def test_save_config_ignores_top_level_masked_keys(self, api, mock_tool):
        """Нельзя перезаписать client_secret и token через save_config."""
        original_secret = mock_tool.config["client_secret"]
        original_token = mock_tool.config["token"]
        api.save_config({
            "client_id": "new_id",
            "client_secret": "hacked",
            "token": {"access_token": "stolen"},
        })
        assert mock_tool.config["client_secret"] == original_secret
        assert mock_tool.config["token"] == original_token
        # Несекретное поле обновилось
        assert mock_tool.config["client_id"] == "new_id"

    def test_save_config_strips_mask_value(self, api, mock_tool):
        """Значение "***" отбрасывается — нельзя перезаписать секрет маской."""
        original_proxy = mock_tool.config["proxy_url"]
        api.save_config({"proxy_url": "***"})
        assert mock_tool.config["proxy_url"] == original_proxy

    def test_save_config_strips_nested_mask(self, api, mock_tool):
        captured = {}
        mock_tool.config.save = lambda **kw: captured.update(kw)
        api.save_config({
            "openai_cover_letter": {
                "api_key": "***",
                "model": "gpt-4-turbo",
            }
        })
        # api_key="***" stripped — existing key preserved via merge, model updated
        assert captured["openai_cover_letter"]["model"] == "gpt-4-turbo"
        assert captured["openai_cover_letter"]["api_key"] == "sk-test-00000000000000000000"

    def test_save_config_preserves_omitted_nested_secret(self, api, mock_tool):
        api.save_config({
            "openai_cover_letter": {
                "model": "gpt-4.1",
            }
        })
        assert mock_tool.config["openai_cover_letter"]["api_key"] == "sk-test-00000000000000000000"
        assert mock_tool.config["openai_cover_letter"]["model"] == "gpt-4.1"

    def test_save_config_updates_captcha_model_without_erasing_key(
        self,
        api,
        mock_tool,
    ):
        api.save_config({
            "openai_captcha": {
                "model": "vision-model-v2",
            }
        })

        assert mock_tool.config["openai_captcha"]["api_key"] == "sk-captcha-test"
        assert mock_tool.config["openai_captcha"]["model"] == "vision-model-v2"

    def test_save_config_preserves_value_types(self, api, mock_tool):
        api.save_config({
            "smtp": {
                "port": 2525,
                "ssl": True,
            }
        })
        assert mock_tool.config["smtp"]["port"] == 2525
        assert mock_tool.config["smtp"]["ssl"] is True

    def test_save_config_returns_ok(self, api):
        result = api.save_config({"client_id": "x"})
        assert result["status"] == "ok"

    def test_save_config_returns_error_on_failure(self, api, mock_tool):
        mock_tool.config.save = MagicMock(side_effect=IOError("permission denied"))
        result = api.save_config({"client_id": "x"})
        assert result["status"] == "error"


class TestPresetsMethods:
    """Проверяем что Api корректно проксирует вызовы в PresetsManager."""

    def test_save_and_list(self, api):
        result = api.save_preset(
            "my_search", {"search": "python", "salary": 200000}
        )
        assert result == {"status": "ok"}
        names = api.list_presets()
        assert "my_search" in names

    def test_load_preset(self, api):
        api.save_preset("p1", {"search": "go"})
        loaded = api.load_preset("p1")
        assert loaded == {"search": "go"}

    def test_delete_preset(self, api):
        api.save_preset("del_me", {"search": "x"})
        api.delete_preset("del_me")
        assert "del_me" not in api.list_presets()

    def test_last_used_initially_none(self, api):
        assert api.get_last_used_params() is None

    def test_save_and_get_last_used(self, api):
        params = {"search": "rust", "area": ["1"]}
        api.save_last_used_params(params)
        assert api.get_last_used_params() == params

    def test_save_preset_rejects_empty_name(self, api):
        result = api.save_preset("", {"search": "x"})
        assert result["status"] == "error"
        assert "message" in result

    def test_save_preset_rejects_name_with_colon(self, api):
        result = api.save_preset("a:b", {"search": "x"})
        assert result["status"] == "error"

    def test_save_preset_rejects_oversized_params(self, api):
        big = {"x": "a" * (65 * 1024)}
        result = api.save_preset("big", big)
        assert result["status"] == "error"

    def test_save_last_used_swallows_invalid(self, api):
        """save_last_used не должен падать при невалидных данных."""
        big = {"x": "a" * (65 * 1024)}
        api.save_last_used_params(big)
        # last_used остался пустым, исключение не поднялось
        assert api.get_last_used_params() is None


class TestErrorMessages:
    """Клиентский код не должен получать внутренние детали исключений."""

    def test_refresh_negotiations_generic_message(self, api, mock_tool):
        mock_tool.get_negotiations.side_effect = Exception(
            "internal path /etc/secret leaked"
        )
        result = api.refresh_negotiations("active")
        assert result["status"] == "error"
        assert "/etc/secret" not in result["message"]
        assert "leaked" not in result["message"]

    def test_apply_vacancies_generic_message_on_failure(self, api, mock_tool):
        """При внутренней ошибке наружу идёт generic-сообщение, не str(e)."""
        # Форсим ошибку через невалидные argv, которые вызовут SystemExit
        # внутри argparse → Exception путь в apply_vacancies
        mock_tool.get_resumes.return_value = []
        # Невалидный параметр вызовет ошибку argparse / Namespace
        result = api.apply_vacancies({"nonexistent_flag_xyz": "leak /root/.ssh"})
        # Либо отработал, либо упал с generic message
        if result["status"] == "error":
            assert "/root/.ssh" not in result.get("message", "")


class TestApplyVacancies:
    """Тесты интеграции apply_vacancies через Api."""

    def test_params_to_argv_simple(self, api):
        """Конвертация dict → CLI argv."""
        argv = api._params_to_argv({"search": "python", "salary": 200000})
        assert "--search" in argv
        assert "python" in argv
        assert "--salary" in argv
        assert "200000" in argv

    def test_params_to_argv_bool_true(self, api):
        argv = api._params_to_argv({"dry_run": True})
        assert "--dry-run" in argv

    def test_params_to_argv_bool_false_skipped(self, api):
        argv = api._params_to_argv({"dry_run": False})
        assert "--dry-run" not in argv

    def test_params_to_argv_none_skipped(self, api):
        argv = api._params_to_argv({"salary": None})
        assert argv == []

    def test_params_to_argv_list(self, api):
        argv = api._params_to_argv({"area": ["1", "2"]})
        # nargs="+" expects: --area 1 2 (single flag, multiple values)
        assert argv.count("--area") == 1
        assert argv == ["--area", "1", "2"]

    def test_params_to_argv_empty_list_skipped(self, api):
        argv = api._params_to_argv({"area": []})
        assert argv == []

    def test_apply_saves_last_used(self, api, mock_tool):
        """apply_vacancies должен сохранять параметры как last_used."""
        # Подменяем run чтобы не выполнять реальную операцию
        mock_tool.get_resumes.return_value = []
        params = {"search": "python", "dry_run": True}
        # Вызов apply_vacancies (может упасть на реальной операции —
        # нам важно что last_used сохраняется ДО выполнения)
        api.apply_vacancies(params)
        assert api.get_last_used_params() == params

    def test_apply_returns_dict_with_status(self, api, mock_tool):
        """apply_vacancies всегда возвращает dict с ключом status."""
        result = api.apply_vacancies({"search": "test", "dry_run": True})
        assert "status" in result


class TestRefreshNegotiations:
    """Синхронизация откликов с hh.ru через refresh_negotiations."""

    def test_sync_all_does_not_force_active_status(self, api, mock_tool):
        mock_tool.get_negotiations.return_value = []

        result = api.refresh_negotiations()

        assert result == {"status": "ok", "count": 0}
        mock_tool.get_negotiations.assert_called_once_with(None)

    def test_saves_api_items_to_db(self, api, mock_tool):
        """Отклики hh.ru (raw dict'ы) сохраняются в БД, возвращается count.

        Структура item'ов повторяет ответ hh.ru /negotiations
        (api.datatypes.Negotiation): NegotiationModel.from_api берёт id, chat_id,
        state.id, vacancy.id, vacancy.employer.id, resume.id.
        """
        item1 = {
            "id": "1234567890",
            "state": {"id": "active", "name": "Активный"},
            "created_at": "2026-08-02T10:00:00+03:00",
            "updated_at": "2026-08-02T12:00:00+03:00",
            "resume": {
                "id": "res1",
                "title": "Python Developer",
                "url": "https://hh.ru/resume/res1",
                "alternate_url": "https://hh.ru/resume/res1",
            },
            "viewed_by_opponent": False,
            "has_updates": False,
            "messages_url": "https://hh.ru/messages/1234567890",
            "url": "https://hh.ru/negotiations/1234567890",
            "counters": {"messages": 1, "unread_messages": 0},
            "chat_states": {"response_reminder_state": {"allowed": False}},
            "source": "https://hh.ru/vacancy/111",
            "chat_id": 987654321,
            "messaging_status": "ok",
            "decline_allowed": True,
            "read": True,
            "has_new_messages": False,
            "applicant_question_state": False,
            "hidden": False,
            "vacancy": {
                "id": "111",
                "premium": False,
                "name": "Python разработчик",
                "department": None,
                "has_test": False,
                "response_letter_required": False,
                "area": {"id": "1", "name": "Москва"},
                "salary": None,
                "salary_range": None,
                "type": {"id": "open", "name": "Открытая"},
                "address": None,
                "response_url": None,
                "sort_point_distance": None,
                "published_at": "2026-07-30T10:00:00+03:00",
                "created_at": "2026-07-30T10:00:00+03:00",
                "archived": False,
                "apply_alternate_url": (
                    "https://hh.ru/applicant/vacancy_response?vacancyId=111"
                ),
                "show_contacts": False,
                "benefits": [],
                "insider_interview": None,
                "url": "https://hh.ru/vacancy/111",
                "alternate_url": "https://hh.ru/vacancy/111",
                "professional_roles": [{"id": "96", "name": "Программист"}],
                "employer": {
                    "id": "777",
                    "name": "ООО Ромашка",
                    "url": "https://hh.ru/employer/777",
                    "alternate_url": "https://hh.ru/employer/777",
                    "logo_urls": None,
                    "vacancies_url": "https://hh.ru/employer/777/vacancies",
                    "accredited_it_employer": False,
                    "trusted": False,
                },
                "show_logo_in_search": None,
            },
            "tags": [],
        }
        item2 = {
            "id": "2233445566",
            "state": {"id": "invitation", "name": "Приглашение"},
            "created_at": "2026-08-01T10:00:00+03:00",
            "updated_at": "2026-08-01T11:00:00+03:00",
            "resume": {
                "id": "res2",
                "title": "Go Developer",
                "url": "https://hh.ru/resume/res2",
                "alternate_url": "https://hh.ru/resume/res2",
            },
            "viewed_by_opponent": True,
            "has_updates": True,
            "messages_url": "https://hh.ru/messages/2233445566",
            "url": "https://hh.ru/negotiations/2233445566",
            "counters": {"messages": 3, "unread_messages": 1},
            "chat_states": {"response_reminder_state": {"allowed": True}},
            "source": "https://hh.ru/vacancy/222",
            "chat_id": 1122334455,
            "messaging_status": "ok",
            "decline_allowed": False,
            "read": False,
            "has_new_messages": True,
            "applicant_question_state": False,
            "hidden": False,
            "vacancy": {
                "id": "222",
                "premium": True,
                "name": "Go разработчик",
                "department": None,
                "has_test": True,
                "response_letter_required": False,
                "area": {"id": "2", "name": "Санкт-Петербург"},
                "salary": {
                    "from": 200000,
                    "to": 300000,
                    "currency": "RUR",
                    "gross": True,
                },
                "salary_range": None,
                "type": {"id": "open", "name": "Открытая"},
                "address": None,
                "response_url": None,
                "sort_point_distance": None,
                "published_at": "2026-07-29T10:00:00+03:00",
                "created_at": "2026-07-29T10:00:00+03:00",
                "archived": False,
                "apply_alternate_url": (
                    "https://hh.ru/applicant/vacancy_response?vacancyId=222"
                ),
                "show_contacts": False,
                "benefits": [],
                "insider_interview": None,
                "url": "https://hh.ru/vacancy/222",
                "alternate_url": "https://hh.ru/vacancy/222",
                "professional_roles": [{"id": "96", "name": "Программист"}],
                "employer": {
                    "id": "888",
                    "name": "ООО ТехноГо",
                    "url": "https://hh.ru/employer/888",
                    "alternate_url": "https://hh.ru/employer/888",
                    "logo_urls": None,
                    "vacancies_url": "https://hh.ru/employer/888/vacancies",
                    "accredited_it_employer": True,
                    "trusted": True,
                },
                "show_logo_in_search": None,
            },
            "tags": [],
        }
        mock_tool.get_negotiations.return_value = [item1, item2]

        result = api.refresh_negotiations()

        assert result == {"status": "ok", "count": 2}
        rows = api.get_negotiations_from_db()
        assert len(rows) == 2
        # Порядок из get_negotiations_from_db: ORDER BY created_at DESC,
        # у item1 created_at позже — он первый
        assert rows[0]["state"] == "active"
        assert rows[0]["vacancy_id"] == int(item1["vacancy"]["id"])
        assert rows[1]["state"] == "invitation"
        assert rows[1]["vacancy_id"] == int(item2["vacancy"]["id"])

    @pytest.mark.parametrize(
        "resume_value",
        [None, pytest.param("missing", id="missing-resume-field")],
    )
    def test_sync_accepts_negotiation_without_resume(
        self,
        api,
        mock_tool,
        resume_value,
    ):
        item = {
            "id": "3344556677",
            "state": {"id": "response", "name": "Отклик"},
            "created_at": "2026-08-03T10:00:00+03:00",
            "updated_at": "2026-08-03T10:00:00+03:00",
            "chat_id": 22334455,
            "vacancy": {
                "id": "333",
                "employer": {"id": "999"},
            },
        }
        if resume_value != "missing":
            item["resume"] = resume_value

        mock_tool.get_negotiations.return_value = [item]

        result = api.refresh_negotiations()

        assert result == {"status": "ok", "count": 1}
        row = mock_tool.storage.negotiations.get(int(item["id"]))
        assert row is not None
        assert row.resume_id is None
        assert row.vacancy_id == int(item["vacancy"]["id"])

    def test_sync_enriches_vacancy_employer_and_resume(
        self,
        api,
        mock_tool,
    ):
        raw = {
            "id": "4455667788",
            "state": {"id": "interview", "name": "Собеседование"},
            "created_at": "2026-08-04T10:00:00+03:00",
            "updated_at": "2026-08-04T11:00:00+03:00",
            "chat_id": 33445566,
            "vacancy": {"id": "444"},
        }
        detail = {
            "id": "4455667788",
            "resume": {"id": "res1"},
            "vacancy": {
                "id": "444",
                "name": "Product Manager",
                "alternate_url": "https://hh.ru/vacancy/444",
                "area": {"id": "2", "name": "Санкт-Петербург"},
                "employer": {
                    "id": "777",
                    "name": "ООО Тест",
                    "alternate_url": "https://hh.ru/employer/777",
                },
            },
        }
        mock_tool.get_negotiations.return_value = [raw]
        mock_tool.api_client.get.return_value = detail

        result = api.refresh_negotiations()

        assert result == {"status": "ok", "count": 1}
        rows = api.get_negotiations_from_db()
        assert rows[0]["state"] == "interview"
        assert rows[0]["resume_id"] == "res1"
        assert rows[0]["vacancy_name"] == "Product Manager"
        assert rows[0]["vacancy_url"] == "https://hh.ru/vacancy/444"
        assert rows[0]["employer_name"] == "ООО Тест"


class TestProfiles:
    def test_get_profiles_returns_active_profile(self, api):
        api._profiles = MagicMock()
        api._profiles.active_profile_id = "work"
        api._profiles.list_profiles.return_value = [
            {"id": ".", "name": "Основной", "active": False, "has_token": True},
            {"id": "work", "name": "work", "active": True, "has_token": False},
        ]

        result = api.get_profiles()

        assert result["active_profile_id"] == "work"
        assert result["profiles"][1]["active"] is True

    def test_switch_profile_resets_profile_context(self, api):
        api._profiles = MagicMock()
        api._profiles.switch_profile.return_value = "work"

        result = api.switch_profile("work")

        assert result == {"status": "ok", "active_profile_id": "work"}
        api._profiles.switch_profile.assert_called_once_with("work")

    def test_create_profile_creates_and_activates(self, api):
        api._profiles = MagicMock()
        api._profiles.create_profile.return_value = "second"
        api._profiles.switch_profile.return_value = "second"

        result = api.create_profile("second")

        assert result == {"status": "ok", "active_profile_id": "second"}
        api._profiles.create_profile.assert_called_once_with("second")
        api._profiles.switch_profile.assert_called_once_with("second")

    def test_delete_active_profile_switches_to_default_first(self, api):
        api._profiles = MagicMock()
        api._profiles.normalize_profile_id.return_value = "work"
        api._profiles.active_profile_id = "work"

        result = api.delete_profile("work")

        assert result["status"] == "ok"
        api._profiles.switch_profile.assert_called_once_with(".")
        api._profiles.delete_profile.assert_called_once_with("work")

    def test_profile_change_is_blocked_while_apply_is_running(self, api):
        api._is_running = True

        result = api.switch_profile("work")

        assert result["status"] == "error"
        assert "выполнения операции" in result["message"]

    def test_profile_change_is_blocked_while_authorizing(self, api):
        api._auth_running = True

        result = api.create_profile("work")

        assert result["status"] == "error"
        assert "авторизации" in result["message"]


class TestStatisticsDates:
    def test_activity_uses_iso_date_prefix_with_timezone(self, api, mock_tool):
        created = datetime.now(timezone.utc) - timedelta(days=1)
        day = created.date().isoformat()
        hh_timestamp = created.strftime("%Y-%m-%dT%H:%M:%S+0300")

        conn = mock_tool.storage.negotiations.conn
        conn.execute(
            """
            INSERT INTO negotiations
                (id, state, vacancy_id, chat_id, created_at)
            VALUES
                (?, ?, ?, ?, ?)
            """,
            (991001, "response", 881001, 771001, hh_timestamp),
        )
        conn.commit()

        stats = api.get_statistics()

        assert None not in stats["daily_negotiations"]
        assert "null" not in stats["daily_negotiations"]
        assert stats["daily_negotiations"][day] == 1

    def test_skipped_activity_uses_iso_date_prefix(self, api, mock_tool):
        created = datetime.now(timezone.utc) - timedelta(days=1)
        day = created.date().isoformat()
        hh_timestamp = created.strftime("%Y-%m-%dT%H:%M:%S+0300")

        conn = mock_tool.storage.negotiations.conn
        conn.execute(
            """
            INSERT INTO skipped_vacancies
                (resume_id, vacancy_id, reason, created_at)
            VALUES
                (?, ?, ?, ?)
            """,
            ("res-stats", 881002, "ai_rejected", hh_timestamp),
        )
        conn.commit()

        stats = api.get_statistics()

        assert None not in stats["daily_skipped"]
        assert "null" not in stats["daily_skipped"]
        assert stats["daily_skipped"][day] == 1
