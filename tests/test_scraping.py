import asyncio
import socket
import time

import httpx
import pytest

from bot import scraping

REAL_ASYNC_CLIENT = httpx.AsyncClient
PUBLIC_IP = "93.184.216.34"


class FakeNetworkStream:
    """Stands in for httpcore's network stream: only ``server_addr`` matters here."""

    def __init__(self, server_addr):
        self.server_addr = server_addr

    def get_extra_info(self, info):
        return self.server_addr if info == "server_addr" else None


def public_peer(request):
    return (PUBLIC_IP, 443)


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


def install_transport(monkeypatch, handler, peer=public_peer):
    """Serve every request from ``handler`` (no real HTTP); returns the requests seen.

    ``peer(request)`` is the server address reported through the ``network_stream``
    extension (None leaves the extension out).
    """
    requests = []

    async def recording_handler(request: httpx.Request):
        requests.append(request)
        result = handler(request)
        if asyncio.iscoroutine(result):
            result = await result
        address = peer(request)
        if address is not None and "network_stream" not in result.extensions:
            result.extensions["network_stream"] = FakeNetworkStream(address)
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

    page = await scraping.fetch_page_text("https://jobs.example.com", timeout=5, max_bytes=10_000)

    assert page is not None
    text = page.text
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

    page = await scraping.fetch_page_text("https://jobs.example.com/start", timeout=5, max_bytes=10_000)

    assert page.text == "Level Designer"
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

    assert plain.text == "Gameplay Programmer\nLyon"
    assert plain.links == ()
    assert xhtml.text == "Tech Artist"


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

    page = await scraping.fetch_page_text("https://jobs.example.com", timeout=5, max_bytes=5_000)

    assert page is not None
    assert len(page.text) <= 5_000
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

    page = await scraping.fetch_page_text("https://jobs.example.com", timeout=5, max_bytes=10_000)

    assert page.text == "Concepteur de niveaux, Montréal"


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

    page = await scraping.fetch_page_text("https://jobs.example.com", timeout=5, max_bytes=10_000)

    assert page.text == "Animator"
    assert calls == [scraping._extract_page]


@pytest.mark.asyncio
async def test_unusable_charset_falls_back_to_utf8(monkeypatch):
    install_resolver(monkeypatch, {"jobs.example.com": [PUBLIC_IP]})
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=idna"},
            content="<p>Développeur gameplay</p>".encode("utf-8"),
        ),
    )

    page = await scraping.fetch_page_text("https://jobs.example.com", timeout=5, max_bytes=10_000)

    assert page.text == "Développeur gameplay"


# --- Peer address (DNS rebinding) ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "server_addr",
    [
        ("127.0.0.1", 443),
        ("10.0.0.7", 80),
        ("::1", 443, 0, 0),
        ("::ffff:127.0.0.1", 443, 0, 0),
        ("fe80::1%eth0", 443, 0, 2),
        None,
    ],
)
async def test_peer_address_is_checked_before_reading(monkeypatch, server_addr):
    # DNS said public, but the connection landed somewhere else (or cannot be verified).
    install_resolver(monkeypatch, {"jobs.example.com": [PUBLIC_IP]})
    consumed = []

    async def body():
        consumed.append(True)
        yield b"<p>internal admin page</p>"

    install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"Content-Type": "text/html"}, content=body()),
        peer=lambda request: server_addr,
    )

    assert await scraping.fetch_page_text("https://jobs.example.com", timeout=5, max_bytes=10_000) is None
    assert consumed == []


@pytest.mark.asyncio
async def test_public_ipv6_peer_is_accepted(monkeypatch):
    install_resolver(monkeypatch, {"jobs.example.com": ["2606:2800:220:1:248:1893:25c8:1946"]})
    install_transport(
        monkeypatch,
        lambda request: html_response("<p>Sound Designer</p>"),
        peer=lambda request: ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0),
    )

    page = await scraping.fetch_page_text("https://jobs.example.com", timeout=5, max_bytes=10_000)

    assert page.text == "Sound Designer"


@pytest.mark.asyncio
async def test_peer_address_is_checked_on_every_hop(monkeypatch):
    install_resolver(
        monkeypatch,
        {"jobs.example.com": [PUBLIC_IP], "careers.example.com": [PUBLIC_IP]},
    )

    def handler(request):
        if request.url.host == "jobs.example.com":
            return httpx.Response(302, headers={"Location": "https://careers.example.com/open"})
        return html_response("<p>internal</p>")

    def peer(request):
        # The second host rebinds to loopback between the lookup and the connection.
        return (PUBLIC_IP, 443) if request.url.host == "jobs.example.com" else ("127.0.0.1", 443)

    requests = install_transport(monkeypatch, handler, peer=peer)

    assert await scraping.fetch_page_text("https://jobs.example.com/start", timeout=5, max_bytes=10_000) is None
    assert [request.url.host for request in requests] == ["jobs.example.com", "careers.example.com"]


@pytest.mark.asyncio
async def test_redirect_from_private_peer_is_not_followed(monkeypatch):
    install_resolver(monkeypatch, {"jobs.example.com": [PUBLIC_IP]})
    requests = install_transport(
        monkeypatch,
        lambda request: httpx.Response(302, headers={"Location": "/next"}),
        peer=lambda request: ("192.168.0.10", 443),
    )

    assert await scraping.fetch_page_text("https://jobs.example.com/start", timeout=5, max_bytes=10_000) is None
    assert len(requests) == 1


def test_peer_check_reads_httpcore_server_addr():
    request = httpx.Request("GET", "https://jobs.example.com/")
    ok = httpx.Response(200, request=request, extensions={"network_stream": FakeNetworkStream((PUBLIC_IP, 443))})
    scraping.ensure_public_peer(ok)

    for extensions in ({}, {"network_stream": FakeNetworkStream(None)}):
        missing = httpx.Response(200, request=request, extensions=extensions)
        with pytest.raises(scraping.UnsafeURLError):
            scraping.ensure_public_peer(missing)


# --- Links ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_page_links_are_resolved_against_the_final_url(monkeypatch):
    install_resolver(monkeypatch, {"studio.example.com": [PUBLIC_IP]})
    html = """
        <nav><a href="/about">About</a></nav>
        <main>
            <a href="https://jobs.lever.co/studio/123#apply">Senior Artist</a>
            <a href="456">Animator</a>
            <a href="https://jobs.lever.co/studio/123">Senior Artist (again)</a>
            <a href="//boards.greenhouse.io/studio/jobs/789">Producer</a>
            <a href="mailto:jobs@studio.example.com">Email us</a>
            <a href="javascript:void(0)">Menu</a>
            <a>No href</a>
        </main>
    """

    def handler(request):
        if request.url.path == "/careers":
            return httpx.Response(301, headers={"Location": "/en/careers/"})
        return html_response(html)

    install_transport(monkeypatch, handler)

    page = await scraping.fetch_page_text("https://studio.example.com/careers", timeout=5, max_bytes=10_000)

    # Page order, deduplicated, nav links and non-http targets left out.
    assert page.links == (
        "https://jobs.lever.co/studio/123",
        "https://studio.example.com/en/careers/456",
        "https://boards.greenhouse.io/studio/jobs/789",
    )


@pytest.mark.asyncio
async def test_page_links_keep_the_text_of_their_first_named_anchor(monkeypatch):
    install_resolver(monkeypatch, {"studio.example.com": [PUBLIC_IP]})
    long_title = "Game Designer " * 20
    html = f"""
        <main>
            <a href="https://www.linkedin.com/company/studio"><span></span></a>
            <a href="https://www.linkedin.com/company/studio">Follow us</a>
            <a href="/jobs/1"><span>Senior   Game</span>
               <span>Designer</span></a>
            <a href="/jobs/1">Apply</a>
            <a href="/jobs/2" title="Level Designer"></a>
            <a href="/jobs/3" aria-label="Narrative Designer"> </a>
            <a href="/jobs/4">{long_title}</a>
            <a href="/jobs/5"></a>
        </main>
    """
    install_transport(monkeypatch, lambda request: html_response(html))

    page = await scraping.fetch_page_text("https://studio.example.com/careers", timeout=5, max_bytes=10_000)

    assert page.link_labels == {
        "https://www.linkedin.com/company/studio": "Follow us",
        "https://studio.example.com/jobs/1": "Senior Game Designer",
        "https://studio.example.com/jobs/2": "Level Designer",
        "https://studio.example.com/jobs/3": "Narrative Designer",
        "https://studio.example.com/jobs/4": " ".join(long_title.split())[: scraping.MAX_LINK_LABEL_CHARS],
    }
    assert "https://studio.example.com/jobs/5" in page.links


# --- Site-specific noise ----------------------------------------------------------------

LINKEDIN_POST_HTML = """
    <main>
        <section class="core-rail">
            <article class="main-feed-activity" data-activity-urn="urn:li:activity:1">
                Laser Rabbit is hiring a Level Designer
                <a href="https://lnkd.in/level-designer">Apply</a>
            </article>
            <section class="comment">Good luck with the hiring!</section>
            <section class="core-section-container my-3 related-posts" data-nosnippet="true">
                More Relevant Posts
                <article class="main-feed-activity" data-activity-urn="urn:li:activity:2">
                    Squanch Games is hiring a Senior Designer
                    <a href="https://ats.rippling.com/squanch/senior-designer">Senior Designer</a>
                </article>
            </section>
        </section>
    </main>
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["www.linkedin.com", "fr.linkedin.com"])
async def test_linkedin_related_posts_are_dropped(monkeypatch, host):
    install_resolver(monkeypatch, {host: [PUBLIC_IP]})
    install_transport(monkeypatch, lambda request: html_response(LINKEDIN_POST_HTML))

    page = await scraping.fetch_page_text(f"https://{host}/feed/update/urn:li:activity:1", timeout=5, max_bytes=10_000)

    assert "Laser Rabbit is hiring a Level Designer" in page.text
    assert "Good luck with the hiring!" in page.text
    assert "Squanch" not in page.text
    assert "More Relevant Posts" not in page.text
    assert page.links == ("https://lnkd.in/level-designer",)


@pytest.mark.asyncio
async def test_related_posts_class_is_kept_on_other_sites(monkeypatch):
    install_resolver(monkeypatch, {"notlinkedin.com": [PUBLIC_IP]})
    install_transport(monkeypatch, lambda request: html_response(LINKEDIN_POST_HTML))

    page = await scraping.fetch_page_text("https://notlinkedin.com/post", timeout=5, max_bytes=10_000)

    assert "Squanch Games is hiring a Senior Designer" in page.text
