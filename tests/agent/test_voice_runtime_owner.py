import threading

import pytest

from atlas_agent.voice_runtime_owner import (
    VoiceRuntimeOwner,
)


class FakeBundle:
    def __init__(self):
        self.close_count = 0

    def close(self):
        self.close_count += 1


class FakeController:
    def __init__(self, bundle):
        self.bundle = bundle
        self.calls = []

    def handle_goal(
        self,
        goal,
        *,
        source="voice",
    ):
        call = {
            "goal": goal,
            "source": source,
        }
        self.calls.append(call)
        return call

    def close(self):
        self.bundle.close()


def make_owner():
    bundles = []
    controllers = []

    def build_bundle():
        bundle = FakeBundle()
        bundles.append(bundle)
        return bundle

    def build_controller(bundle):
        controller = FakeController(bundle)
        controllers.append(controller)
        return controller

    owner = VoiceRuntimeOwner(
        build_bundle,
        controller_factory=build_controller,
    )
    return owner, bundles, controllers


def test_construction_is_lazy():
    owner, bundles, controllers = make_owner()

    assert owner.initialized is False
    assert owner.closed is False
    assert bundles == []
    assert controllers == []

    owner.close()

    assert owner.closed is True
    assert bundles == []
    assert controllers == []


def test_voice_and_phone_reuse_one_controller():
    owner, bundles, controllers = make_owner()

    voice_result = owner.handle_goal(
        "Find my newest Atlas file.",
        source="voice",
    )
    phone_result = owner.handle_goal(
        "Show the active PC apps.",
        source="phone",
    )

    assert len(bundles) == 1
    assert len(controllers) == 1
    assert owner.initialized is True
    assert voice_result == {
        "goal": "Find my newest Atlas file.",
        "source": "voice",
    }
    assert phone_result == {
        "goal": "Show the active PC apps.",
        "source": "phone",
    }
    assert controllers[0].calls == [
        voice_result,
        phone_result,
    ]

    owner.close()


def test_concurrent_requests_build_only_once():
    build_count = 0
    build_count_lock = threading.Lock()
    controller = None

    def build_bundle():
        nonlocal build_count

        with build_count_lock:
            build_count += 1

        return FakeBundle()

    def build_controller(bundle):
        nonlocal controller
        controller = FakeController(bundle)
        return controller

    owner = VoiceRuntimeOwner(
        build_bundle,
        controller_factory=build_controller,
    )
    barrier = threading.Barrier(8)
    errors = []

    def worker(position):
        try:
            barrier.wait(timeout=2)
            owner.handle_goal(
                f"goal-{position}",
                source=(
                    "voice"
                    if position % 2 == 0
                    else "phone"
                ),
            )
        except Exception as error:
            errors.append(error)

    threads = [
        threading.Thread(
            target=worker,
            args=(position,),
        )
        for position in range(8)
    ]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=3)

    assert errors == []
    assert all(
        not thread.is_alive()
        for thread in threads
    )
    assert build_count == 1
    assert controller is not None
    assert len(controller.calls) == 8

    owner.close()


def test_close_is_idempotent_and_blocks_reuse():
    owner, bundles, controllers = make_owner()

    owner.handle_goal("Check the PC.")
    owner.close()
    owner.close()

    assert len(bundles) == 1
    assert len(controllers) == 1
    assert bundles[0].close_count == 1
    assert owner.initialized is False
    assert owner.closed is True

    with pytest.raises(
        RuntimeError,
        match="voice runtime owner is closed",
    ):
        owner.handle_goal("Try again.")


