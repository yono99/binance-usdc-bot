from bot.config import load_settings
from bot.news import NewsVeto, parse_titles

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Feed</title>
  <item><title>Bitcoin melonjak setelah data CPI</title></item>
  <item><title>SEC menunda keputusan ETF</title></item>
  <item><title>  </title></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>Exchange X kena hack</title></entry>
</feed>"""


def test_parse_rss_titles():
    t = parse_titles(RSS)
    assert "Bitcoin melonjak setelah data CPI" in t
    assert "SEC menunda keputusan ETF" in t
    assert all(x.strip() for x in t)        # judul kosong dibuang


def test_parse_atom_titles():
    assert parse_titles(ATOM) == ["Exchange X kena hack"]


def test_parse_invalid_returns_empty():
    assert parse_titles(b"bukan xml") == []


def test_veto_off_when_disabled():
    # GEMINI_ENABLED default false di .env.example -> veto non-aktif, selalu allow
    nv = NewsVeto(load_settings(), load_settings().raw)
    assert nv.enabled is False
    assert nv.check() == (False, "off")
