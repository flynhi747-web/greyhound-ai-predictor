#!/usr/bin/env python3
"""
Greyhound AI Predictor V1.1
===========================
Prediction-only. This program does NOT place bets.

LIVE DATA
- Sportsbet: today's greyhound meetings, racecards and current win prices.
- TAB: matching racecard + detailed form guide when available.

OUTPUT
- Exactly ONE predicted winner per race.
- Confidence label.
- Main danger.
- Top-three ranking.
- Debug JSON saved so API changes are easy to diagnose.

The model is deliberately transparent: it is a weighted statistical model,
not a claim of certainty and not yet a trained machine-learning model.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo(os.getenv("BOT_TIMEZONE", "Australia/Brisbane"))
MAX_RACES = int(os.getenv("MAX_RACES", "8"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "25"))
TAB_JURISDICTION = os.getenv("TAB_JURISDICTION", "NSW")

SPORTSBET_BASE = "https://www.sportsbet.com.au/apigw"
SPORTSBET_ALL_RACING = (
    SPORTSBET_BASE + "/sportsbook-racing/Sportsbook/Racing/AllRacing/{date}"
)
SPORTSBET_RACECARD = (
    SPORTSBET_BASE + "/sportsbook-racing/Sportsbook/Racing/Events/{event_id}/Racecard"
)

TAB_BASE = "https://api.beta.tab.com.au/v1/tab-info-service/racing"
TAB_MEETINGS = TAB_BASE + "/dates/{date}/meetings"
TAB_RACE = (
    TAB_BASE
    + "/dates/{date}/meetings/G/{venue}/races/{race_number}"
)
TAB_FORM = TAB_RACE + "/form"

SPORTSBET_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-AU,en;q=0.9",
    "Origin": "https://www.sportsbet.com.au",
    "Referer": "https://www.sportsbet.com.au/",
    "country-code": "AU",
    "brand": "sportsbet",
}

# TAB is Akamai-fronted and behaves better with a complete normal browser bundle.
TAB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-AU,en;q=0.9",
    "Origin": "https://www.tab.com.au",
    "Referer": "https://www.tab.com.au/",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}

# V1.1 weighting.
# Missing features are ignored and weights are re-normalised.
WEIGHTS = {
    "market": 0.38,          # Sportsbet market/favourite signal
    "recent_form": 0.18,     # recent finishing positions
    "speed_history": 0.12,   # comparable recent race times
    "win_rate": 0.10,        # wins in available history
    "track_distance": 0.10,  # wins at same track/distance where possible
    "early_speed": 0.07,     # first split / early pace where available
    "box": 0.05,             # small generic draw prior
}


@dataclass
class RaceInfo:
    event_id: int
    meeting: str
    race_number: int | None
    start_time: datetime | None


@dataclass
class TabMeeting:
    name: str
    venue: str
    races: list[dict]


@dataclass
class RunnerScore:
    number: int | None
    name: str
    odds: float | None
    market_prob: float | None
    score: float
    confidence: str
    components: dict[str, float]
    data_sources: list[str]
    raw: dict[str, Any]


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def get_json(
    session: requests.Session,
    url: str,
    headers: dict,
    params: dict | None = None,
) -> Any:
    r = session.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    try:
        return r.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Non-JSON response ({r.status_code}). First 200 chars: {r.text[:200]!r}"
        ) from exc


def walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def first_present(d: dict, keys: Iterable[str], default=None):
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
    return default


def to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, str):
        m = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if not m:
            return None
        try:
            return float(m.group())
        except ValueError:
            return None
    return None


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        x = float(value)
        if x > 10_000_000_000:
            x /= 1000
        try:
            return datetime.fromtimestamp(x, tz=timezone.utc).astimezone(TZ)
        except Exception:
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ)
            return dt.astimezone(TZ)
        except ValueError:
            return None
    return None


def normalise_name(name: Any) -> str:
    s = str(name or "").upper()
    s = re.sub(r"^\s*\d+\s*[\.\-]?\s*", "", s)
    s = s.replace("&", "AND")
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return " ".join(s.split())


def match_ratio(a: str, b: str) -> float:
    aa, bb = normalise_name(a), normalise_name(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    if aa in bb or bb in aa:
        return 0.92
    return SequenceMatcher(None, aa, bb).ratio()


def is_greyhound_label(value: Any) -> bool:
    s = str(value or "").strip().lower()
    return s in {"g", "greyhound", "greyhounds", "dogs", "dog"} or "greyhound" in s


def find_value(obj: Any, aliases: Iterable[str]) -> Any:
    wanted = {str(a).lower().replace("_", "") for a in aliases}
    for node in walk(obj):
        if not isinstance(node, dict):
            continue
        for key, val in node.items():
            kk = str(key).lower().replace("_", "")
            if kk in wanted and val not in (None, ""):
                return val
    return None


# ---------------------------------------------------------------------------
# Sportsbet discovery and racecards
# ---------------------------------------------------------------------------

def looks_like_race(obj: dict) -> bool:
    event_id = first_present(obj, ["eventId", "eventID", "id"])
    race_no = first_present(obj, ["raceNumber", "raceNo", "number"])
    start = first_present(
        obj, ["advertisedStartTime", "startTime", "startDateTime", "advertisedStart"]
    )
    return event_id is not None and (race_no is not None or start is not None)


def add_sportsbet_race(store: dict[int, RaceInfo], event: dict, meeting_name: str):
    raw_id = first_present(event, ["eventId", "eventID", "id"])
    try:
        event_id = int(raw_id)
    except (TypeError, ValueError):
        return

    raw_no = first_present(event, ["raceNumber", "raceNo", "number"])
    try:
        race_no = int(raw_no) if raw_no is not None else None
    except (TypeError, ValueError):
        race_no = None

    start = parse_dt(first_present(
        event, ["advertisedStartTime", "startTime", "startDateTime", "advertisedStart"]
    ))

    store[event_id] = RaceInfo(
        event_id=event_id,
        meeting=meeting_name or str(first_present(
            event, ["meetingName", "competitionName", "venueName"], "Unknown meeting"
        )),
        race_number=race_no,
        start_time=start,
    )


def extract_sportsbet_greyhounds(payload: Any) -> list[RaceInfo]:
    races: dict[int, RaceInfo] = {}

    # Main path: greyhound meeting containing events/races.
    for node in walk(payload):
        if not isinstance(node, dict):
            continue
        race_type = first_present(
            node, ["raceType", "raceTypeName", "className", "eventType", "type"], ""
        )
        children = first_present(node, ["events", "races", "raceEvents"], [])
        if not (isinstance(children, list) and is_greyhound_label(race_type)):
            continue

        meeting_name = str(first_present(
            node, ["name", "meetingName", "competitionName", "venueName"], "Unknown meeting"
        ))
        for event in children:
            if isinstance(event, dict) and looks_like_race(event):
                add_sportsbet_race(races, event, meeting_name)

    # Shape-drift fallback.
    if not races:
        for node in walk(payload):
            if not isinstance(node, dict) or not looks_like_race(node):
                continue
            blob = " ".join(
                str(first_present(node, [key], ""))
                for key in [
                    "raceType", "raceTypeName", "className",
                    "meetingName", "competitionName", "venueName"
                ]
            )
            if "greyhound" in blob.lower():
                add_sportsbet_race(
                    races,
                    node,
                    str(first_present(
                        node, ["meetingName", "competitionName", "venueName"],
                        "Unknown meeting"
                    )),
                )

    return list(races.values())


def selection_list_from_sportsbet(payload: Any) -> list[dict]:
    markets = []
    for node in walk(payload):
        if isinstance(node, dict) and isinstance(node.get("selections"), list):
            markets.append(node)

    if not markets:
        return []

    def market_rank(m: dict) -> tuple[int, int]:
        name = str(first_present(m, ["name", "marketName", "displayName"], "")).lower()
        status = str(first_present(m, ["status", "marketStatus"], "")).lower()
        score = 0
        if name == "win":
            score += 100
        if "fixed" in name and "win" in name:
            score += 90
        if "win" in name and "place" not in name:
            score += 70
        if "place" in name:
            score -= 40
        if "head to head" in name:
            score -= 50
        if status in {"active", "a", "open", "priced"}:
            score += 10
        return score, len(m.get("selections", []))

    markets.sort(key=market_rank, reverse=True)
    selections = []
    for sel in markets[0].get("selections", []):
        if not isinstance(sel, dict):
            continue
        status = str(first_present(
            sel, ["status", "bettingStatus", "selectionStatus"], ""
        )).lower()
        scratched = first_present(sel, ["scratched", "isScratched"], False)
        if scratched is True or status in {"scratched", "suspended"}:
            continue
        selections.append(sel)
    return selections


def runner_name(sel: dict) -> str:
    return str(first_present(
        sel, ["name", "selectionName", "runnerName", "dogName"], "Unknown runner"
    )).strip()


def runner_number(sel: dict) -> int | None:
    for key in ["runnerNumber", "boxNumber", "box", "number", "sort", "selectionNumber"]:
        n = to_float(sel.get(key))
        if n is not None and 0 < n < 20:
            return int(n)
    m = re.match(r"^\s*(\d+)[\.\s-]+", runner_name(sel))
    return int(m.group(1)) if m else None


def runner_odds(sel: dict) -> float | None:
    price = sel.get("price")
    if isinstance(price, dict):
        for key in ["winPrice", "decimal", "price", "currentPrice", "returnWin"]:
            n = to_float(price.get(key))
            if n is not None and n > 1:
                return n
    for key in ["winPrice", "odds", "fixedOdds", "decimalOdds", "returnWin"]:
        n = to_float(sel.get(key))
        if n is not None and n > 1:
            return n
    return None


# ---------------------------------------------------------------------------
# TAB enrichment
# ---------------------------------------------------------------------------

def extract_tab_meetings(payload: Any) -> list[TabMeeting]:
    candidates = []
    if isinstance(payload, dict) and isinstance(payload.get("meetings"), list):
        candidates = payload["meetings"]
    elif isinstance(payload, list):
        candidates = payload
    else:
        for node in walk(payload):
            if isinstance(node, list) and node and all(isinstance(x, dict) for x in node):
                if any("meetingName" in x for x in node):
                    candidates = node
                    break

    result = []
    for meeting in candidates:
        if not isinstance(meeting, dict):
            continue
        race_type = first_present(meeting, ["raceType", "raceTypeName", "type"], "")
        if not is_greyhound_label(race_type):
            continue
        name = str(first_present(
            meeting, ["meetingName", "name", "venueName"], "Unknown meeting"
        ))
        venue = str(first_present(
            meeting, ["venueMnemonic", "venueCode", "mnemonic"], ""
        ))
        races = first_present(meeting, ["races", "events"], [])
        if venue and isinstance(races, list):
            result.append(TabMeeting(name=name, venue=venue, races=races))
    return result


def find_tab_meeting(sportsbet_name: str, tab_meetings: list[TabMeeting]) -> TabMeeting | None:
    if not tab_meetings:
        return None
    ranked = sorted(
        ((match_ratio(sportsbet_name, m.name), m) for m in tab_meetings),
        key=lambda x: x[0],
        reverse=True,
    )
    best_ratio, best = ranked[0]
    return best if best_ratio >= 0.60 else None


def extract_tab_runners(payload: Any) -> list[dict]:
    if isinstance(payload, dict) and isinstance(payload.get("runners"), list):
        return [x for x in payload["runners"] if isinstance(x, dict)]
    for node in walk(payload):
        if isinstance(node, dict) and isinstance(node.get("runners"), list):
            return [x for x in node["runners"] if isinstance(x, dict)]
    return []


def extract_form_runner_objects(payload: Any) -> list[dict]:
    for key in ["formData", "runners", "runnerForm", "forms"]:
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return [x for x in payload[key] if isinstance(x, dict)]
    # Shape-drift fallback: locate a list containing runnerName.
    for node in walk(payload):
        if isinstance(node, list) and node and all(isinstance(x, dict) for x in node):
            if any(first_present(x, ["runnerName", "name"]) for x in node):
                if any(
                    isinstance(first_present(
                        x, ["pastPerformances", "previousStarts", "starts", "form"], None
                    ), list)
                    for x in node
                ):
                    return node
    return []


def history_from_form_runner(obj: dict) -> list[dict]:
    for key in ["pastPerformances", "previousStarts", "starts", "recentStarts", "form"]:
        value = obj.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def match_runner_object(
    sb_sel: dict,
    objects: list[dict],
) -> dict | None:
    sb_num = runner_number(sb_sel)
    sb_name = runner_name(sb_sel)

    # Runner number is the strongest join.
    if sb_num is not None:
        for obj in objects:
            n = to_float(first_present(
                obj, ["runnerNumber", "boxNumber", "box", "number"]
            ))
            if n is not None and int(n) == sb_num:
                return obj

    # Then normalized dog name.
    ranked = []
    for obj in objects:
        name = str(first_present(obj, ["runnerName", "name", "selectionName"], ""))
        ranked.append((match_ratio(sb_name, name), obj))
    if not ranked:
        return None
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[0][1] if ranked[0][0] >= 0.78 else None


def enrich_with_tab(
    selections: list[dict],
    tab_race: Any,
    tab_form: Any,
    meeting_name: str,
) -> tuple[float | None, int]:
    """
    Attach TAB runner card/history to each Sportsbet selection.
    Returns (current race distance, number of matched runners).
    """
    distance = to_float(find_value(
        tab_race, ["raceDistance", "distance", "distanceMetres", "distanceMeters"]
    ))
    race_runners = extract_tab_runners(tab_race)
    form_objects = extract_form_runner_objects(tab_form)

    matched = 0
    for sel in selections:
        card_obj = match_runner_object(sel, race_runners)
        form_obj = match_runner_object(sel, form_objects)
        history = history_from_form_runner(form_obj or {})

        if card_obj is not None:
            sel["_tab_card"] = card_obj
        if form_obj is not None:
            sel["_tab_form"] = form_obj
        if history:
            sel["_tab_history"] = history
        if card_obj is not None or form_obj is not None:
            matched += 1

    derive_history_features(selections, meeting_name, distance)
    return distance, matched


# ---------------------------------------------------------------------------
# Statistical feature engineering
# ---------------------------------------------------------------------------

def extract_position(perf: dict) -> int | None:
    n = to_float(first_present(
        perf, ["position", "finishPosition", "placing", "place", "finishingPosition"]
    ))
    return int(n) if n is not None and n > 0 else None


def extract_distance(perf: dict) -> float | None:
    return to_float(first_present(
        perf, ["distance", "raceDistance", "distanceMetres", "distanceMeters"]
    ))


def extract_track(perf: dict) -> str:
    return str(first_present(
        perf, ["track", "trackName", "venue", "meetingName", "venueName"], ""
    ))


def extract_time_seconds(perf: dict) -> float | None:
    raw = first_present(
        perf, ["raceTime", "time", "runTime", "performanceTime", "overallTime"]
    )
    n = to_float(raw)
    if n is None:
        return None
    # Greyhound overall race times are generally in the teens to 50s.
    if 10 <= n <= 70:
        return n
    return None


def extract_early_split(perf: dict) -> float | None:
    raw = first_present(
        perf,
        [
            "firstSplit", "splitTime", "firstSection", "firstSectional",
            "earlySectional", "sectionalTime", "firstSplitTime"
        ],
    )
    n = to_float(raw)
    if n is None:
        return None
    if 3 <= n <= 15:
        return n
    return None


def recent_positions_score(positions: list[int]) -> float | None:
    if not positions:
        return None
    points = {1: 1.00, 2: 0.80, 3: 0.65, 4: 0.49, 5: 0.35, 6: 0.24, 7: 0.15, 8: 0.08}
    recency = [1.00, 0.86, 0.73, 0.62, 0.52, 0.44]
    vals = [points.get(p, 0.04) for p in positions[:6]]
    ws = recency[:len(vals)]
    return sum(v*w for v, w in zip(vals, ws)) / sum(ws)


def inverted_minmax(values: dict[str, float]) -> dict[str, float]:
    """Lower raw value is better. Maps best=1.0, worst=0.20."""
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if math.isclose(lo, hi):
        return {k: 0.70 for k in values}
    return {
        k: 0.20 + 0.80 * ((hi - v) / (hi - lo))
        for k, v in values.items()
    }


def derive_history_features(
    selections: list[dict],
    current_track: str,
    current_distance: float | None,
):
    time_raw: dict[str, float] = {}
    split_raw: dict[str, float] = {}

    for sel in selections:
        history = sel.get("_tab_history", [])
        if not isinstance(history, list) or not history:
            continue

        # Assume returned history is newest-first; only use a useful recent window.
        history = [x for x in history[:12] if isinstance(x, dict)]
        positions = [p for p in (extract_position(x) for x in history) if p is not None]
        if positions:
            sel["_recent_form_score"] = recent_positions_score(positions)
            sel["_history_win_rate"] = sum(p == 1 for p in positions) / len(positions)

        # Track/distance record.
        same_td = []
        same_dist = []
        for perf in history:
            p = extract_position(perf)
            d = extract_distance(perf)
            t = extract_track(perf)
            if p is None:
                continue
            distance_ok = (
                current_distance is not None
                and d is not None
                and abs(d - current_distance) <= 10
            )
            if distance_ok:
                same_dist.append(p)
                if current_track and match_ratio(current_track, t) >= 0.70:
                    same_td.append(p)

        sample = same_td if len(same_td) >= 2 else same_dist
        if len(sample) >= 2:
            sel["_track_distance_win_rate"] = sum(p == 1 for p in sample) / len(sample)

        # Comparable speed samples: same distance, preferably same track.
        time_samples_track = []
        time_samples_dist = []
        split_samples_track = []
        split_samples_dist = []

        for perf in history:
            d = extract_distance(perf)
            if current_distance is None or d is None or abs(d - current_distance) > 10:
                continue
            same_track = current_track and match_ratio(current_track, extract_track(perf)) >= 0.70

            rt = extract_time_seconds(perf)
            if rt is not None:
                time_samples_dist.append(rt)
                if same_track:
                    time_samples_track.append(rt)

            sp = extract_early_split(perf)
            if sp is not None:
                split_samples_dist.append(sp)
                if same_track:
                    split_samples_track.append(sp)

        time_samples = time_samples_track if len(time_samples_track) >= 2 else time_samples_dist
        split_samples = split_samples_track if len(split_samples_track) >= 2 else split_samples_dist

        # Median of fastest three prevents one freak time dominating.
        key = normalise_name(runner_name(sel))
        if time_samples:
            fastest = sorted(time_samples)[:3]
            time_raw[key] = statistics.median(fastest)
        if split_samples:
            fastest_split = sorted(split_samples)[:3]
            split_raw[key] = statistics.median(fastest_split)

    speed_scores = inverted_minmax(time_raw)
    split_scores = inverted_minmax(split_raw)

    for sel in selections:
        key = normalise_name(runner_name(sel))
        if key in speed_scores:
            sel["_speed_history_score"] = speed_scores[key]
        if key in split_scores:
            sel["_early_speed_score"] = split_scores[key]


def fallback_recent_form_score(sel: dict) -> float | None:
    raw = find_value(sel, [
        "last5Starts", "recentForm", "lastFive", "last5", "formString", "recentResults"
    ])
    if raw is None:
        return None

    positions: list[int] = []
    if isinstance(raw, list):
        for item in raw[:6]:
            if isinstance(item, dict):
                p = extract_position(item)
            else:
                n = to_float(item)
                p = int(n) if n is not None else None
            if p is not None:
                positions.append(p)
    else:
        nums = re.findall(r"\d+", str(raw))
        if len(nums) == 1 and len(nums[0]) <= 6:
            positions = [int(ch) for ch in nums[0]]
        else:
            positions = [int(n) for n in nums[:6]]

    return recent_positions_score(positions)


def percentage_feature(sel: dict, aliases: Iterable[str]) -> float | None:
    n = to_float(find_value(sel, aliases))
    if n is None:
        return None
    if n > 1:
        n /= 100
    return max(0.0, min(1.0, n))


def box_score(number: int | None) -> float | None:
    if number is None:
        return None
    # Intentionally a small generic prior only.
    table = {1: 0.74, 2: 0.70, 3: 0.64, 4: 0.58, 5: 0.54, 6: 0.57, 7: 0.61, 8: 0.66}
    return table.get(number, 0.55)


def market_probs(selections: list[dict]) -> dict[str, float]:
    raw = {}
    for sel in selections:
        odd = runner_odds(sel)
        if odd and odd > 1:
            raw[normalise_name(runner_name(sel))] = 1 / odd
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()} if total > 0 else {}


def confidence_label(score: float, margin: float, features: int, tab_enriched: bool) -> str:
    # Confidence is model confidence, not a guaranteed win probability.
    if tab_enriched and features >= 5 and score >= 0.72 and margin >= 0.09:
        return "VERY HIGH"
    if features >= 4 and score >= 0.64 and margin >= 0.055:
        return "HIGH"
    if score >= 0.54 and margin >= 0.025:
        return "MEDIUM"
    return "LOW"


def score_race(selections: list[dict]) -> list[RunnerScore]:
    probs = market_probs(selections)
    max_prob = max(probs.values()) if probs else None
    scored = []

    for sel in selections:
        key = normalise_name(runner_name(sel))
        components: dict[str, float] = {}
        sources = ["Sportsbet"]

        if key in probs and max_prob:
            components["market"] = probs[key] / max_prob

        recent = sel.get("_recent_form_score")
        if recent is None:
            recent = fallback_recent_form_score(sel)
        if recent is not None:
            components["recent_form"] = float(recent)

        speed = sel.get("_speed_history_score")
        if speed is not None:
            components["speed_history"] = float(speed)

        wr = sel.get("_history_win_rate")
        if wr is None:
            wr = percentage_feature(
                sel, ["winRate", "winPercentage", "winPercent", "winsPercentage"]
            )
        if wr is not None:
            components["win_rate"] = float(wr)

        td = sel.get("_track_distance_win_rate")
        if td is None:
            td = percentage_feature(
                sel,
                [
                    "trackDistanceWinRate", "trackAndDistanceWinRate",
                    "trackDistancePercentage", "trackDistanceStrikeRate"
                ],
            )
        if td is not None:
            components["track_distance"] = float(td)

        early = sel.get("_early_speed_score")
        if early is not None:
            components["early_speed"] = float(early)

        bx = box_score(runner_number(sel))
        if bx is not None:
            components["box"] = bx

        if "_tab_card" in sel or "_tab_form" in sel or "_tab_history" in sel:
            sources.append("TAB form")

        active = {k: WEIGHTS[k] for k in components if k in WEIGHTS}
        denom = sum(active.values()) or 1.0
        total = sum(components[k] * active[k] for k in active) / denom

        scored.append(RunnerScore(
            number=runner_number(sel),
            name=runner_name(sel),
            odds=runner_odds(sel),
            market_prob=probs.get(key),
            score=total,
            confidence="",
            components=components,
            data_sources=sources,
            raw=sel,
        ))

    scored.sort(key=lambda x: x.score, reverse=True)

    if scored:
        top, second = scored[0].score, scored[1].score if len(scored) > 1 else 0.0
        scored[0].confidence = confidence_label(
            top,
            top - second,
            len(scored[0].components),
            "TAB form" in scored[0].data_sources,
        )
        for x in scored[1:]:
            x.confidence = "â"

    return scored


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def format_race(race: RaceInfo, scores: list[RunnerScore], tab_status: str) -> str:
    if not scores:
        return (
            "\n" + "=" * 66
            + f"\nð {race.meeting} â R{race.race_number or '?'}"
            + "\nâ ï¸ No usable runner data was returned."
        )

    winner = scores[0]
    number = f"#{winner.number} " if winner.number else ""
    start = race.start_time.strftime("%-I:%M %p") if race.start_time else "time unavailable"

    favourite = min(
        (x for x in scores if x.odds is not None),
        key=lambda x: x.odds,
        default=None,
    )

    labels = {
        "market": "Sportsbet market strength",
        "recent_form": "recent form",
        "speed_history": "recent race speed",
        "win_rate": "win strike rate",
        "track_distance": "track/distance record",
        "early_speed": "early sectional speed",
        "box": "box draw",
    }

    lines = [
        "",
        "=" * 66,
        f"ð {race.meeting} â R{race.race_number or '?'} â {start}",
        "",
        f"ð PREDICTED WINNER: {number}{winner.name.upper()}",
        f"â­ CONFIDENCE: {winner.confidence}",
    ]

    if winner.odds:
        lines.append(f"ð° Sportsbet: ${winner.odds:.2f}")

    if favourite:
        fav_no = f"#{favourite.number} " if favourite.number else ""
        same = " â BOT AGREES" if favourite.name == winner.name else ""
        lines.append(
            f"ð Favourite: {fav_no}{favourite.name} (${favourite.odds:.2f}){same}"
        )

    if len(scores) > 1:
        danger = scores[1]
        dn = f"#{danger.number} " if danger.number else ""
        lines.append(f"â ï¸ MAIN DANGER: {dn}{danger.name}")

    ordered_signals = sorted(
        winner.components,
        key=lambda k: WEIGHTS.get(k, 0),
        reverse=True,
    )
    if ordered_signals:
        lines.append(
            "ð§  Analysed: "
            + ", ".join(labels.get(k, k) for k in ordered_signals)
        )

    lines.append(f"ð Data: Sportsbet live prices | TAB form: {tab_status}")
    lines.append("")
    lines.append("Top 3:")
    for i, x in enumerate(scores[:3], 1):
        n = f"#{x.number} " if x.number else ""
        odd = f" | ${x.odds:.2f}" if x.odds else ""
        lines.append(f"  {i}. {n}{x.name} â {x.score*100:.1f}/100{odd}")

    return "\n".join(lines)


def compact_score(score: RunnerScore) -> dict:
    d = asdict(score)
    # Don't duplicate potentially huge raw form history in predictions.json.
    d["raw"] = {
        "name": runner_name(score.raw),
        "number": runner_number(score.raw),
        "price": score.raw.get("price"),
        "tab_history_count": len(score.raw.get("_tab_history", []))
        if isinstance(score.raw.get("_tab_history"), list) else 0,
    }
    return d


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    now = datetime.now(TZ)
    date_str = os.getenv("RACE_DATE", now.strftime("%Y-%m-%d"))

    debug = Path("debug")
    debug.mkdir(exist_ok=True)

    sb = requests.Session()
    tab = requests.Session()

    print("ð GREYHOUND AI PREDICTOR V1.1")
    print(f"Date: {date_str} | Timezone: {TZ}")
    print("Prediction-only: this bot does not place bets.\n")

    # 1) Sportsbet live slate.
    try:
        sportsbet_daily = get_json(
            sb,
            SPORTSBET_ALL_RACING.format(date=date_str),
            SPORTSBET_HEADERS,
        )
    except Exception as exc:
        print(f"â Sportsbet daily feed failed: {exc}")
        print("Send me this Action error and I can adjust the collector.")
        return 2

    (debug / "sportsbet_all_racing.json").write_text(
        json.dumps(sportsbet_daily, indent=2, default=str), encoding="utf-8"
    )

    races = extract_sportsbet_greyhounds(sportsbet_daily)
    future = [r for r in races if r.start_time is None or r.start_time >= now]
    future.sort(key=lambda r: r.start_time or datetime.max.replace(tzinfo=TZ))
    selected = future[:MAX_RACES]

    if not selected:
        print(f"â ï¸ Found {len(races)} greyhound races but none identified as upcoming.")
        print("Raw response saved to debug/sportsbet_all_racing.json.")
        return 3

    print(f"â Sportsbet: {len(races)} greyhound races found; analysing next {len(selected)}.")

    # 2) TAB daily meetings for deeper form. Failure is non-fatal.
    tab_meetings: list[TabMeeting] = []
    try:
        tab_daily = get_json(
            tab,
            TAB_MEETINGS.format(date=date_str),
            TAB_HEADERS,
            params={
                "jurisdiction": TAB_JURISDICTION,
                "returnOffers": "false",
                "returnPromo": "false",
            },
        )
        (debug / "tab_meetings.json").write_text(
            json.dumps(tab_daily, indent=2, default=str), encoding="utf-8"
        )
        tab_meetings = extract_tab_meetings(tab_daily)
        print(f"â TAB: {len(tab_meetings)} greyhound meetings available for form enrichment.\n")
    except Exception as exc:
        print(f"â ï¸ TAB enrichment unavailable: {exc}")
        print("The bot will still predict using Sportsbet + available racecard data.\n")

    reports = []
    results_json = []

    for race in selected:
        try:
            card = get_json(
                sb,
                SPORTSBET_RACECARD.format(event_id=race.event_id),
                SPORTSBET_HEADERS,
                params={"selectionNames": "true"},
            )
            (debug / f"sportsbet_race_{race.event_id}.json").write_text(
                json.dumps(card, indent=2, default=str), encoding="utf-8"
            )
            selections = selection_list_from_sportsbet(card)

            tab_status = "not matched"
            tab_match = find_tab_meeting(race.meeting, tab_meetings)
            if (
                tab_match is not None
                and race.race_number is not None
                and selections
            ):
                venue = quote(tab_match.venue, safe="")
                race_url = TAB_RACE.format(
                    date=date_str, venue=venue, race_number=race.race_number
                )
                form_url = TAB_FORM.format(
                    date=date_str, venue=venue, race_number=race.race_number
                )
                try:
                    # Deliberate pacing because TAB throttles anonymous traffic.
                    time.sleep(0.45)
                    tab_race = get_json(
                        tab, race_url, TAB_HEADERS,
                        params={"jurisdiction": TAB_JURISDICTION},
                    )
                    time.sleep(0.45)
                    tab_form = get_json(
                        tab, form_url, TAB_HEADERS,
                        params={"jurisdiction": TAB_JURISDICTION},
                    )
                    (debug / f"tab_race_{race.event_id}.json").write_text(
                        json.dumps(tab_race, indent=2, default=str), encoding="utf-8"
                    )
                    (debug / f"tab_form_{race.event_id}.json").write_text(
                        json.dumps(tab_form, indent=2, default=str), encoding="utf-8"
                    )
                    _, matched = enrich_with_tab(
                        selections, tab_race, tab_form, tab_match.name
                    )
                    tab_status = f"matched {matched}/{len(selections)} runners"
                except Exception as exc:
                    tab_status = f"unavailable ({type(exc).__name__})"

            scores = score_race(selections)
            report = format_race(race, scores, tab_status)
            print(report)
            reports.append(report)

            results_json.append({
                "event_id": race.event_id,
                "meeting": race.meeting,
                "race_number": race.race_number,
                "start_time": race.start_time.isoformat() if race.start_time else None,
                "tab_status": tab_status,
                "winner": compact_score(scores[0]) if scores else None,
                "ranking": [compact_score(x) for x in scores],
            })

        except Exception as exc:
            msg = (
                "\n" + "=" * 66
                + f"\nð {race.meeting} â R{race.race_number or '?'}"
                + f"\nâ Could not analyse race: {exc}"
            )
            print(msg)
            reports.append(msg)

        time.sleep(0.35)

    header = (
        "GREYHOUND AI PREDICTOR V1.1\n"
        f"Generated: {datetime.now(TZ).isoformat()}\n"
        "Prediction-only. Confidence is a model rating, not certainty.\n"
    )
    Path("predictions.txt").write_text(
        header + "\n".join(reports), encoding="utf-8"
    )
    Path("predictions.json").write_text(
        json.dumps(results_json, indent=2, default=str), encoding="utf-8"
    )

    print("\n" + "=" * 66)
    print("â COMPLETE â predictions.txt and predictions.json created.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

