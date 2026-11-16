"""Marktzeitung der Freundes-App: holt die Schlagzeilen aus öffentlichen RSS-Feeds und schreibt news.json.

Läuft als GitHub-Action (.github/workflows/news.yml) alle 3 Stunden im Repo krypto-freunde.
Die App lädt news.json beim Öffnen über raw.githubusercontent.com. Auswahl und Gewichtung
sind dieselben wie in Portfolio-Modell/kurse_aktualisieren.py (dort hole_news); bei Änderungen beide anpassen.

Aufruf: python news.py [Zieldatei]   (Standard: news.json neben dem Skript)
"""
import concurrent.futures
import datetime as dt
import email.utils
import html
import json
import pathlib
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

ZEITLIMIT = 10  # Sekunden je Feed

# Name, Adresse, Sprache, Art, höchstens so viele Meldungen, höchstens so viele Tage alt.
QUELLEN = [
    ("Fed", "https://www.federalreserve.gov/feeds/press_monetary.xml", "en", "bank", 3, 30),
    ("EZB", "https://www.ecb.europa.eu/rss/press.html", "en", "bank", 3, 30),
    ("tagesschau", "https://www.tagesschau.de/wirtschaft/finanzen/index~rss2.xml", "de", "boerse", 8, 4),
    ("Handelsblatt", "https://www.handelsblatt.com/contentexport/feed/finanzen", "de", "boerse", 8, 4),
    ("n-tv", "https://www.n-tv.de/wirtschaft/rss", "de", "boerse", 8, 4),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "en", "boerse", 6, 4),
    ("CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html", "en", "boerse", 8, 4),
    ("BTC-Echo", "https://www.btc-echo.de/feed/", "de", "krypto", 8, 4),
    ("Cointelegraph", "https://cointelegraph.com/rss", "en", "krypto", 8, 4),
]


def _muster(woerter):
    return [re.compile(r"(?<!\w)" + w + r"(?!\w)", re.I) for w in woerter]


ENTSCHEID = _muster([
    "fomc statement", r"minutes of the federal open market committee", r"monetary policy decisions?",
    r"monetary policy statement", r"interest rates?", "account of the monetary policy meeting",
])
ZINSEN = _muster([
    "fed", "fomc", "powell", "federal reserve", "us-notenbank", "ezb", "ecb", "lagarde",
    r"notenbank\w*", r"zentralbank\w*", r"central banks?", r"leitzins\w*", r"zins\w*",
    r"interest rates?", r"rate cuts?", r"rate hikes?", "geldpolitik", "monetary policy",
    r"treasury yields?", r"anleiherendite\w*", r"bond yields?",
    r"inflation\w*", "cpi", r"verbraucherpreise\w*", r"consumer prices?", r"rezession\w*",
    r"recession\w*", r"konjunktur\w*", "us-arbeitsmarkt", "jobs report", "payrolls",
    "gdp", "bip", r"z[oö]lle?n?", r"strafz[oö]ll\w*", r"tariffs?", "dollar index", "dxy",
    r"staatsschulden\w*", "debt ceiling", "shutdown", r"handelskrieg\w*", "trade war",
    r"ölpreis\w*", r"oil prices?",
])
BOERSE = _muster([
    r"aktien?\w*", r"börsen?\w*", "dax", "mdax", "wall street", "nasdaq", r"s&p ?500", "dow",
    "dow jones", r"kurs\w*", r"anleger\w*", r"marktbericht\w*", r"märkte", r"stocks?",
    r"shares?", r"futures?", r"rall(?:y|ye|ies)", r"sell-?off", r"earnings", r"quartalszahlen",
    r"gewinnwarnung\w*", r"prognose\w*", r"stock markets?", r"börsengang\w*", "ipo",
    "nvidia", "tesla", "apple", "microsoft", "amazon", "alphabet", "google", "meta",
    "broadcom", "amd", "tsmc", "palantir", "openai", "strategy", "microstrategy", "coinbase",
    "blackrock", "sap", "infineon", r"chip\w*", r"ki-\w+", r"halbleiter\w*", r"semiconductors?",
    "gold", r"goldpreis\w*",
])
REGEL = _muster([
    r"\w*etfs?", "sec", "cftc", r"regulier\w+", r"regulat\w+", "mica", "bafin", "fma",
    r"gesetz\w*", "bill", "congress", "kongress", "senat", "senate", r"steuer\w*",
    r"tax\w*", r"strategische\w* reserve", "bitcoin-reserve", "strategic reserve",
    r"verbot\w*", r"bans?", r"sanktion\w*", r"sanctions?", "genius act", "clarity act",
    r"hack\w*", r"exploit\w*", r"insolven\w+", r"pleite\w*", r"bankrupt\w*", r"liquidat\w+",
    r"liquidier\w+", r"abfl[uü]ss\w*", r"zufl[uü]ss\w*", r"outflows?", r"inflows?",
])
WUCHT = _muster([
    r"crash\w*", r"absturz\w*", r"st[uü]rzt\w*", r"abgest[uü]rzt", r"kurssturz\w*", r"kursrutsch\w*",
    r"einbruch\w*", r"bricht ein", r"brechen ein", r"eingebrochen", r"ausverkauf\w*", r"panik\w*",
    r"rekordhoch\w*", r"allzeithoch\w*", r"rekord\w*", r"kurssprung\w*", r"explodier\w+",
    r"plunges?", r"plunged", r"tumbles?", r"tumbled", r"sinks?", r"slumps?", r"crashes",
    r"soars?", r"surges?", r"jumps?", r"record high\w*", r"all-time high\w*", r"selloff",
    r"sell-off", r"rout", r"meltdown", r"bear market", r"bärenmarkt\w*",
])
NAMEN = _muster([
    "nvidia", "bitcoin", "btc", "fed", "ezb", "dax", "wall street", "nasdaq", r"s&p ?500",
    "tesla", "apple", "microsoft", "ethereum",
])
KRYPTO = re.compile(r"bitcoin|krypto|crypto|stablecoin|ethereum|\bbtc\b", re.I)
PROZENT = re.compile(r"(\d{1,3})(?:[.,]\d+)?\s?(?:%|prozent|percent)", re.I)
TAGS = re.compile(r"<[^>]+>")


def passt(muster, text):
    return any(m.search(text) for m in muster)


def gewicht(titel, zeit, jetzt):
    """Wie wichtig eine Meldung ist: starke Kursbewegung, große Prozentzahl, bekannter Name, frisch."""
    w = 2 if passt(WUCHT, titel) else 0
    prozente = [int(p) for p in PROZENT.findall(titel)]
    if prozente and 5 <= max(prozente) <= 99:
        w += 3 if max(prozente) >= 10 else 2
    if passt(NAMEN, titel):
        w += 1
    if jetzt - zeit <= dt.timedelta(hours=18):
        w += 1
    return w


def lade_feed(quelle):
    """Liefert die passenden Meldungen einer Quelle; bei jedem Fehler None."""
    name, url, sprache, art, hoechstens, tage = quelle
    try:
        anfrage = urllib.request.Request(url, headers={"User-Agent": "krypto-freunde-marktzeitung"})
        with urllib.request.urlopen(anfrage, timeout=ZEITLIMIT) as antwort:
            roh = antwort.read(3_000_000)
        wurzel = ET.fromstring(roh)
    except Exception:
        return None
    jetzt = dt.datetime.now(dt.timezone.utc)
    meldungen = []
    for item in wurzel.iter("item"):
        try:
            titel = " ".join(html.unescape(item.findtext("title") or "").split())[:220]
            link = (item.findtext("link") or "").strip()
            zeit = email.utils.parsedate_to_datetime((item.findtext("pubDate") or "").strip())
            if zeit.tzinfo is None:
                zeit = zeit.replace(tzinfo=dt.timezone.utc)
            zeit = zeit.astimezone(dt.timezone.utc)
            kurz = " ".join(html.unescape(TAGS.sub(" ", item.findtext("description") or "")).split())
        except Exception:
            continue
        if not titel or not link.startswith("https://") or len(link) > 500:
            continue
        if not (dt.timedelta(days=-1) <= jetzt - zeit <= dt.timedelta(days=tage)):
            continue
        if art == "bank":
            if not passt(ENTSCHEID, titel):
                continue
            ressort = "Zinsen & Konjunktur"
        elif art == "krypto":
            rubriken = " ".join(c.text or "" for c in item.findall("category"))
            if not (passt(WUCHT, titel) or passt(ZINSEN, titel) or passt(REGEL, titel + " " + rubriken)):
                continue
            ressort = "Krypto"
        else:
            boerse, zinsen = passt(BOERSE, titel), passt(ZINSEN, titel)
            if not (boerse or zinsen or passt(WUCHT, titel)):
                continue
            ressort = ("Krypto" if KRYPTO.search(titel)
                       else "Zinsen & Konjunktur" if zinsen and not boerse else "Aktien & Börse")
        if kurz == titel:
            kurz = ""
        if len(kurz) > 190:
            kurz = kurz[:190].rsplit(" ", 1)[0] + " …"
        meldungen.append({"t": titel, "u": link, "d": zeit.strftime("%Y-%m-%dT%H:%MZ"), "q": name,
                          "r": ressort, "l": sprache, "w": gewicht(titel, zeit, jetzt), "s": kurz})
    meldungen.sort(key=lambda m: m["d"], reverse=True)
    return meldungen[:hoechstens]


def hole_news():
    """Sammelt die Meldungen aller Quellen. Liefert None, wenn keine Quelle erreichbar war."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(QUELLEN)) as pool:
        ergebnisse = list(pool.map(lade_feed, QUELLEN))
    ok = [q[0] for q, e in zip(QUELLEN, ergebnisse) if e is not None]
    if not ok:
        return None
    meldungen, gesehen = [], set()
    for liste in ergebnisse:
        for m in liste or []:
            if m["u"] not in gesehen:
                gesehen.add(m["u"])
                meldungen.append(m)
    meldungen.sort(key=lambda m: m["d"], reverse=True)
    return {
        "abruf": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "quellen": ok,
        "fehlten": [q[0] for q, e in zip(QUELLEN, ergebnisse) if e is None],
        "meldungen": meldungen[:70],
    }


def main():
    ziel = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent / "news.json"
    news = hole_news()
    if not news or not news["meldungen"]:
        # Alte Datei behalten statt eine leere Zeitung zu veröffentlichen
        print("Keine Meldungen geholt, news.json bleibt unverändert.")
        return 1
    ziel.write_text(json.dumps(news, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(news['meldungen'])} Meldungen aus {', '.join(news['quellen'])}"
          + (f"; nicht erreichbar: {', '.join(news['fehlten'])}" if news["fehlten"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
