"""Secure phone link — authenticated /phone/* routes for the hub.

Everything here requires the PHONE_TOKEN (config/robot.env). This is the
ONLY surface intended to be reachable from off-network, and it must be
reached over a private overlay (Tailscale) or an authenticated tunnel —
never a raw internet port-forward. See PHONE_LINK.md.

Conversation continuity is automatic: /phone/ask records into the same
memory_store session the desk uses, so a thread started on the phone can
continue at the desk and vice-versa.

Token-cost note: /phone/ask and /phone/camera call the model on demand
(same per-question cost as a voice question). Everything else is local.
"""
import hmac

from flask import jsonify, request

import robot_config


def _token():
    return robot_config.get("PHONE_TOKEN")


def _authed():
    provided = request.headers.get("X-Phone-Token", "")
    token = _token()
    return bool(token) and hmac.compare_digest(provided, token)


def register(app, speak_text, log_qa_entry, camera_gate, hud_stats, pc_control):
    """Registers the phone routes on the hub's Flask app. Dependencies are
    injected so this module stays import-light."""

    def guard():
        if not _token():
            return jsonify({"ok": False, "error": "phone link not configured"}), 503
        if not _authed():
            return jsonify({"ok": False, "error": "invalid token"}), 401
        return None

    @app.post("/phone/ask")
    def phone_ask():
        denied = guard()
        if denied:
            return denied

        # Imported lazily — listen_and_answer pulls in heavy deps.
        import listen_and_answer

        question = str((request.get_json(silent=True) or {}).get("text", "")).strip()
        if not question:
            return jsonify({"ok": False, "error": "empty question"}), 400

        try:
            answer = listen_and_answer.answer_text_only(question)
        except Exception as error:
            return jsonify({"ok": False, "error": str(error)}), 500

        log_qa_entry(f"[phone] {question}", answer)

        # Optionally speak it at the desk too, so a conversation can hand
        # off between phone and room.
        if (request.get_json(silent=True) or {}).get("speak_at_desk"):
            try:
                speak_text(answer)
            except Exception:
                pass

        return jsonify({"ok": True, "answer": answer})

    @app.get("/phone/status")
    def phone_status():
        denied = guard()
        if denied:
            return denied

        stats = hud_stats.get_hud_stats()
        auth = camera_gate.hud_status()
        return jsonify({
            "ok": True,
            "weather": stats.get("weather"),
            "pc_online": stats.get("gaming_pc", {}).get("online"),
            "printer": stats.get("printer"),
            "unreviewed_intruders": auth.get("unreviewed_intruders"),
            "device_count": stats.get("network", {}).get("device_count"),
        })

    @app.get("/phone/events")
    def phone_events():
        denied = guard()
        if denied:
            return denied

        records = camera_gate.unreviewed_intruders()
        return jsonify({
            "ok": True,
            "count": len(records),
            "events": [
                {
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "denied_commands": [d["command"] for d in r.get("denied_commands", [])],
                }
                for r in records
            ],
        })

    @app.get("/phone/event_photo/<record_id>")
    def phone_event_photo(record_id):
        denied = guard()
        if denied:
            return denied

        from flask import send_file
        import os

        record = next(
            (r for r in camera_gate.unreviewed_intruders() if r["id"] == record_id),
            None,
        )
        if record is None or not record.get("photo") or not os.path.exists(record["photo"]):
            return jsonify({"ok": False, "error": "no such photo"}), 404
        return send_file(record["photo"])

    @app.post("/phone/camera")
    def phone_camera():
        denied = guard()
        if denied:
            return denied

        # "What does the camera see?" — capture a frame and describe it.
        # Uses the model (on-demand token cost). Runs the existing vision
        # path in a subprocess so the hub stays responsive.
        import subprocess
        import sys

        try:
            subprocess.run(
                [sys.executable, "/home/atlas/atlas-robot/vision_test.py"],
                timeout=40, check=False,
            )
            return jsonify({"ok": True, "note": "described aloud at the desk"})
        except Exception as error:
            return jsonify({"ok": False, "error": str(error)}), 500

    @app.post("/phone/pc/<action>")
    def phone_pc(action):
        denied = guard()
        if denied:
            return denied

        # Proxy a small set of approved PC actions through the companion.
        body = request.get_json(silent=True) or {}
        handlers = {
            "open_fusion": lambda: pc_control.open_fusion(),
            "screenshot": lambda: pc_control.screenshot_to_hud(),
            "youtube": lambda: pc_control.youtube_search(str(body.get("query", ""))),
            "apps": lambda: pc_control.active_apps(),
        }

        handler = handlers.get(action)
        if handler is None:
            return jsonify({"ok": False, "error": "unknown pc action"}), 404

        return jsonify({"ok": True, "result": handler()})
