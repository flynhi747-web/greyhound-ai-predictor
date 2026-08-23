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


# ============================================================
# GREYHOUND AI PREDICTOR V3.2
# ============================================================

API_URL = "https://api.puntersedge.online/v1/racing/next-to-go"

API_KEY = os.getenv(
    "PUNTERSEDGE_API_KEY",
    ""
).strip()

BOT_TIMEZONE = os.getenv(
    "BOT_TIMEZONE",
    "Australia/Brisbane"
).strip()

try:
    MAX_DISPLAY = int(
        os.getenv(
            "MAX_RACES",
            "8"
        )
        or "8"
    )
except ValueError:
    MAX_DISPLAY = 8


MAX_DISPLAY = max(
    1,
    min(
        20,
        MAX_DISPLAY
    )
)

FETCH_RACES = 50

STALE_AFTER_SECONDS = 120

Path(
    "debug"
).mkdir(
    exist_ok=True
)


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_num(value):

    try:

        number = float(
            value
        )

        return (
            number
            if math.isfinite(
                number
            )
            else None
        )

    except (
        TypeError,
        ValueError
    ):

        return None


def save_json(
    path,
    data
):

    Path(
        path
    ).write_text(

        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
            default=str
        ),

        encoding="utf-8"
    )


def local_time(value):

    if not value:

        return "Time N/A"

    try:

        dt = datetime.fromisoformat(

            str(
                value
            ).replace(
                "Z",
                "+00:00"
            )

        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(

            ZoneInfo(
                BOT_TIMEZONE
            )

        ).strftime(
            "%a %d %b %I:%M %p"
        )

    except Exception:

        return str(
            value
        )


def race_title(
    race
):

    distance = race.get(
        "distance_m"
    )

    distance_text = (

        f" · {distance}m"

        if distance

        else ""
    )

    return (

        f"{race.get('venue') or 'Unknown venue'} "
        f"R{race.get('race_number') or '?'}"
        f"{distance_text} · "
        f"{local_time(race.get('start_time'))}"

    )


# ============================================================
# RECENT FORM
# ============================================================

def form_positions(
    form
):

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
            str(
                form
            )
        )

    positions = []

    for value in raw:

        try:

            position = int(
                value
            )

        except (
            TypeError,
            ValueError
        ):

            continue

        if (
            1
            <= position
            <= 8
        ):

            positions.append(
                position
            )

    return positions[
        -5:
    ]


def form_text(
    form
):

    positions = form_positions(
        form
    )

    return (

        "".join(
            map(
                str,
                positions
            )
        )

        if positions

        else "N/A"

    )


def form_score(
    form
):

    positions = form_positions(
        form
    )

    if not positions:

        return 0.50


    finish_value = {

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
            len(
                positions
            )
            + 1
        )

    )


    weighted_total = sum(

        finish_value[
            position
        ]
        *
        weight

        for position, weight
        in zip(
            positions,
            weights
        )

    )


    return (

        weighted_total
        /
        sum(
            weights
        )

    )


# ============================================================
# BOOKMAKER HELPERS
# ============================================================

def is_sportsbet(
    quote
):

    text = " ".join(

        [

            str(
                quote.get(
                    "key"
                )
                or ""
            ),

            str(
                quote.get(
                    "name"
                )
                or ""
            ),

        ]

    )


    cleaned = re.sub(

        r"[^a-z0-9]",

        "",

        text.lower()

    )


    return (

        "sportsbet"
        in cleaned

    )


def fresh_quotes(
    runner
):

    quotes = []

    bookmakers = (

        runner.get(
            "bookmakers"
        )

        or []

    )


    for bookmaker in bookmakers:

        if not isinstance(
            bookmaker,
            dict
        ):

            continue


        price = safe_num(

            bookmaker.get(
                "win_price"
            )

        )


        age = safe_num(

            bookmaker.get(
                "age_seconds"
            )

        )


        if (
            price is None
            or price <= 1
        ):

            continue


        if bookmaker.get(
            "stale"
        ) is True:

            continue


        if (

            age is not None

            and

            age
            >
            STALE_AFTER_SECONDS

        ):

            continue


        key = str(

            bookmaker.get(
                "key"
            )

            or

            bookmaker.get(
                "name"
            )

            or

            bookmaker.get(
                "title"
            )

            or

            "unknown"

        )


        name = str(

            bookmaker.get(
                "title"
            )

            or

            bookmaker.get(
                "name"
            )

            or

            bookmaker.get(
                "key"
            )

            or

            "Unknown bookmaker"

        )


        quotes.append(

            {

                "key":
                    key,

                "name":
                    name,

                "price":
                    price,

                "age_seconds":
                    age,

            }

        )


    return quotes


# ============================================================
# FETCH LIVE RACES
# ============================================================

def fetch_races():

    if not API_KEY:

        raise RuntimeError(

            "PUNTERSEDGE_API_KEY is missing. "
            "Check the GitHub repository secret "
            "and workflow."

        )


    response = requests.get(

        API_URL,

        params={

            "categories":
                "greyhound",

            "num_races":
                FETCH_RACES,

        },

        headers={

            "X-API-Key":
                API_KEY,

            "Accept":
                "application/json",

            "User-Agent":
                "greyhound-ai-predictor/3.2",

        },

        timeout=30,

    )


    try:

        data = response.json()

    except ValueError:

        data = {

            "raw_text":
                response.text[
                    :5000
                ]

        }


    save_json(

        "debug/puntersedge_response.json",

        data

    )


    if response.status_code != 200:

        raise RuntimeError(

            f"PuntersEdge HTTP "
            f"{response.status_code}: "
            f"{data}"

        )


    if not isinstance(
        data,
        list
    ):

        raise RuntimeError(

            "Unexpected PuntersEdge response. "
            "Expected a list of races."

        )


    return data


# ============================================================
# AUSTRALIA / NZ IDENTIFICATION
# ============================================================

def is_full_data_race(
    race
):

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


    runners = (

        race.get(
            "runners"
        )

        or []

    )


    if not runners:

        return False


    enriched_count = 0


    for runner in runners:

        if not isinstance(
            runner,
            dict
        ):

            continue


        has_form = (

            runner.get(
                "form"
            )

            not in (
                None,
                "",
                []
            )

        )


        has_box = (

            runner.get(
                "barrier"
            )
            is not None

            or

            runner.get(
                "box"
            )
            is not None

        )


        if (
            has_form
            and
            has_box
        ):

            enriched_count += 1


    required_count = max(

        2,

        len(
            runners
        )
        // 2

    )


    return (

        enriched_count
        >=
        required_count

    )


# ============================================================
# BUILD RUNNER DATA
# ============================================================

def build_runner_rows(
    race
):

    rows = []


    for runner in (

        race.get(
            "runners"
        )

        or []

    ):

        if not isinstance(
            runner,
            dict
        ):

            continue


        quotes = fresh_quotes(
            runner
        )


        if not quotes:

            continue


        if runner.get(
            "barrier"
        ) is not None:

            box_value = runner.get(
                "barrier"
            )

        else:

            box_value = runner.get(
                "box"
            )


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
                    box_value,

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
# DEVIG BOOKMAKER MARKETS
# ============================================================

def market_probabilities(
    runners
):

    runner_count = len(
        runners
    )


    bookmaker_markets = {}


    for runner_index, runner in enumerate(
        runners
    ):

        for quote in runner[
            "quotes"
        ]:

            bookmaker_markets.setdefault(

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

        for i in range(
            runner_count
        )

    }


    for prices in bookmaker_markets.values():

        coverage = (

            len(
                prices
            )

            /

            runner_count

        )


        if coverage < 0.70:

            continue


        implied = {

            runner_index:

                1
                /
                price

            for runner_index, price
            in prices.items()

        }


        overround = sum(
            implied.values()
        )


        if overround <= 0:

            continue


        for runner_index, implied_probability in implied.items():

            samples[
                runner_index
            ].append(

                implied_probability
                /
                overround

            )


    raw_probabilities = []


    for runner_index, runner in enumerate(
        runners
    ):

        if samples[
            runner_index
        ]:

            probability = statistics.median(

                samples[
                    runner_index
                ]

            )

        else:

            median_price = statistics.median(

                quote[
                    "price"
                ]

                for quote
                in runner[
                    "quotes"
                ]

            )


            probability = (

                1
                /
                median_price

            )


        raw_probabilities.append(
            probability
        )


    total = sum(
        raw_probabilities
    )


    if total <= 0:

        return (
            None,
            None
        )


    probabilities = [

        probability
        /
        total

        for probability
        in raw_probabilities

    ]


    return (

        probabilities,
        samples

    )


# ============================================================
# ANALYSE ONE RACE
# ============================================================

def analyse_race(
    race,
    mode
):

    runners = build_runner_rows(
        race
    )


    if len(
        runners
    ) < 2:

        return None


    market_probs, samples = (
        market_probabilities(
            runners
        )
    )


    if not market_probs:

        return None


    # ========================================================
    # FULL AU/NZ MODEL
    # 85% MARKET
    # 15% RECENT FORM
    # ========================================================

    if mode == "full":

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


        raw_model = [

            0.85
            *
            market_probs[
                i
            ]

            +

            0.15
            *
            form_probs[
                i
            ]

            for i
            in range(
                len(
                    runners
                )
            )

        ]


        model_total = sum(
            raw_model
        )


        model_probs = [

            probability
            /
            model_total

            for probability
            in raw_model

        ]


    # ========================================================
    # INTERNATIONAL
    # MARKET ONLY
    # ========================================================

    else:

        model_probs = (
            market_probs
        )


    # ========================================================
    # ADD PRICES / BOOKMAKER DATA
    # ========================================================

    for runner_index, runner in enumerate(
        runners
    ):

        best_quote = max(

            runner[
                "quotes"
            ],

            key=lambda quote:

                quote[
                    "price"
                ]

        )


        sportsbet_quote = next(

            (

                quote

                for quote
                in runner[
                    "quotes"
                ]

                if is_sportsbet(
                    quote
                )

            ),

            None

        )


        quote_ages = [

            quote[
                "age_seconds"
            ]

            for quote
            in runner[
                "quotes"
            ]

            if quote[
                "age_seconds"
            ]
            is not None

        ]


        probability_samples = (

            samples.get(
                runner_index,
                []
            )

        )


        runner[
            "probability"
        ] = model_probs[
            runner_index
        ]


        runner[
            "fair_price"
        ] = (

            1
            /
            model_probs[
                runner_index
            ]

        )


        runner[
            "best_price"
        ] = best_quote[
            "price"
        ]


        runner[
            "best_bookmaker"
        ] = best_quote[
            "name"
        ]


        runner[
            "sportsbet_price"
        ] = (

            sportsbet_quote[
                "price"
            ]

            if sportsbet_quote

            else None

        )


        runner[
            "bookmaker_count"
        ] = len(

            runner[
                "quotes"
            ]

        )


        runner[
            "mean_quote_age"
        ] = (

            statistics.mean(
                quote_ages
            )

            if quote_ages

            else None

        )


        runner[
            "book_probability_sd"
        ] = (

            statistics.pstdev(
                probability_samples
            )

            if len(
                probability_samples
            ) > 1

            else None

        )


        runner[
            "price_gap_pct"
        ] = (

            (

                model_probs[
                    runner_index
                ]

                *

                best_quote[
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
                "probability"
            ],

        reverse=True

    )


    winner = runners[
        0
    ]


    second = runners[
        1
    ]


    probability_gap = (

        winner[
            "probability"
        ]

        -

        second[
            "probability"
        ]

    )


    # ========================================================
    # FULL MODEL CONFIDENCE
    # ========================================================

    if mode == "full":

        coverage_score = min(

            1.0,

            winner[
                "bookmaker_count"
            ]

            /

            8.0

        )


        freshness_score = (

            0.75

            if winner[
                "mean_quote_age"
            ]
            is None

            else

            max(

                0.0,

                1.0

                -

                winner[
                    "mean_quote_age"
                ]

                /

                STALE_AFTER_SECONDS

            )

        )


        agreement_score = (

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
                "probability"
            ]
            *
            48

            +

            probability_gap
            *
            75

            +

            coverage_score
            *
            8

            +

            freshness_score
            *
            4

            +

            agreement_score
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

            verdict = (
                "STRONG PICK"
            )

        elif confidence >= 70:

            verdict = (
                "GOOD PICK"
            )

        elif confidence >= 60:

            verdict = (
                "LEAN"
            )

        else:

            verdict = (
                "LOW CONFIDENCE"
            )


        race_strength = (

            confidence

            +

            min(

                5,

                winner[
                    "bookmaker_count"
                ]
                *
                0.45

            )

            +

            min(

                5,

                probability_gap
                *
                35

            )

        )


    # ========================================================
    # INTERNATIONAL CONFIDENCE
    # ========================================================

    else:

        coverage_score = min(

            1.0,

            winner[
                "bookmaker_count"
            ]

            /

            3.0

        )


        confidence = (

            34

            +

            winner[
                "probability"
            ]
            *
            30

            +

            probability_gap
            *
            38

            +

            coverage_score
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
                "bookmaker_count"
            ] >= 2

            else

            "LOW-DATA MARKET LEAN"

        )


        race_strength = (

            confidence

            +

            min(

                3,

                probability_gap
                *
                25

            )

        )


    return {

        "race":
            race,

        "mode":
            mode,

        "winner":
            winner,

        "top3":
            runners[
                :3
            ],

        "confidence":
            confidence,

        "verdict":
            verdict,

        "race_strength":
            race_strength,

    }


# ============================================================
# DISPLAY HELPERS
# ============================================================

def price_text(
    value
):

    return (

        f"${value:.2f}"

        if value
        is not None

        else

        "No fresh quote"

    )


# ============================================================
# GITHUB SUMMARY
# ============================================================

def markdown_output(
    results,
    mode
):

    now = datetime.now(

        ZoneInfo(
            BOT_TIMEZONE
        )

    ).strftime(
        "%d %b %Y, %I:%M %p %Z"
    )


    lines = [

        f"Generated **{now}**",

        "",

    ]


    if not results:

        lines.extend(

            [

                (
                    "## No usable upcoming "
                    "greyhound races found"
                ),

                "",

                (
                    "The feed returned no races "
                    "with enough fresh runner prices "
                    "to build a prediction."
                ),

            ]

        )


        return "\n".join(
            lines
        )


    # ========================================================
    # MODE HEADER
    # ========================================================

    if mode == "full":

        lines.extend(

            [

                "## 🇦🇺 FULL AU/NZ MODEL",

                "",

                (
                    "Uses de-vigged multi-bookmaker "
                    "prices plus recent finishing form."
                ),

                "",

            ]

        )


        best_heading = (
            "## 🏆 BEST AU/NZ PICK"
        )


    else:

        lines.extend(

            [

                (
                    "## 🌍 INTERNATIONAL FALLBACK "
                    "— MARKET ONLY"
                ),

                "",

                (
                    "> No suitable AU/NZ race was "
                    "in the current next-to-go window. "
                    "International confidence is capped "
                    "because foreign races often have "
                    "much thinner bookmaker coverage."
                ),

                "",

            ]

        )


        best_heading = (

            "## 🏆 BEST INTERNATIONAL "
            "MARKET LEAN"

        )


    # ========================================================
    # BEST PICK
    # ========================================================

    best = results[
        0
    ]


    winner = best[
        "winner"
    ]


    lines.extend(

        [

            best_heading,

            "",

            (
                f"### "
                f"#{winner['number'] or '?'} "
                f"**{winner['name'].upper()}**"
            ),

            (
                f"**"
                f"{race_title(best['race'])}"
                f"**"
            ),

            "",

            (
                f"- **Estimated win chance:** "
                f"{winner['probability'] * 100:.1f}%"
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
                f"({winner['best_bookmaker']})"
            ),

            (
                f"- **Model fair price:** "
                f"${winner['fair_price']:.2f}"
            ),

            (
                f"- **Fresh bookmaker quotes:** "
                f"{winner['bookmaker_count']}"
            ),

        ]

    )


    # ========================================================
    # AU/NZ EXTRA INFORMATION
    # ========================================================

    if mode == "full":

        box_display = (

            winner[
                "box"
            ]

            if winner[
                "box"
            ]
            is not None

            else

            "N/A"

        )


        lines.extend(

            [

                (
                    f"- **Box:** "
                    f"{box_display}"
                ),

                (
                    f"- **Recent form:** "
                    f"`{form_text(winner['form'])}`"
                ),

                (
                    f"- **Price vs model:** "
                    f"{winner['price_gap_pct']:+.1f}%"
                ),

            ]

        )


    # ========================================================
    # INTERNATIONAL DATA QUALITY
    # ========================================================

    else:

        data_quality = (

            "Multi-book market"

            if winner[
                "bookmaker_count"
            ] >= 2

            else

            "Single-book market"

        )


        lines.extend(

            [

                (
                    f"- **Data quality:** "
                    f"{data_quality}"
                ),

                (
                    "- **Value signal:** Not shown as "
                    "a betting recommendation for the "
                    "international fallback; this model "
                    "is primarily derived from the same "
                    "market prices."
                ),

            ]

        )


    # ========================================================
    # RANKED RACES
    # ========================================================

    lines.extend(

        [

            "",

            "---",

            "",

            (
                "## 📊 RACES RANKED "
                "STRONGEST → WEAKEST"
            ),

            "",

        ]

    )


    for position, result in enumerate(
        results,
        1
    ):

        race = result[
            "race"
        ]


        race_winner = result[
            "winner"
        ]


        lines.extend(

            [

                (
                    f"### {position}. "
                    f"{race_title(race)}"
                ),

                (
                    f"🏆 **"
                    f"#{race_winner['number'] or '?'} "
                    f"{race_winner['name']}"
                    f"**"
                ),

                "",

                (
                    f"**Win estimate:** "
                    f"{race_winner['probability'] * 100:.1f}% "
                    f"· **Confidence:** "
                    f"{result['confidence']}/100 "
                    f"· **{result['verdict']}**"
                ),

                "",

                (
                    f"Sportsbet **"
                    f"{price_text(race_winner['sportsbet_price'])}"
                    f"** · "
                    f"Best **"
                    f"${race_winner['best_price']:.2f} "
                    f"({race_winner['best_bookmaker']})"
                    f"** · "
                    f"Fair **"
                    f"${race_winner['fair_price']:.2f}"
                    f"**"
                ),

                "",

                "**Top 3**",

            ]

        )


        for rank, runner in enumerate(

            result[
                "top3"
            ],

            1

        ):

            if mode == "full":

                box_display = (

                    runner[
                        "box"
                    ]

                    if runner[
                        "box"
                    ]
                    is not None

                    else

                    "N/A"

                )


                extra = (

                    f" · box "
                    f"{box_display} "
                    f"· form "
                    f"`{form_text(runner['form'])}`"

                )

            else:

                extra = ""


            lines.append(

                (
                    f"{rank}. "
                    f"**#{runner['number'] or '?'} "
                    f"{runner['name']}** "
                    f"— "
                    f"{runner['probability'] * 100:.1f}% "
                    f"· best "
                    f"${runner['best_price']:.2f}"
                    f"{extra}"
                )

            )


        lines.extend(

            [

                "",

                "---",

                "",

            ]

        )


    # ========================================================
    # MODEL NOTES
    # ========================================================

    lines.extend(

        [

            "### Model notes",

            "",

            (
                "- Bookmaker quotes marked stale, "
                f"or older than "
                f"{STALE_AFTER_SECONDS} seconds, "
                "are excluded."
            ),

            (
                "- Each sufficiently complete "
                "bookmaker market is de-vigged before "
                "bookmaker probabilities are combined."
            ),

            (
                "- AU/NZ mode weights the market "
                "at 85% and recent finishing form "
                "at 15%."
            ),

            (
                "- International fallback is "
                "market-only and its confidence "
                "is deliberately capped."
            ),

            (
                "- The win estimate is a model "
                "estimate, not a guaranteed or "
                "historically calibrated probability."
            ),

        ]

    )


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

            "GREYHOUND AI PREDICTOR V3.2\n"
            "No usable upcoming "
            "greyhound races found.\n"

        )


    best = results[
        0
    ]


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

        "GREYHOUND AI PREDICTOR V3.2",

        mode_text,

        "",

        "BEST PICK",

        race_title(
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
            f"{winner['probability'] * 100:.1f}%"
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
            f"({winner['best_bookmaker']})"
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
                f"{race_title(result['race'])} "
                f"| "
                f"#{runner['number'] or '?'} "
                f"{runner['name']} "
                f"| "
                f"{runner['probability'] * 100:.1f}% "
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
                                    "probability"
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
                                "best_bookmaker"
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
                                "bookmaker_count"
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

                        "price_gap_pct":
                            round(

                                winner[
                                    "price_gap_pct"
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
        "🐕 GREYHOUND AI PREDICTOR V3.2"
    )


    print(

        "Prediction-only: "
        "this program does not place bets."

    )


    try:

        races = fetch_races()


        full_results = []

        fallback_results = []


        # ====================================================
        # ANALYSE ALL UPCOMING RACES
        # ====================================================

        for race in races:

            if not isinstance(
                race,
                dict
            ):

                continue


            if is_full_data_race(
                race
            ):

                result = analyse_race(

                    race,

                    "full"

                )


                if result:

                    full_results.append(
                        result
                    )


            else:

                result = analyse_race(

                    race,

                    "market"

                )


                if result:

                    fallback_results.append(
                        result
                    )


        # ====================================================
        # ALWAYS PRIORITISE AU/NZ
        # ====================================================

        if full_results:

            mode = "full"


            full_results.sort(

                key=lambda result:

                    result[
                        "race_strength"
                    ],

                reverse=True

            )


            results = full_results[
                :MAX_DISPLAY
            ]


        # ====================================================
        # OTHERWISE USE INTERNATIONAL FALLBACK
        # ====================================================

        else:

            mode = "market"


            fallback_results.sort(

                key=lambda result:

                    result[
                        "race_strength"
                    ],

                reverse=True

            )


            results = fallback_results[
                :MAX_DISPLAY
            ]


        # ====================================================
        # SAVE GITHUB SUMMARY
        # ====================================================

        Path(
            "predictions.md"
        ).write_text(

            markdown_output(
                results,
                mode
            )

            +

            "\n",

            encoding="utf-8"

        )


        # ====================================================
        # SAVE TEXT VERSION
        # ====================================================

        Path(
            "predictions.txt"
        ).write_text(

            text_output(
                results,
                mode
            ),

            encoding="utf-8"

        )


        # ====================================================
        # SAVE JSON VERSION
        # ====================================================

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

            encoding="utf-8"

        )


        Path(
            "predictions.txt"
        ).write_text(

            message
            +
            "\n",

            encoding="utf-8"

        )


        save_json(

            "predictions.json",

            {

                "error":
                    str(
                        error
                    )

            }

        )


        return 2


if __name__ == "__main__":

    sys.exit(
        main()
    )
