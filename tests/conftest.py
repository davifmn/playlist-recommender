import pytest

from recommender.core.storage import LocalStore
from recommender.trainer.sample_data import generate_csv


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path)


@pytest.fixture
def seeded_store(store):
    store.put("datasets/synthetic_playlists.csv", generate_csv(600, seed=7))
    return store
