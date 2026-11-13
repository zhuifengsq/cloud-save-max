import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.infrastructure.persistence.models.tmdb_setting import TMDBSetting


@pytest.fixture
def db():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    TMDBSetting.__table__.create(engine)
    with Session(engine) as session:
        session.add(TMDBSetting(id=1, config_json=json.dumps({'api_key': 'test-key'})))
        session.commit()
        yield session
    engine.dispose()


@pytest.mark.parametrize('media_type', ['tv', 'movie'])
def test_images_client_does_not_filter_out_other_languages(monkeypatch, media_type):
    from app.infrastructure.http.tmdb_client import TMDBClient
    client = TMDBClient('test-key')
    calls = []
    monkeypatch.setattr(client, '_make_request', lambda endpoint, params: calls.append((endpoint, params)) or {})
    try:
        client.get_images(media_type, 12)
        assert calls == [(f'/{media_type}/12/images', {'language': None})]
    finally:
        client.session.close()


def patch_client(monkeypatch, db, payload):
    from app.application import tmdb_images
    calls = []

    class Client:
        def __init__(self, api_key, *, language):
            assert api_key == 'test-key'
            self.session = self

        def get_images(self, media_type, tmdb_id):
            assert not db.in_transaction(), 'TMDB HTTP must not hold a database transaction'
            calls.append((media_type, tmdb_id))
            return payload

        def close(self):
            calls.append('closed')

    monkeypatch.setattr(tmdb_images, 'TMDBClient', Client)
    return calls


def api_client(db):
    from app.api.routes import media_discovery
    from app.application.container import get_db
    from app.core.errors import ApiError
    from app.core.exception_handlers import api_error_handler
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(media_discovery.router, prefix='/media')
    app.dependency_overrides[get_db] = lambda: db
    for route in media_discovery.router.routes:
        for dependency in route.dependencies:
            app.dependency_overrides[dependency.dependency] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize('media_type', ['tv', 'movie'])
def test_image_api_returns_only_safe_image_metadata(db, monkeypatch, media_type):
    calls = patch_client(monkeypatch, db, {
        'posters': [
            {'file_path': '/poster.jpg', 'width': 600, 'height': 900, 'iso_639_1': 'zh', 'unused': 'ignored'},
            {'file_path': 'https://other.invalid/image.jpg', 'width': 600, 'height': 900},
            {'file_path': '/invalid.jpg', 'width': 0, 'height': 900},
            {'file_path': '/poster.jpg', 'width': 600, 'height': 900},
        ],
        'backdrops': [{'file_path': '/wide.png', 'width': 1920, 'height': 1080, 'iso_639_1': None}],
        'logos': [{'file_path': '/logo.png'}],
    })
    with api_client(db) as client:
        response = client.get(f'/media/{media_type}/12/images')
    assert response.status_code == 200, response.text
    assert response.json() == {
        'posters': [{'file_path': '/poster.jpg', 'width': 600, 'height': 900, 'language': 'zh'}],
        'backdrops': [{'file_path': '/wide.png', 'width': 1920, 'height': 1080, 'language': None}],
    }
    assert calls == [(media_type, 12), 'closed']


@pytest.mark.parametrize('payload', [None, {}, {'posters': 'invalid', 'backdrops': []}])
def test_upstream_failure_is_not_reported_as_no_images(db, monkeypatch, payload):
    patch_client(monkeypatch, db, payload)
    with api_client(db) as client:
        response = client.get('/media/tv/12/images')
    assert response.status_code == 502
    assert response.json()['code'] == 'TMDB_IMAGES_UNAVAILABLE'


def test_valid_empty_gallery_and_unconfigured_are_distinct(db, monkeypatch):
    calls = patch_client(monkeypatch, db, {'posters': [], 'backdrops': []})
    with api_client(db) as client:
        response = client.get('/media/tv/12/images')
        assert response.status_code == 200
        assert response.json() == {'posters': [], 'backdrops': []}
        setting = db.get(TMDBSetting, 1)
        setting.config_json = '{}'
        db.commit()
        response = client.get('/media/tv/12/images')
        assert response.status_code == 503
        assert response.json()['code'] == 'TMDB_NOT_CONFIGURED'
    assert calls == [('tv', 12), 'closed']


@pytest.mark.parametrize('path', ['/person/12/images', '/tv/0/images', '/tv/-1/images'])
def test_image_route_validates_type_and_id(db, path):
    with api_client(db) as client:
        assert client.get('/media' + path).status_code == 422
