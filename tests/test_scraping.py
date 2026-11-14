import asyncio
import socket
import time

import httpx
import pytest

from bot import scraping

REAL_ASYNC_CLIENT = httpx.AsyncClient
PUBLIC_IP = "93.184.216.34"


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


def install_resolver(monkeypatch, mapping):
    """Replace the running loop's getaddrinfo: no real DNS in tests."""
    lookups = []

    async def fake_getaddrinfo(host, port, *args, **kwargs):
        lookups.append(host)
        if host not in mapping:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, port),
            )
            for address in mapping[host]
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
    return lookups


def install_transport(monkeypatch, handler):
    """Serve every request from ``handler`` (no real HTTP); returns the requests seen."""
    requests = []

    async def recording_handler(request: httpx.Request):
        requests.append(request)
        result = handler(request)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(recording_handler)
        return REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(scraping.httpx, "AsyncClient", factory)
    return requests


def html_response(body: str, *, status: int = 200, content_type: str = "text/html; charset=utf-8"):
    return httpx.Response(status, headers={"Content-Type": content_type}, content=body.encode("utf-8"))


@pytest.mark.asyncio
async def test_fetch_page_text_strips_scripts(monkeypatch):
    html = """
        <html>
            <head><script>console.log('hidden')</script><style>p {}</style></head>
            <body>
                <header>Site banner</header>
                <nav>Home | About</nav>
                <noscript>Enable JS</noscript>
                <main><article><header><h1>Producer</h1></header><p>Remote role</p></article></main>
                <footer>Copyright</footer>
            </body>
        </html>
    """
    install_resolver(monkeypatch, {"jobs.example.com": [PUBLIC_IP]})
    install_transport(monkeypatch, lambda request: html_response(html))

    text = await scraping.fetch_page_text("https://jobs.example.com", timeout=5, max_bytes=10_000)

    assert text is not None
    assert "Producer" in text
    assert "Remote role" in text
    for hidden in ("console.log", "p {}", "Site banner", "Home | About", "Enable JS", "Copyright"):
        assert hidden not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url, mapping",
    [
        ("http://127.0.0.1:27865/admin", {"127.0.0.1": ["127.0.0.1"]}),
        ("http://localhost:8080/", {"localhost": ["127.0.0.1", "::1"]}),
        ("http://10.0.0.5/", {"10.0.0.5": ["10.0.0.5"]}),
        ("http://192.168.1.10/", {"192.168.1.10": ["192.168.1.10"]}),
        ("http://169.254.169.254/latest/meta-data/", {"169.254.169.254": ["169.254.169.254"]}),
        ("http://[::1]:27865/", {"::1": ["::1"]}),
        ("http://[fe80::1]/", {"fe80::1": ["fe80::1"]}),
        # The OS resolver turns decimal/hex IPv4 forms into dotted quads.
        ("http://2130706433/", {"2130706433": ["127.0.0.1"]}),
        ("http://0x7f000001/", {"0x7f000001": ["127.0.0.1"]}),
        ("http://mapped.example.com/", {"mapped.example.com": ["::ffff:127.0.0.1"]}),
        # One private record is enough to refuse.
        ("https://mixed.example.com/", {"mixed.example.com": [PUBLIC_IP, "10.1.2.3"]}),
        ("https://unknown.example.com/", {}),
    ],
)
async def test_fetch_page_text_refuses_non_public_destinations(monkeypatch, url, mapping):
    lookups = install_resolver(monkeypatch, mapping)
    requests = install_transport(monkeypatch, lambda request: html_response("<p>secret</p>"))

    text = await scraping.fetch_page_text(url, timeout=5, max_bytes=10_000)

    assert text is None
    assert lookups, "the host must be resolved before any request"
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ["ftp://jobs.example.com/offer.html", "file:///etc/passwd", "gopher://jobs.example.com/"],
)
async def test_fetch_page_text_refuses_other_schemes(monkeypatch, url):
    lookups = install_resolver(monkeypatch, {"jobs.example.com": [PUBLIC_IP]})
    requests = install_transport(monkeypatch, lambda request: html_response("<p>x</p>"))

    assert await scraping.fetch_page_text(url, timeout=5, max_bytes=10_000) is None
    assert lookups == []
    assert requests == []


@pytest.mark.asyncio
async def test_redirect_to_private_address_is_refused(monkeypatch):
    install_resolver(
        monkeypatch,
        {"jobs.example.com": [PUBLIC_IP], "127.0.0.1": ["127.0.0.1"]},
    )

    def handler(request):
        if request.url.host == "jobs.example.com":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:27865/admin"})
        return html_response("<p>internal</p>")

    requests = install_transport(monkeypatch, handler)

    text = await scraping.fetch_page_text("https://jobs.example.com/start", timeout=5, max_bytes=10_000)

    assert text is None
    assert [str(request.url) for request in requests] == ["https://jobs.example.com/start"]


@pytest.mark.asyncio
async def test_relative_redirect_is_followed_and_validated(monkeypatch):
    lookups = install_resolver(
        monkeypatch,
        {"jobs.example.com": [PUBLIC_IP], "careers.example.com": [PUBLIC_IP]},
    )

    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(301, headers={"Location": "/careers"})
        if request.url.path == "/careers":
            return httpx.Response(302, headers={"Location": "https://careers.example.com/open"})
        return html_response("<h1>Level Designer</h1>")

    requests = install_transport(monkeypatch, handler)

    text = await scraping.fetch_page_text("https://jobs.example.com/start", timeout=5, max_bytes=10_000)

    assert text == "Level Designer"
    assert [str(request.url) for request in requests] == [
        "https://jobs.example.com/start",
        "https://jobs.example.com/careers",
        "https://careers.example.com/open",
    ]
    assert lookups == ["jobs.example.com", "jobs.example.com", "careers.example.com"]


@pytest.mark.asyncio
async def test_too_many_redirects(monkeypatch):
    install_resolver(monkeypatch, {"jobs.example.com": [PUBLIC_IP]})

    def handler(request):
        hop = int(request.url.params.get("hop", "0"))
        return httpx.Response(302, headers={"Location": f"/loop?hop={hop + 1}"})

    requests = install_transport(monkeypatch, handler)

    text = await scraping.fetch_page_text("https://jobs.example.com/loop", timeout=5, max_bytes=10_000)

    assert text is None
    assert len(requests) == scraping.MAX_REDIRECTS + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["application/pdf", "image/png", "application/json", None])
async def test_non_html_content_is_rejected(monkeypatch, content_type):
    install_resolver(monkeypatch, {"jobs.example.com": [PUBLIC_IP]})
    headers = {"Content-Type": content_type} if content_type else {}
    install_transport(monkeypatch, lambda request: httpx.Response(200, headers=headers, content=b"<p>x</p>"))

    assert await scraping.fetch_page_text("https://jobs.example.com", timeout=5, max_bytes=10_000) is None


@pytest.mark.asyncio
async def test_text_plain_and_xhtml_are_accepted(monkeypatch):
    install_resolver(monkeypatch, {"jobs.example.com": [PUBLIC_IP]})

    def handler(request):
        if request.url.path == "/plain":
            return httpx.Response(200, headers={"Content-Type": "text/plain"}, content=b"  Gameplay Programmer \n\n Lyon ")
        return html_response("<html><body><p>Tech Artist</p></body></html>", content_type="application/xhtml+xml")

    install_transport(monkeypatch, handler)

    plain = await scraping.fetch_page_text("https://jobs.example.com/plain", timeout=5, max_bytes=10_000)
    xhtml = await scraping.fetch_page_text("https://jobs.example.com/xhtml", timeout=5, max_bytes=10_000)

    assert plain == "Gameplay Programmer\nLyon"
    assert xhtml == "Tech Artist"


@pytest.mark.asyncio
async def test_declared_content_length_over_limit_is_rejected_without_reading(monkeypatch):
    install_resolver(monkeypatch, {"jobs.example.com": [PUBLIC_IP]})
    consumed = []

    async def body():
        consumed.append(True)
        yield b"<p>big</p>"

    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/html", "Content-Length": "5000000"},
            content=body(),
        ),
    )

    assert await scraping.fetch_page_text("https://jobs.example.com", timeout=5, max_bytes=10_000) is None
    assert consumed == []


@pytest.mark.asyncio
# Stopping early leaves httpx's internal stream wrapper generator to the loop's finalizer,
# which only runs after this test's loop is closed.
@pytest.mark.filterwarnings("ignore:coroutine method 'aclose':RuntimeWarning")
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
async def test_reading_stops_at_max_bytes(monkeypatch):
    install_resolver(monkeypatch, {"jobs.example.com": [PUBLIC_IP]})
    chunks_sent = []

    async def endless_body():
        yield b"<p>"
        while True:
            chunks_sent.append(1)
            yield b"a" * 1000

    install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"Content-Type": "text/html"}, content=endless_body()),
    )

    text = await scraping.fetch_page_text("https://jobs.example.com", timeout=5, max_bytes=5_000)

    assert text is not None
    assert len(text) <= 5_000
    assert len(chunks_sent) <= 6


@pytest.mark.asyncio
async def test_whole_fetch_is_bounded_by_timeout(monkeypatch):
    install_resolver(monkeypatch, {"jobs.example.com": [PUBLIC_IP]})

    async def trickle():
        # Each chunk arrives quickly, so only a whole-fetch deadline can stop this.
        while True:
            await asyncio.sleep(0.01)
            yield b"a"

    install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"Content-Type": "text/html"}, content=trickle()),
    )

    started = time.monotonic()
    text = await scraping.fetch_page_text("https://jobs.example.com", timeout=0.2, max_bytes=10_000_000)

    assert text is None
    assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_charset_from_headers_is_used(monkeypatch):
    install_resolver(monkeypatch, {"jobs.example.com": [PUBLIC_IP]})
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=iso-8859-1"},
            content="<p>Concepteur de niveaux, Montréal</p>".encode("iso-8859-1"),
        ),
    )

    text = await scraping.fetch_page_text("https://jobs.example.com", timeout=5, max_bytes=10_000)

    assert text == "Concepteur de niveaux, Montréal"


@pytest.mark.asyncio
async def test_html_parsing_runs_off_the_event_loop(monkeypatch):
    install_resolver(monkeypatch, {"jobs.example.com": [PUBLIC_IP]})
    install_transport(monkeypatch, lambda request: html_response("<p>Animator</p>"))
    calls = []
    real_run_blocking = scraping.run_blocking

    async def spy(func, *args, **kwargs):
        calls.append(func)
        return await real_run_blocking(func, *args, **kwargs)

    monkeypatch.setattr(scraping, "run_blocking", spy)

    assert await scraping.fetch_page_text("https://jobs.example.com", timeout=5, max_bytes=10_000) == "Animator"
    assert calls == [scraping._extract_text]


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
