import pytest

from bot import scraping


class DummyResponse:
    def __init__(self, *, text: str = "", content: bytes = b""):
        self.text = text
        self.content = content

    def raise_for_status(self) -> None:
        return None


class DummyClient:
    def __init__(self, response: DummyResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str):
        return self._response


@pytest.mark.asyncio
async def test_fetch_page_text_strips_scripts(monkeypatch):
    html = """
        <html>
            <head><script>console.log('hidden')</script></head>
            <body><h1>Producer</h1><p>Remote role</p></body>
        </html>
    """
    response = DummyResponse(text=html)

    def fake_client(*args, **kwargs):
        return DummyClient(response)

    monkeypatch.setattr(scraping.httpx, "AsyncClient", fake_client)

    text = await scraping.fetch_page_text(
        "https://jobs.example.com",
        timeout=5,
        max_bytes=1000,
    )
    assert "console.log" not in text
    assert "Producer" in text


@pytest.mark.asyncio
async def test_fetch_image_bytes_returns_content(monkeypatch):
    payload = b"image-bytes"
    response = DummyResponse(content=payload)

    def fake_client(*args, **kwargs):
        return DummyClient(response)

    monkeypatch.setattr(scraping.httpx, "AsyncClient", fake_client)

    content = await scraping.fetch_image_bytes(
        "https://cdn.example.com/poster.png",
        timeout=5,
        max_bytes=1024,
    )
    assert content == payload


@pytest.mark.asyncio
async def test_fetch_image_bytes_returns_none_when_too_large(monkeypatch):
    payload = b"x" * 10
    response = DummyResponse(content=payload)

    def fake_client(*args, **kwargs):
        return DummyClient(response)

    monkeypatch.setattr(scraping.httpx, "AsyncClient", fake_client)

    content = await scraping.fetch_image_bytes(
        "https://cdn.example.com/poster.png",
        timeout=5,
        max_bytes=5,
    )
    assert content is None
