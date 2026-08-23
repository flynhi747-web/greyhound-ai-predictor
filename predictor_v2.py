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
# GREYHOUND WINNER PREDICTOR V2
# PuntersEdge multi-bookmaker feed
# ============================================================

API_URL = "https://api.puntersedge.online/v1/racing/next-to-go"

API_KEY = os.getenv("PUNTERSEDGE_API_KEY", "").strip()

BOT_TIMEZONE = os.getenv(
    "BOT_TIMEZONE",
    "Australia/Brisbane"
).strip()

try:
    MAX_RACES = int(os.getenv("MAX_RACES", "8"))
except ValueError:
    MAX_RACES = 8

MAX_RACES = max(1, min(20, MAX_RACES))

Path("debug").mkdir(exist_ok=True)


# ============================================================
# HELPERS
# ============================================================

def safe_number(value):

    try:

        number = float(value)

        if math.isfinite(number):
            return number

        return None

    except (TypeError, ValueError):

        return None


def local_time(value):

    if not value:
        return "Time N/A"

    try:

        value = str(value).replace(
            "Z",
            "+00:00"
        )

        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        timezone_local = ZoneInfo(
            BOT_TIMEZONE
        )

        return dt.astimezone(
            timezone_local
        ).strftime(
            "%a %d %b %I:%M %p"
        )

    except Exception:

        return str(value)


# ============================================================
# FORM ANALYSIS
# ============================================================

def form_score(form):

    """
    Converts recent finishing positions
    into a score between 0 and 1.

    Recent races receive more weight.
    """

    if form is None:

        return 0.50

    if isinstance(form, list):

        results = []

        for value in form:

            try:

                results.append(
                    int(value)
                )

            except (TypeError, ValueError):

                pass

    else:

        results = [
            int(x)
            for x in re.findall(
                r"\d",
                str(form)
            )
        ]

    results = results[-5:]

    if not results:

        return 0.50


    position_value = {

        0: 0.08,

        1: 1.00,

        2: 0.84,

        3: 0.70,

        4: 0.56,

        5: 0.44,

        6: 0.34,

        7: 0.25,

        8: 0.18,

        9: 0.12,

    }


    weights = list(
        range(
            1,
            len(results) + 1
        )
    )


    weighted_score = sum(

        position_value.get(
            position,
            0.08
        ) * weight

        for position, weight
        in zip(
            results,
            weights
        )

    )


    return weighted_score / sum(weights)


def display_form(form):

    if form in (
        None,
        "",
        []
    ):

        return "N/A"

    if isinstance(
        form,
        list
    ):

        return "-".join(
            str(x)
            for x in form
        )

    return str(form)


# ============================================================
# BOOKMAKER ODDS
# ============================================================

def get_prices(runner):

    fresh_prices = []

    fallback_prices = []


    bookmakers = runner.get(
        "bookmakers"
    ) or []


    for bookmaker in bookmakers:

        if not isinstance(
            bookmaker,
            dict
        ):

            continue


        price = safe_number(
            bookmaker.get(
                "win_price"
            )
        )


        if not price:

            continue


        if price <= 1:

            continue


        bookmaker_name = str(

            bookmaker.get(
                "title"
            )

            or bookmaker.get(
                "name"
            )

            or bookmaker.get(
                "key"
            )

            or "Unknown"

        )


        item = {

            "key":
                str(
                    bookmaker.get(
                        "key"
                    ) or ""
                ),

            "name":
                bookmaker_name,

            "price":
                price,

            "stale":
                bool(
                    bookmaker.get(
                        "stale",
                        False
                    )
                ),

        }


        fallback_prices.append(
            item
        )


        if not item["stale"]:

            fresh_prices.append(
                item
            )


    if fresh_prices:

        return fresh_prices


    return fallback_prices


# ============================================================
# GET LIVE GREYHOUND RACES
# ============================================================

def fetch_races():

    if not API_KEY:

        raise RuntimeError(

            "PUNTERSEDGE_API_KEY is missing. "
            "Check the GitHub repository secret "
            "and predict.yml."

        )


    response = requests.get(

        API_URL,

        params={

            "categories":
                "greyhound",

            "num_races":
                MAX_RACES,

        },

        headers={

            "X-API-Key":
                API_KEY,

            "Accept":
                "application/json",

            "User-Agent":
                "greyhound-ai-predictor/2.0",

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


    Path(
        "debug/puntersedge_response.json"
    ).write_text(

        json.dumps(
            data,
            indent=2,
            ensure_ascii=False
        ),

        encoding="utf-8"

    )


    if response.status_code != 200:

        if isinstance(
            data,
            dict
        ):

            detail = (

                data.get(
                    "detail"
                )

                or data.get(
                    "title"
                )

                or data

            )

        else:

            detail = data


        raise RuntimeError(

            f"PuntersEdge HTTP "
            f"{response.status_code}: "
            f"{detail}"

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
# SCORE EACH RACE
# ============================================================

def score_race(race):

    runners = []


    for runner in (
        race.get(
            "runners"
        ) or []
    ):


        prices = get_prices(
            runner
        )


        if not prices:

            continue


        all_prices = [

            item["price"]

            for item
            in prices

        ]


        consensus_price = (
            statistics.median(
                all_prices
            )
        )


        best_quote = max(

            prices,

            key=lambda x:
                x["price"]

        )


        sportsbet_price = None


        for bookmaker in prices:

            search_name = (

                bookmaker["key"]
                + " "
                + bookmaker["name"]

            ).lower().replace(
                " ",
                ""
            )


            if "sportsbet" in search_name:

                sportsbet_price = (
                    bookmaker["price"]
                )

                break


        recent_form = form_score(
            runner.get(
                "form"
            )
        )


        runners.append({

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
                    ) is not None

                    else runner.get(
                        "box"
                    )
                ),

            "form":
                runner.get(
                    "form"
                ),

            "form_score":
                recent_form,

            "books":
                len(prices),

            "consensus":
                consensus_price,

            "sportsbet":
                sportsbet_price,

            "best_price":
                best_quote[
                    "price"
                ],

            "best_book":
                best_quote[
                    "name"
                ],

            "market_raw":
                1 / consensus_price,

            "sportsbet_raw":
                (
                    1 / sportsbet_price

                    if sportsbet_price

                    else
                    1 / consensus_price
                ),

        })


    if len(runners) < 2:

        return None


    max_market = max(

        runner["market_raw"]

        for runner
        in runners

    )


    max_sportsbet = max(

        runner["sportsbet_raw"]

        for runner
        in runners

    )


    max_books = max(

        runner["books"]

        for runner
        in runners

    )


    # ========================================================
    # MODEL
    #
    # 55% Multi-bookmaker market consensus
    # 20% Sportsbet market signal
    # 20% Recent finishing form
    #  5% Bookmaker/data coverage
    # ========================================================

    for runner in runners:


        market_score = (

            100
            *
            runner["market_raw"]
            /
            max_market

        )


        sportsbet_score = (

            100
            *
            runner["sportsbet_raw"]
            /
            max_sportsbet

        )


        recent_form_score = (

            100
            *
            runner["form_score"]

        )


        coverage_score = (

            100
            *
            runner["books"]
            /
            max_books

        )


        runner["score"] = (

            0.55
            *
            market_score

            +

            0.20
            *
            sportsbet_score

            +

            0.20
            *
            recent_form_score

            +

            0.05
            *
            coverage_score

        )


    runners.sort(

        key=lambda x:
            x["score"],

        reverse=True

    )


    winner = runners[0]

    second = runners[1]


    score_gap = (

        winner["score"]
        -
        second["score"]

    )


    # ========================================================
    # CONFIDENCE INDEX
    # ========================================================

    confidence = 50


    confidence += min(

        24,

        score_gap * 1.5

    )


    confidence += (

        12
        *
        (
            winner["books"]
            /
            max_books
        )

    )


    if winner[
        "sportsbet"
    ] is not None:

        confidence += 4


    if race.get(
        "stale"
    ):

        confidence -= 8


    confidence = int(

        round(

            max(

                50,

                min(
                    90,
                    confidence
                )

            )

        )

    )


    return {

        "winner":
            winner,

        "top3":
            runners[:3],

        "confidence":
            confidence,

        "gap":
            round(
                score_gap,
                2
            ),

    }


# ============================================================
# EXPLAIN PREDICTION
# ============================================================

def prediction_reasons(model):

    winner = model[
        "winner"
    ]


    reasons = []


    if winner[
        "sportsbet"
    ] is not None:

        reasons.append(

            f"Sportsbet "
            f"${winner['sportsbet']:.2f}"

        )


    if winner[
        "form_score"
    ] >= 0.70:

        reasons.append(
            "strong recent form"
        )


    elif winner[
        "form_score"
    ] >= 0.55:

        reasons.append(
            "solid recent form"
        )


    if model[
        "gap"
    ] >= 8:

        reasons.append(
            "clear model-score edge"
        )


    if winner[
        "books"
    ] >= 5:

        reasons.append(

            f"{winner['books']} "
            f"bookmaker quotes used"

        )


    if not reasons:

        reasons.append(

            "best combined "
            "market and form score"

        )


    return reasons[:3]


# ============================================================
# CREATE OUTPUT
# ============================================================

def create_output(races):

    scored_races = []

    json_output = []


    # Prefer AU / NZ greyhound racing.
    preferred = [

        race

        for race
        in races

        if not race.get(
            "country"
        )

        or str(
            race.get(
                "country"
            )
        ).upper()
        in {
            "AU",
            "NZ"
        }

    ]


    chosen_races = (

        preferred

        if preferred

        else races

    )


    for race in (
        chosen_races[
            :MAX_RACES
        ]
    ):


        model = score_race(
            race
        )


        if not model:

            continue


        scored_races.append(

            (
                race,
                model
            )

        )


        winner = model[
            "winner"
        ]


        json_output.append({

            "race_id":
                (
                    race.get(
                        "race_id"
                    )

                    or race.get(
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

            "prediction": {

                "number":
                    winner[
                        "number"
                    ],

                "name":
                    winner[
                        "name"
                    ],

                "model_score":
                    round(
                        winner[
                            "score"
                        ],
                        2
                    ),

                "confidence_index":
                    model[
                        "confidence"
                    ],

                "consensus_price":
                    round(
                        winner[
                            "consensus"
                        ],
                        2
                    ),

                "sportsbet_price":
                    winner[
                        "sportsbet"
                    ],

                "best_price":
                    winner[
                        "best_price"
                    ],

                "best_bookmaker":
                    winner[
                        "best_book"
                    ],

                "box":
                    winner[
                        "box"
                    ],

                "form":
                    winner[
                        "form"
                    ],

            }

        })


    now = datetime.now(
        ZoneInfo(
            BOT_TIMEZONE
        )
    )


    lines = [

        "GREYHOUND AI PREDICTOR V2",

        "",

        f"Generated: "
        f"{now.strftime('%Y-%m-%d %H:%M %Z')}",

        "Data: PuntersEdge "
        "multi-bookmaker feed",

        "Prediction only - "
        "no bets are placed.",

        ""

    ]


    if not scored_races:

        lines.append(

            "No suitable upcoming "
            "greyhound races found."

        )


    for race, model in scored_races:


        winner = model[
            "winner"
        ]


        venue = (

            race.get(
                "venue"
            )

            or
            "Unknown venue"

        )


        race_number = (

            race.get(
                "race_number"
            )

            or
            "?"

        )


        country = (

            race.get(
                "country"
            )

            or
            "?"

        )


        lines.extend([

            "=" * 60,

            (
                f"{venue} "
                f"R{race_number} "
                f"| "
                f"{local_time(race.get('start_time'))} "
                f"| "
                f"{country}"
            ),

            "",

            (
                f"🏆 PREDICTED WINNER: "
                f"#{winner['number'] or '?'} "
                f"{winner['name'].upper()}"
            ),

            "",

            (
                f"MODEL CONFIDENCE: "
                f"{model['confidence']}/100"
            ),

            (
                f"MODEL SCORE: "
                f"{winner['score']:.1f}/100"
            ),

            "",

            (
                f"Consensus odds: "
                f"${winner['consensus']:.2f}"
            ),

        ])


        if winner[
            "sportsbet"
        ]:

            lines.append(

                f"Sportsbet: "
                f"${winner['sportsbet']:.2f}"

            )

        else:

            lines.append(

                "Sportsbet: "
                "no fresh quote"

            )


        lines.extend([

            (
                f"Best available: "
                f"${winner['best_price']:.2f} "
                f"({winner['best_book']})"
            ),

            (
                f"Box: "
                f"{winner['box'] if winner['box'] is not None else 'N/A'}"
            ),

            (
                f"Recent form: "
                f"{display_form(winner['form'])}"
            ),

            "",

            (
                "WHY: "
                +
                "; ".join(
                    prediction_reasons(
                        model
                    )
                )
            ),

            "",

            "TOP 3",

        ])


        for position, runner in enumerate(

            model[
                "top3"
            ],

            start=1

        ):


            sportsbet = (

                f"${runner['sportsbet']:.2f}"

                if runner[
                    "sportsbet"
                ]

                else
                "N/A"

            )


            lines.append(

                f"{position}. "
                f"#{runner['number'] or '?'} "
                f"{runner['name']} "
                f"| Score "
                f"{runner['score']:.1f} "
                f"| Consensus "
                f"${runner['consensus']:.2f} "
                f"| Sportsbet "
                f"{sportsbet} "
                f"| Form "
                f"{display_form(runner['form'])}"

            )


        if race.get(
            "stale"
        ):

            lines.append(

                "⚠️ Feed flagged stale - "
                "confidence reduced."

            )


        lines.append("")


    lines.extend([

        "=" * 60,

        (
            "Confidence is a model index, "
            "not a guaranteed probability "
            "of winning."
        ),

    ])


    text_output = "\n".join(
        lines
    )


    Path(
        "predictions.txt"
    ).write_text(

        text_output + "\n",

        encoding="utf-8"

    )


    Path(
        "predictions.json"
    ).write_text(

        json.dumps(

            json_output,

            indent=2,

            ensure_ascii=False,

            default=str

        ),

        encoding="utf-8"

    )


    return text_output


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "🐕 GREYHOUND AI PREDICTOR V2"
    )

    print(
        f"Timezone: "
        f"{BOT_TIMEZONE}"
    )

    print(
        f"Maximum races: "
        f"{MAX_RACES}"
    )

    print(
        "Prediction-only: "
        "this bot does not place bets."
    )


    try:

        races = fetch_races()

        output = create_output(
            races
        )

        print()

        print(
            output
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
            "predictions.txt"
        ).write_text(

            (
                "GREYHOUND AI "
                "PREDICTOR V2\n\n"
                +
                message
                +
                "\n"
            ),

            encoding="utf-8"

        )


        Path(
            "predictions.json"
        ).write_text(

            json.dumps(

                {
                    "error":
                        str(error)
                },

                indent=2

            ),

            encoding="utf-8"

        )


        return 2


if __name__ == "__main__":

    sys.exit(
        main()
    )
