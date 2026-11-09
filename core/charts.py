# -*- coding: utf-8 -*-
"""Специфікації чартів з прозорою методикою. Назви не вигадують «офіційних» рейтингів."""
from __future__ import annotations

PERIODS = ("week", "month")
JAMENDO_ORDER = {"week": "popularity_week", "month": "popularity_month"}

# (id, назва, джерело, провайдер, параметр)
SPECS = [
    ("audius-trending", "Audius Trending", "Audius", "audius", None),
    ("electronic-trending", "Electronic Trending", "Audius · Electronic", "audius", "Electronic"),
    ("popular-pop", "Popular Pop", "Jamendo", "jamendo", "pop"),
    ("popular-rock", "Popular Rock", "Jamendo", "jamendo", "rock"),
    ("popular-electronic", "Popular Electronic", "Jamendo", "jamendo", "electronic"),
    ("popular-hiphop", "Popular Hip-Hop", "Jamendo", "jamendo", "hiphop"),
    ("popular-jazz", "Popular Jazz", "Jamendo", "jamendo", "jazz"),
    ("popular-classical", "Popular Classical", "Jamendo", "jamendo", "classical"),
    ("popular-ambient", "Popular Ambient", "Jamendo", "jamendo", "ambient"),
    ("popular-rnb", "Popular R&B", "Jamendo", "jamendo", "rnb"),
]

METHODOLOGY = {
    "audius": "Добірка Audius Trending від самого Audius. Це не офіційний рейтинг і не Global Top; "
              "позиції й кількість прослуховувань MusicLSP не рахує.",
    "jamendo": "Треки Jamendo за тегом жанру, відсортовані за популярністю Jamendo за вибраний період. "
               "Це добірка джерела, а не офіційний чарт; позиції MusicLSP не рахує.",
}


def normalize_period(value) -> str:
    return value if value in PERIODS else "month"


def build_chart(spec, tracks, period="month") -> dict:
    cid, title, source, provider, _param = spec
    chart = {
        "id": cid, "title": title, "source": source, "kind": "provider_trending",
        "provider": provider, "methodology": METHODOLOGY[provider],
        "tracks": list(tracks or []),
    }
    if provider == "jamendo":
        chart["period"] = normalize_period(period)
    return chart


def visible(charts) -> list:
    """Порожні чарти не показуємо: краще менше, ніж порожня «добірка»."""
    return [c for c in charts if c.get("tracks")]