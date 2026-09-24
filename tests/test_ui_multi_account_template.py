from __future__ import annotations

from hh_applicant_tool.ui import TEMPLATES_DIR


def test_multi_account_controls_are_wired_to_js_api():
    html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    js = (TEMPLATES_DIR / "js" / "app.js").read_text(encoding="utf-8")

    assert 'id="profile-select"' in html
    assert "switchProfile(this.value)" in html
    assert "createProfile()" in html
    assert "deleteCurrentProfile()" in html
    assert "Переавторизоваться" in html

    assert "pywebview.api.get_profiles()" in js
    assert "pywebview.api.switch_profile(profileId)" in js
    assert "pywebview.api.create_profile(profileId)" in js
    assert "pywebview.api.delete_profile(profileId)" in js



def test_captcha_ai_settings_are_exposed_in_ui():
    html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")

    assert "AI для решения CAPTCHA" in html
    assert 'data-config-key="openai_captcha.api_key"' in html
    assert 'data-config-key="openai_captcha.base_url"' in html
    assert 'data-config-key="openai_captcha.model"' in html
    assert 'data-config-secret="true"' in html
    assert "vision-модель" in html



def test_resume_metrics_are_lazy_loaded_after_startup():
    js = (TEMPLATES_DIR / "js" / "app.js").read_text(encoding="utf-8")

    assert "loadResumes(false);" in js
    assert "pywebview.api.get_resume_metrics()" in js
    assert "async function loadResumes(loadMetrics = true)" in js
    assert "void loadResumeMetrics(resumes, generation);" in js



def test_resume_metric_labels_are_unambiguous():
    js = (TEMPLATES_DIR / "js" / "app.js").read_text(encoding="utf-8")

    assert "История откликов:" in js
    assert "Приглашения за 7 дней:" in js
    assert "Просмотры всего:" in js
    assert "Просмотры за 7 дней:" in js
    assert "Показы за 7 дней:" in js
    assert "откликов/приглашений синхронизировано" not in js
