import pytest

from tests.pack_fixtures import test_library


@pytest.fixture(autouse=True)
def test_only_default_pack_library(monkeypatch):
    # Game-engine tests use a test fixture; production never loads packs from files.
    monkeypatch.setattr("app.models.game.room.load_default_library", test_library)
    monkeypatch.setattr("app.models.game.session.load_default_library", test_library)
    monkeypatch.setattr("app.models.services.room_service.load_default_library", test_library)
