import httpx
import pytest

from casefile.bootstrap.api import create_app
from casefile.bootstrap.runtime import create_memory_runtime
from casefile.bootstrap.server import combined_app, runtime_context


@pytest.mark.asyncio
async def test_demo_server_lifespan_and_authenticated_http_boundary() -> None:
    async with runtime_context() as runtime:
        app = combined_app(runtime)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost"
            ) as client,
        ):
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/identity")).json()["demo"] is True
            assert (
                await client.get("/cases", headers={"host": "rebinding.invalid"})
            ).status_code == 400
    secure = create_app(
        await create_memory_runtime(seed=False), background_worker=False, access_token="test-token"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=secure), base_url="http://localhost"
    ) as client:
        assert (await client.get("/cases")).status_code == 401
        assert (await client.get("/health")).status_code == 200
        assert (
            await client.get("/cases", headers={"authorization": "Bearer test-token"})
        ).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value"), [("CASEFILE_TOOLS", "unknown"), ("CASEFILE_LANGUAGE_MODEL", "unknown")]
)
async def test_invalid_runtime_configuration_is_not_silently_ignored(
    key: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        async with runtime_context():
            pass
