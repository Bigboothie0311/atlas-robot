"""Spoken 'run diagnostics' uses structured checks and drives the HUD."""
import listen_and_answer


def test_run_diagnostics_command_reports_and_updates_hud(
    monkeypatch,
):
    import diagnostics

    findings = [
        {
            "component": "services",
            "ok": True,
            "detail": "all 5 services active",
        },
        {
            "component": "camera",
            "ok": False,
            "detail": "no camera device connected",
        },
    ]
    monkeypatch.setattr(
        diagnostics,
        "run_structured_checks",
        lambda components=None: findings,
    )

    posts = []

    def fake_post(url, json=None, timeout=None):
        posts.append({"url": url, "json": json})

        class _Response:
            status_code = 200

        return _Response()

    monkeypatch.setattr(
        listen_and_answer.requests, "post", fake_post
    )

    answer = listen_and_answer.run_diagnostics_command()

    assert "camera" in answer
    assert "no camera device connected" in answer

    report_posts = [
        post
        for post in posts
        if post["url"].endswith("/diagnostics_report")
    ]
    assert len(report_posts) == 1
    assert report_posts[0]["json"]["findings"] == findings


def test_run_diagnostics_command_survives_hub_outage(
    monkeypatch,
):
    import diagnostics
    import requests as requests_module

    monkeypatch.setattr(
        diagnostics,
        "run_structured_checks",
        lambda components=None: [
            {
                "component": "services",
                "ok": True,
                "detail": "all 5 services active",
            },
        ],
    )

    def failing_post(url, json=None, timeout=None):
        raise requests_module.ConnectionError("hub down")

    monkeypatch.setattr(
        listen_and_answer.requests, "post", failing_post
    )

    answer = listen_and_answer.run_diagnostics_command()

    assert "nominal" in answer.lower() or "1" in answer


def test_diagnostics_phrases_route_to_structured_command():
    handler = listen_and_answer.DIAGNOSTIC_CAPABILITY_HANDLERS[
        "diagnostics"
    ]

    assert handler is listen_and_answer.run_diagnostics_command
