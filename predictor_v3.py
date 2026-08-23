import json
import math
import os
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

API_URL = "https://api.puntersedge.online/v1/racing/next-to-go"
MOVERS_URL = "https://api.puntersedge.online/v1/racing/movers"
API_KEY = os.getenv("PUNTERSEDGE_API_KEY", "").strip()
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "Australia/Brisbane").strip()

try:
    MAX_DISPLAY = int(os.getenv("MAX_RACES", "8"))
except ValueError:
    MAX_DISPLAY = 8

MAX_DISPLAY = max(1, min(20, MAX_DISPLAY))
FETCH_RACES = 50

Path("debug").mkdir(exist_ok=True)


# ============================================================
# HELPERS
# ============================================================

def num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def save_json(path, data):
    Path(path).write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
            default=str
        ),
        encoding="utf-8",
    )


def local_time(value):
    if not value:
        return "Time N/A"

    try:
        dt = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00"
            )
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            ZoneInfo(BOT_TIMEZONE)
        ).strftime(
            "%a %d %b %I:%M %p"
        )

    except Exception:
        return str(value)


def race_label(race):
    distance = (
        f" · {race.get('distance_m')}m"
        if race.get("distance_m")
        else ""
    )

    return (
        f"{race.get('venue') or 'Unknown venue'} "
        f"R{race.get('race_number') or '?'}"
        f"{distance} · "
        f"{local_time(race.get('start_time'))}"
    )


# ============================================================
# FORM
# ============================================================

def form_positions(form):

    if form in (
        None,
        "",
        []
    ):
        return []

    if isinstance(
        form,
        list
    ):
        raw = form

    else:
        raw = re.findall(
            r"[1-8]",
            str(form)
        )

    out = []

    for value in raw:

        try:
            value = int(value)

            if 1 <= value <= 8:
                out.append(value)

        except (
            TypeError,
            ValueError
        ):
            pass

    return out[-5:]


def form_text(form):

    values = form_positions(
        form
    )

    if not values:
        return "N/A"

    return "".join(
        map(
            str,
            values
        )
    )


def form_score(form):

    values = form_positions(
        form
    )

    if not values:
        return 0.50

    score_map = {
        1: 1.00,
        2: 0.84,
        3: 0.70,
        4: 0.56,
        5: 0.43,
        6: 0.32,
        7: 0.23,
        8: 0.16,
    }

    weights = list(
        range(
            1,
            len(values) + 1
        )
    )

    return (
        sum(
            score_map[position] * weight
            for position, weight
            in zip(
                values,
                weights
            )
        )
        /
        sum(weights)
    )


# ============================================================
# BOOKMAKER PRICES
# ============================================================

def is_sportsbet(value):

    cleaned = re.sub(
        r"[^a-z0-9]",
        "",
        str(
            value or ""
        ).lower()
    )

    return (
        "sportsbet"
        in cleaned
    )


def fresh_quotes(runner):

    quotes = []

    for book in (
        runner.get(
            "bookmakers"
        )
        or []
    ):

        if not isinstance(
            book,
            dict
        ):
            continue

        price = num(
            book.get(
                "win_price"
            )
        )

        age = num(
            book.get(
                "age_seconds"
            )
        )

        if not price:
            continue

        if price <= 1:
            continue

        if book.get(
            "stale"
        ):
            continue

        if (
            age is not None
            and age > 120
        ):
            continue

        key = str(
            book.get("key")
            or book.get("name")
            or book.get("title")
            or "unknown"
        )

        name = str(
            book.get("title")
            or book.get("name")
            or book.get("key")
            or "Unknown"
        )

        quotes.append(
            {
                "key": key,
                "name": name,
                "price": price,
                "age": age,
            }
        )

    return quotes


# ============================================================
# API
# ============================================================

def api_get(
    url,
    params=None,
    required=True
):

    if not API_KEY:

        raise RuntimeError(
            "PUNTERSEDGE_API_KEY is missing. "
            "Check the GitHub repository secret."
        )

    response = requests.get(

        url,

        params=(
            params
            or {}
        ),

        headers={
            "X-API-Key":
                API_KEY,

            "Accept":
                "application/json",

            "User-Agent":
                "greyhound-ai-predictor/3.1",
        },

        timeout=30,
    )

    try:
        data = response.json()

    except ValueError:

        data = {
            "raw_text":
                response.text[:5000]
        }

    if response.status_code != 200:

        if required:

            raise RuntimeError(
                f"PuntersEdge HTTP "
                f"{response.status_code}: "
                f"{data}"
            )

        return (
            None,
            response.status_code
        )

    return (
        data,
        response.status_code
    )


def fetch_data():

    races, _ = api_get(

        API_URL,

        {
            "categories":
                "greyhound",

            "num_races":
                FETCH_RACES,
        },

        required=True,
    )

    save_json(
        "debug/puntersedge_races.json",
        races
    )

    # Movers are optional.
    # If the user's plan does not provide this endpoint,
    # the predictor continues without failing.

    movers, status = api_get(

        MOVERS_URL,

        {
            "categories":
                "greyhound"
        },

        required=False,
    )

    if movers is None:

        movers = []

        Path(
            "debug/movers_status.txt"
        ).write_text(

            (
                f"Movers unavailable "
                f"(HTTP {status}); "
                f"continued without "
                f"movement adjustment.\n"
            ),

            encoding="utf-8",
        )

    else:

        save_json(
            "debug/puntersedge_movers.json",
            movers
        )

    if not isinstance(
        races,
        list
    ):

        raise RuntimeError(
            "Unexpected next-to-go response; "
            "expected a list of races."
        )

    if not isinstance(
        movers,
        list
    ):
        movers = []

    return (
        races,
        movers
    )


# ============================================================
# AUSTRALIAN / INTERNATIONAL CLASSIFICATION
# ============================================================

def enriched_race(race):

    country = str(
        race.get(
            "country"
        )
        or ""
    ).upper()

    if country in {
        "AU",
        "NZ"
    }:
        return True

    if country:
        return False

    # Some Australian races can temporarily
    # arrive with no country label.
    #
    # Presence of form/barrier/trainer data
    # strongly suggests an enriched AU/NZ race.

    return any(

        runner.get(
            "form"
        )
        not in (
            None,
            "",
            []
        )

        or

        runner.get(
            "barrier"
        )
        is not None

        or

        runner.get(
            "box"
        )
        is not None

        or

        runner.get(
            "trainer"
        )
        not in (
            None,
            ""
        )

        for runner
        in (
            race.get(
                "runners"
            )
            or []
        )
    )


# ============================================================
# MARKET MOVEMENT
# ============================================================

def mover_key(
    venue,
    race_number,
    runner_name
):

    return (

        str(
            venue or ""
        ).strip().lower(),

        str(
            race_number or ""
        ).strip(),

        str(
            runner_name or ""
        ).strip().lower(),
    )


def build_mover_index(
    movers
):

    index = {}

    for item in movers:

        if not isinstance(
            item,
            dict
        ):
            continue

        venue = item.get(
            "venue"
        )

        race_number = item.get(
            "race_number"
        )

        runner = (

            item.get(
                "runner"
            )

            or

            item.get(
                "runner_name"
            )

            or

            item.get(
                "name"
            )
        )

        if (
            venue
            and runner
        ):

            index[
                mover_key(
                    venue,
                    race_number,
                    runner
                )
            ] = item

    return index


def mover_pct(item):

    if not item:
        return None

    for field in (
        "move_pct",
        "consensus_move_pct",
        "movement_pct",
        "pct_change",
    ):

        value = num(
            item.get(
                field
            )
        )

        if value is not None:
            return value

    return None


# ============================================================
# RUNNER DATA
# ============================================================

def runner_rows(race):

    rows = []

    for runner in (
        race.get(
            "runners"
        )
        or []
    ):

        quotes = fresh_quotes(
            runner
        )

        if not quotes:
            continue

        rows.append(
            {
                "number":
                    runner.get(
                        "number"
                    ),

                "name":
                    str(
                        runner.get(
                            "name"
                        )
                        or
                        "Unknown Runner"
                    ),

                "box":
                    (
                        runner.get(
                            "barrier"
                        )

                        if runner.get(
                            "barrier"
                        )
                        is not None

                        else

                        runner.get(
                            "box"
                        )
                    ),

                "form":
                    runner.get(
                        "form"
                    ),

                "form_score":
                    form_score(
                        runner.get(
                            "form"
                        )
                    ),

                "quotes":
                    quotes,
            }
        )

    return rows


# ============================================================
# FAIR MARKET PROBABILITIES
# ============================================================

def fair_market_probabilities(
    runners
):

    n = len(
        runners
    )

    by_book = {}

    for runner_index, runner in enumerate(
        runners
    ):

        for quote in runner[
            "quotes"
        ]:

            by_book.setdefault(
                quote[
                    "key"
                ],
                {}
            )[
                runner_index
            ] = quote[
                "price"
            ]

    samples = {

        i: []

        for i
        in range(n)
    }

    # De-vig every bookmaker market separately.

    for prices in by_book.values():

        if (
            len(prices)
            /
            n
            <
            0.70
        ):
            continue

        implied = {

            i:
                1 / price

            for i, price
            in prices.items()
        }

        total = sum(
            implied.values()
        )

        if total <= 0:
            continue

        for i, probability in (
            implied.items()
        ):

            samples[i].append(
                probability
                /
                total
            )

    raw = []

    for i, runner in enumerate(
        runners
    ):

        if samples[i]:

            raw.append(

                statistics.median(
                    samples[i]
                )

            )

        else:

            median_price = (
                statistics.median(

                    quote[
                        "price"
                    ]

                    for quote
                    in runner[
                        "quotes"
                    ]
                )
            )

            raw.append(
                1
                /
                median_price
            )

    total = sum(
        raw
    )

    if total <= 0:

        return (
            None,
            None
        )

    probabilities = [

        value
        /
        total

        for value
        in raw
    ]

    return (
        probabilities,
        samples
    )


# ============================================================
# MODEL
# ============================================================

def analyse_race(
    race,
    mover_index,
    mode
):

    runners = runner_rows(
        race
    )

    if len(
        runners
    ) < 2:

        return None

    market_probs, fair_samples = (
        fair_market_probabilities(
            runners
        )
    )

    if not market_probs:
        return None

    form_raw = [

        max(
            0.05,
            runner[
                "form_score"
            ]
        )

        for runner
        in runners
    ]

    form_total = sum(
        form_raw
    )

    form_probs = [

        value
        /
        form_total

        for value
        in form_raw
    ]

    raw_model = []

    for i, runner in enumerate(
        runners
    ):

        if mode == "full":

            # Market remains the dominant signal.

            base = (

                0.84
                *
                market_probs[i]

                +

                0.16
                *
                form_probs[i]
            )

            movement = (
                mover_index.get(

                    mover_key(

                        race.get(
                            "venue"
                        ),

                        race.get(
                            "race_number"
                        ),

                        runner[
                            "name"
                        ]
                    )
                )
            )

            move = mover_pct(
                movement
            )

            runner[
                "move_pct"
            ] = move

            if move is None:

                multiplier = 1.0

            else:

                # PuntersEdge:
                # negative = firming
                # positive = drifting

                signal = max(

                    -1.0,

                    min(

                        1.0,

                        -move
                        /
                        20.0
                    )
                )

                multiplier = (

                    1.0

                    +

                    0.06
                    *
                    signal
                )

            raw_model.append(

                base
                *
                multiplier
            )

        else:

            # International fallback.
            # Market only.

            runner[
                "move_pct"
            ] = None

            raw_model.append(
                market_probs[i]
            )

    total = sum(
        raw_model
    )

    if total <= 0:
        return None

    probs = [

        value
        /
        total

        for value
        in raw_model
    ]

    # ========================================================
    # ADD PRICE INFORMATION
    # ========================================================

    for i, runner in enumerate(
        runners
    ):

        best = max(

            runner[
                "quotes"
            ],

            key=lambda quote:
                quote[
                    "price"
                ],
        )

        sportsbet = next(

            (

                quote

                for quote
                in runner[
                    "quotes"
                ]

                if is_sportsbet(

                    quote[
                        "key"
                    ]

                    +

                    " "

                    +

                    quote[
                        "name"
                    ]
                )
            ),

            None,
        )

        ages = [

            quote[
                "age"
            ]

            for quote
            in runner[
                "quotes"
            ]

            if quote[
                "age"
            ]
            is not None
        ]

        samples = (
            fair_samples.get(
                i,
                []
            )
        )

        runner[
            "prob"
        ] = probs[i]

        runner[
            "fair_price"
        ] = (
            1
            /
            probs[i]
        )

        runner[
            "best_price"
        ] = best[
            "price"
        ]

        runner[
            "best_book"
        ] = best[
            "name"
        ]

        runner[
            "sportsbet_price"
        ] = (

            sportsbet[
                "price"
            ]

            if sportsbet

            else None
        )

        runner[
            "book_count"
        ] = len(
            runner[
                "quotes"
            ]
        )

        runner[
            "mean_age"
        ] = (

            statistics.mean(
                ages
            )

            if ages

            else None
        )

        runner[
            "book_probability_sd"
        ] = (

            statistics.pstdev(
                samples
            )

            if len(
                samples
            ) > 1

            else None
        )

        runner[
            "model_edge_pct"
        ] = (

            (
                probs[i]
                *
                best[
                    "price"
                ]

                -

                1
            )

            *
            100
        )

    runners.sort(

        key=lambda runner:
            runner[
                "prob"
            ],

        reverse=True,
    )

    winner = runners[0]
    second = runners[1]

    gap = (

        winner[
            "prob"
        ]

        -

        second[
            "prob"
        ]
    )

    # ========================================================
    # FULL AU/NZ CONFIDENCE
    # ========================================================

    if mode == "full":

        coverage = min(

            1.0,

            winner[
                "book_count"
            ]
            /
            8.0
        )

        freshness = (

            0.75

            if winner[
                "mean_age"
            ]
            is None

            else

            max(

                0.0,

                1.0

                -

                winner[
                    "mean_age"
                ]
                /
                120.0
            )
        )

        agreement = (

            0.45

            if winner[
                "book_probability_sd"
            ]
            is None

            else

            max(

                0.0,

                min(

                    1.0,

                    1.0

                    -

                    winner[
                        "book_probability_sd"
                    ]
                    /
                    0.08
                )
            )
        )

        has_form = (

            winner[
                "form"
            ]

            not in (
                None,
                "",
                []
            )
        )

        confidence = (

            34

            +

            winner[
                "prob"
            ]
            *
            48

            +

            gap
            *
            75

            +

            coverage
            *
            8

            +

            freshness
            *
            4

            +

            agreement
            *
            4

            +

            (
                3
                if has_form
                else 0
            )
        )

        confidence = int(

            round(

                max(

                    48,

                    min(
                        90,
                        confidence
                    )
                )
            )
        )

        if confidence >= 80:
            verdict = "STRONG PICK"

        elif confidence >= 70:
            verdict = "GOOD PICK"

        elif confidence >= 60:
            verdict = "LEAN"

        else:
            verdict = "LOW CONFIDENCE"

        strength = (

            confidence

            +

            min(

                5,

                winner[
                    "book_count"
                ]
                *
                0.45
            )

            +

            min(

                5,

                gap
                *
                35
            )
        )

    # ========================================================
    # INTERNATIONAL CONFIDENCE
    # ========================================================

    else:

        coverage = min(

            1.0,

            winner[
                "book_count"
            ]
            /
            3.0
        )

        confidence = (

            34

            +

            winner[
                "prob"
            ]
            *
            30

            +

            gap
            *
            38

            +

            coverage
            *
            4
        )

        confidence = int(

            round(

                max(

                    42,

                    min(
                        68,
                        confidence
                    )
                )
            )
        )

        verdict = (

            "MARKET LEAN"

            if winner[
                "book_count"
            ] >= 2

            else

            "LOW-DATA MARKET LEAN"
        )

        strength = (

            confidence

            +

            min(
                3,
                gap * 25
            )
        )

    return {
        "race":
            race,

        "winner":
            winner,

        "top3":
            runners[:3],

        "confidence":
            confidence,

        "verdict":
            verdict,

        "strength":
            strength,

        "mode":
            mode,
    }


# ============================================================
# OUTPUT HELPERS
# ============================================================

def price_text(value):

    return (

        f"${value:.2f}"

        if value
        is not None

        else

        "No fresh quote"
    )


def edge_label(edge):

    if edge >= 8:

        return (
            "POSITIVE MODEL/PRICE GAP"
        )

    if edge >= 3:

        return (
            "SMALL MODEL/PRICE GAP"
        )

    return (
        "NO MODEL/PRICE GAP"
    )


# ============================================================
# GITHUB MARKDOWN SUMMARY
# ============================================================

def markdown_output(
    results,
    mode,
    movers_available
):

    now = datetime.now(

        ZoneInfo(
            BOT_TIMEZONE
        )

    ).strftime(
        "%d %b %Y, %I:%M %p %Z"
    )

    # No title here because predict.yml
    # already adds the V3 heading.

    lines = [

        f"Generated **{now}**",

        "",
    ]

    if not results:

        return "\n".join(

            lines

            +

            [
                (
                    "## No usable upcoming "
                    "greyhound races found"
                ),

                "",

                (
                    "The feed returned races, "
                    "but none had enough fresh "
                    "runner prices to build "
                    "a prediction."
                ),
            ]
        )

    best = results[0]
    winner = best[
        "winner"
    ]

    # ========================================================
    # FULL AU/NZ
    # ========================================================

    if mode == "full":

        lines += [

            "## 🇦🇺 FULL AU/NZ MODEL",

            "",

            (
                "Uses fresh bookmaker prices "
                "plus recent form. Market movement "
                "is added when the optional movers "
                "feed is available."
            ),

            "",
        ]

        if not movers_available:

            lines += [

                (
                    "> Market movers were unavailable "
                    "on this run, so the model continued "
                    "without that adjustment."
                ),

                "",
            ]

        best_heading = (
            "## 🏆 BEST AU/NZ PICK"
        )

    # ========================================================
    # INTERNATIONAL FALLBACK
    # ========================================================

    else:

        lines += [

            (
                "## 🌍 INTERNATIONAL FALLBACK "
                "— MARKET ONLY"
            ),

            "",

            (
                "> No suitable AU/NZ race was in "
                "the current next-to-go window. "
                "Foreign racing usually has thinner "
                "bookmaker coverage and no AU/NZ "
                "form/box enrichment, so confidence "
                "is capped."
            ),

            "",
        ]

        best_heading = (
            "## 🏆 BEST INTERNATIONAL MARKET LEAN"
        )

    # ========================================================
    # BEST PICK
    # ========================================================

    lines += [

        best_heading,

        "",

        (
            f"### "
            f"#{winner['number'] or '?'} "
            f"**{winner['name'].upper()}**"
        ),

        (
            f"**"
            f"{race_label(best['race'])}"
            f"**"
        ),

        "",

        (
            f"- **Estimated win chance:** "
            f"{winner['prob'] * 100:.1f}%"
        ),

        (
            f"- **Confidence index:** "
            f"{best['confidence']}/100 "
            f"— **{best['verdict']}**"
        ),

        (
            f"- **Sportsbet:** "
            f"{price_text(winner['sportsbet_price'])}"
        ),

        (
            f"- **Best available:** "
            f"${winner['best_price']:.2f} "
            f"({winner['best_book']})"
        ),

        (
            f"- **Market/model fair price:** "
            f"${winner['fair_price']:.2f}"
        ),

        (
            f"- **Fresh bookmaker quotes:** "
            f"{winner['book_count']}"
        ),
    ]

    # ========================================================
    # EXTRA AU/NZ INFORMATION
    # ========================================================

    if mode == "full":

        lines += [

            (
                f"- **Box:** "
                f"{winner['box'] "
                f"if winner['box'] is not None "
                f"else 'N/A'}"
            ),

            (
                f"- **Recent form:** "
                f"`{form_text(winner['form'])}`"
            ),

            (
                f"- **Price vs model:** "
                f"{winner['model_edge_pct']:+.1f}% "
                f"— **"
                f"{edge_label(winner['model_edge_pct'])}"
                f"**"
            ),
        ]

        if winner[
            "move_pct"
        ] is not None:

            direction = (

                "firming"

                if winner[
                    "move_pct"
                ] < 0

                else

                "drifting"

                if winner[
                    "move_pct"
                ] > 0

                else

                "flat"
            )

            lines.append(

                (
                    f"- **Market move:** "
                    f"{winner['move_pct']:+.1f}% "
                    f"({direction})"
                )
            )

    # ========================================================
    # INTERNATIONAL DATA WARNING
    # ========================================================

    else:

        lines += [

            (
                f"- **Data quality:** "
                f"{'Multi-book market' "
                f"if winner['book_count'] >= 2 "
                f"else 'Single-book market'}"
            ),

            (
                "- **Value signal:** Not shown for "
                "international fallback because the "
                "model is derived almost entirely "
                "from those same market prices."
            ),
        ]

    # ========================================================
    # RANKED RACES
    # ========================================================

    lines += [

        "",

        "---",

        "",

        (
            "## 📊 RACES RANKED "
            "STRONGEST → WEAKEST"
        ),

        "",
    ]

    for position, result in enumerate(
        results,
        1
    ):

        race = result[
            "race"
        ]

        winner = result[
            "winner"
        ]

        lines += [

            (
                f"### {position}. "
                f"{race_label(race)}"
            ),

            (
                f"🏆 **"
                f"#{winner['number'] or '?'} "
                f"{winner['name']}"
                f"**"
            ),

            "",

            (
                f"**Win estimate:** "
                f"{winner['prob'] * 100:.1f}% "
                f"· **Confidence:** "
                f"{result['confidence']}/100 "
                f"· **{result['verdict']}**"
            ),

            "",

            (
                f"Sportsbet **"
                f"{price_text(winner['sportsbet_price'])}"
                f"** · "
                f"Best **"
                f"${winner['best_price']:.2f} "
                f"({winner['best_book']})"
                f"** · "
                f"Fair **"
                f"${winner['fair_price']:.2f}"
                f"**"
            ),

            "",

            "**Top 3**",
        ]

        for rank, runner in enumerate(
            result[
                "top3"
            ],
            1
        ):

            extra = ""

            if mode == "full":

                box = (

                    runner[
                        "box"
                    ]

                    if runner[
                        "box"
                    ]
                    is not None

                    else "N/A"
                )

                extra = (

                    f" · box {box} "
                    f"· form "
                    f"`{form_text(runner['form'])}`"
                )

            lines.append(

                (
                    f"{rank}. "
                    f"**#{runner['number'] or '?'} "
                    f"{runner['name']}** "
                    f"— "
                    f"{runner['prob'] * 100:.1f}% "
                    f"· best "
                    f"${runner['best_price']:.2f}"
                    f"{extra}"
                )
            )

        lines += [

            "",

            "---",

            "",
        ]

    lines += [

        "### Model notes",

        "",

        (
            "- Quotes marked stale, or older "
            "than 120 seconds, are excluded."
        ),

        (
            "- The probability figure is a model "
            "estimate, not a guaranteed or historically "
            "calibrated win probability."
        ),

        (
            "- Box is displayed for AU/NZ races but "
            "is not given a generic bonus; a useful "
            "box effect needs track-and-distance-specific "
            "history."
        ),

        (
            "- International fallback is intentionally "
            "lower confidence because foreign markets "
            "commonly have much thinner coverage."
        ),
    ]

    return "\n".join(
        lines
    )


# ============================================================
# PLAIN TEXT OUTPUT
# ============================================================

def text_output(
    results,
    mode
):

    if not results:

        return (
            "GREYHOUND AI PREDICTOR V3.1\n"
            "No usable upcoming greyhound races found.\n"
        )

    best = results[0]
    winner = best[
        "winner"
    ]

    mode_text = (

        "FULL AU/NZ MODEL"

        if mode == "full"

        else

        "INTERNATIONAL MARKET-ONLY FALLBACK"
    )

    lines = [

        "GREYHOUND AI PREDICTOR V3.1",

        mode_text,

        "",

        "BEST PICK",

        race_label(
            best[
                "race"
            ]
        ),

        (
            f"WINNER: "
            f"#{winner['number'] or '?'} "
            f"{winner['name']}"
        ),

        (
            f"Estimated win chance: "
            f"{winner['prob'] * 100:.1f}%"
        ),

        (
            f"Confidence: "
            f"{best['confidence']}/100 "
            f"- {best['verdict']}"
        ),

        (
            f"Sportsbet: "
            f"{price_text(winner['sportsbet_price'])}"
        ),

        (
            f"Best available: "
            f"${winner['best_price']:.2f} "
            f"({winner['best_book']})"
        ),

        (
            f"Fair price: "
            f"${winner['fair_price']:.2f}"
        ),

        "",

        "RANKED PICKS",
    ]

    for position, result in enumerate(
        results,
        1
    ):

        runner = result[
            "winner"
        ]

        lines.append(

            (
                f"{position}. "
                f"{race_label(result['race'])} "
                f"| "
                f"#{runner['number'] or '?'} "
                f"{runner['name']} "
                f"| "
                f"{runner['prob'] * 100:.1f}% "
                f"| "
                f"{result['confidence']}/100"
            )
        )

    return (
        "\n".join(
            lines
        )
        +
        "\n"
    )


# ============================================================
# JSON OUTPUT
# ============================================================

def json_output(
    results,
    mode
):

    output = []

    for result in results:

        race = result[
            "race"
        ]

        winner = result[
            "winner"
        ]

        output.append(

            {
                "mode":
                    mode,

                "race_id":
                    (
                        race.get(
                            "race_id"
                        )

                        or

                        race.get(
                            "id"
                        )
                    ),

                "venue":
                    race.get(
                        "venue"
                    ),

                "race_number":
                    race.get(
                        "race_number"
                    ),

                "country":
                    race.get(
                        "country"
                    ),

                "start_time":
                    race.get(
                        "start_time"
                    ),

                "distance_m":
                    race.get(
                        "distance_m"
                    ),

                "prediction":
                    {
                        "number":
                            winner[
                                "number"
                            ],

                        "name":
                            winner[
                                "name"
                            ],

                        "estimated_win_probability":
                            round(
                                winner[
                                    "prob"
                                ],
                                4
                            ),

                        "confidence_index":
                            result[
                                "confidence"
                            ],

                        "verdict":
                            result[
                                "verdict"
                            ],

                        "sportsbet_price":
                            winner[
                                "sportsbet_price"
                            ],

                        "best_price":
                            round(
                                winner[
                                    "best_price"
                                ],
                                2
                            ),

                        "best_bookmaker":
                            winner[
                                "best_book"
                            ],

                        "fair_price":
                            round(
                                winner[
                                    "fair_price"
                                ],
                                2
                            ),

                        "fresh_bookmaker_quotes":
                            winner[
                                "book_count"
                            ],

                        "box":
                            winner[
                                "box"
                            ],

                        "form":
                            form_text(
                                winner[
                                    "form"
                                ]
                            ),

                        "market_move_pct":
                            winner[
                                "move_pct"
                            ],

                        "model_price_gap_pct":
                            round(
                                winner[
                                    "model_edge_pct"
                                ],
                                2
                            ),
                    },
            }
        )

    return output


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "🐕 GREYHOUND AI PREDICTOR V3.1"
    )

    print(
        "Prediction-only: "
        "this program does not place bets."
    )

    try:

        races, movers = fetch_data()

        mover_index = (
            build_mover_index(
                movers
            )
        )

        full_results = []
        fallback_results = []

        # ====================================================
        # ANALYSE EVERY UPCOMING RACE
        # ====================================================

        for race in races:

            if not isinstance(
                race,
                dict
            ):
                continue

            if enriched_race(
                race
            ):

                result = analyse_race(

                    race,

                    mover_index,

                    "full"
                )

                if result:

                    full_results.append(
                        result
                    )

            else:

                result = analyse_race(

                    race,

                    mover_index,

                    "market"
                )

                if result:

                    fallback_results.append(
                        result
                    )

        # ====================================================
        # AU/NZ ALWAYS HAS PRIORITY
        # ====================================================

        if full_results:

            mode = "full"

            full_results.sort(

                key=lambda result:
                    result[
                        "strength"
                    ],

                reverse=True,
            )

            results = (
                full_results[
                    :MAX_DISPLAY
                ]
            )

        # ====================================================
        # NO AU/NZ? USE INTERNATIONAL FALLBACK
        # ====================================================

        else:

            mode = "market"

            fallback_results.sort(

                key=lambda result:
                    result[
                        "strength"
                    ],

                reverse=True,
            )

            results = (
                fallback_results[
                    :MAX_DISPLAY
                ]
            )

        # ====================================================
        # SAVE OUTPUT
        # ====================================================

        markdown = markdown_output(

            results,

            mode,

            bool(
                movers
            )
        )

        Path(
            "predictions.md"
        ).write_text(

            markdown
            +
            "\n",

            encoding="utf-8",
        )

        Path(
            "predictions.txt"
        ).write_text(

            text_output(
                results,
                mode
            ),

            encoding="utf-8",
        )

        save_json(

            "predictions.json",

            json_output(
                results,
                mode
            )
        )

        print(

            text_output(
                results,
                mode
            )
        )

        return 0

    except Exception as error:

        message = (
            f"ERROR: "
            f"{error}"
        )

        print(
            message
        )

        Path(
            "predictions.md"
        ).write_text(

            (
                "## Predictor error\n\n"
                f"`{message}`\n"
            ),

            encoding="utf-8",
        )

        Path(
            "predictions.txt"
        ).write_text(

            message
            +
            "\n",

            encoding="utf-8",
        )

        save_json(

            "predictions.json",

            {
                "error":
                    str(error)
            }
        )

        return 2


if __name__ == "__main__":

    sys.exit(
        main()
    )
