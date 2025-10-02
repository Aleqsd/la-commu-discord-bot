import base64

from bot import utils


def test_extract_urls():
    text = "Visit https://jobs.example.com and http://careers.example.org today!"
    assert utils.extract_urls(text) == [
        "https://jobs.example.com",
        "http://careers.example.org",
    ]


def test_extract_image_urls():
    text = "image: https://cdn.example.com/offer.png and IMAGE: https://foo.bar/poster.jpg"
    assert utils.extract_image_urls(text) == [
        "https://cdn.example.com/offer.png",
        "https://foo.bar/poster.jpg",
    ]


def test_sanitize_team():
    assert utils.sanitize_team("Game Design") == "game_design"
    assert utils.sanitize_team("") == "others"
    assert utils.sanitize_team("AArt") == "art"


def test_to_base64_roundtrip():
    payload = b"hello world"
    encoded = utils.to_base64(payload)
    assert encoded == base64.b64encode(payload).decode("ascii")
