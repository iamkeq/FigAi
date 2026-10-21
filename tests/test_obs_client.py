from __future__ import annotations

import pytest

from figai.obs.client import ObsError, build_client_factory, get_status, response_to_dict


class FakeResponse:
    def __init__(self, **kwargs) -> None:
        self._fields = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

    def attrs(self) -> list[str]:
        return list(self._fields)


class FakeObsClient:
    def get_record_status(self):
        return FakeResponse(output_active=True)

    def get_stream_status(self):
        return FakeResponse(output_active=False)

    def get_current_program_scene(self):
        return FakeResponse(current_program_scene_name="Main")

    def get_scene_list(self):
        return FakeResponse(scenes=[{"sceneName": "Main"}, {"sceneName": "BRB"}])


def test_response_to_dict_uses_attrs_method() -> None:
    assert response_to_dict(FakeResponse(output_active=True)) == {"output_active": True}


def test_get_status_combines_record_stream_and_scene_info() -> None:
    status = get_status(FakeObsClient())

    assert status == {
        "recording": True,
        "streaming": False,
        "current_scene": "Main",
        "scenes": ["Main", "BRB"],
    }


def test_build_client_factory_raises_obs_error_when_not_configured() -> None:
    class FakeSettings:
        obs = None

    class FakeConfig:
        settings = FakeSettings()

    factory = build_client_factory(FakeConfig())

    with pytest.raises(ObsError):
        factory()
