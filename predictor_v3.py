import json, math, os, re, statistics, sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

API = "https://api.puntersedge.online/v1/racing/next-to-go"
MOVERS = "https://api.puntersedge.online/v1/racing/movers"
KEY = os.getenv("PUNTERSEDGE_API_KEY", "").strip()
TZ = os.getenv("BOT_TIMEZONE", "Australia/Brisbane").strip()

try:
    MAX_RACES = int(os.getenv("MAX_RACES", "8"))
except ValueError:
    MAX_RACES = 8

MAX_RACES = max(1, min(20, MAX_RACES))

Path("debug").mkdir(exist_ok=True)


def num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def save(path, data):
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def local_time(value):
    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(
            ZoneInfo(TZ)
        ).strftime("%a %d %b %I:%M %p")

    except Exception:
        return str(value or "Time N/A")


def form_list(form):

    values = (
        form
        if isinstance(form, list)
        else re.findall(r"[1-8]", str(form or ""))
    )

    out = []

    for v in values:
        try:
            n = int(v)

            if 1 <= n <= 8:
                out.append(n)

        except (TypeError, ValueError):
            pass

    return out[-5:]


def form_score(form):

    results = form_list(form)

    if not results:
        return 0.50

    value = {
        1: 1.00,
        2: .84,
        3: .70,
        4: .56,
        5: .43,
        6: .32,
        7: .23,
        8: .16,
    }

    weights = range(
        1,
        len(results) + 1
    )

    return (
        sum(
            value[p] * w
            for p, w in zip(
                results,
                weights
            )
        )
        /
        sum(weights)
    )


def form_text(form):

    x = form_list(form)

    return (
        "".join(
            map(str, x)
        )
        if x
        else "N/A"
    )


def sportsbet(key):

    return (
        "sportsbet"
        in str(
            key or ""
        ).lower().replace(
            " ",
            ""
        )
    )


def quotes(runner):

    out = []

    for b in runner.get(
        "bookmakers"
    ) or []:

        if not isinstance(
            b,
            dict
        ):
            continue

        price = num(
            b.get(
                "win_price"
            )
        )

        age = num(
            b.get(
                "age_seconds"
            )
        )

        if (
            not price
            or price <= 1
            or b.get("stale")
            or (
                age is not None
                and age > 120
            )
        ):
            continue

        key = str(
            b.get("key")
            or b.get("name")
            or b.get("title")
            or "unknown"
        )

        out.append({

            "key":
                key,

            "name":
                str(
                    b.get("title")
                    or b.get("name")
                    or key
                ),

            "price":
                price,

            "age":
                age,

        })

    return out


def api_get(
    url,
    params,
    required=True
):

    if not KEY:

        raise RuntimeError(
            "PUNTERSEDGE_API_KEY is missing."
        )

    r = requests.get(

        url,

        params=params,

        headers={

            "X-API-Key":
                KEY,

            "Accept":
                "application/json",

            "Accept-Encoding":
                "gzip",

            "User-Agent":
                "greyhound-ai-predictor/3.0",

        },

        timeout=30,

    )

    try:
        data = r.json()

    except ValueError:

        data = {
            "raw_text":
                r.text[:3000]
        }

    if r.status_code != 200:

        if required:

            raise RuntimeError(
                f"PuntersEdge HTTP "
                f"{r.status_code}: "
                f"{data}"
            )

        return None, r.status_code

    return data, r.status_code


def fetch_data():

    races, _ = api_get(

        API,

        {
            "categories":
                "greyhound",

            "num_races":
                50,
        }
    )

    save(
        "debug/puntersedge_races.json",
        races
    )

    movers, status = api_get(

        MOVERS,

        {
            "categories":
                "greyhound",

            "min_move_pct":
                3,

            "min_books":
                2,

            "max_mins_to_jump":
                360,

            "limit":
                200,
        },

        required=False,
    )

    if movers is None:

        Path(
            "debug/movers_status.txt"
        ).write_text(

            (
                f"Movers unavailable "
                f"(HTTP {status}); "
                f"V3 continued without it.\n"
            ),

            encoding="utf-8"
        )

        movers = []

    else:

        save(
            "debug/puntersedge_movers.json",
            movers
        )

    if not isinstance(
        races,
        list
    ):

        raise RuntimeError(
            "Unexpected next-to-go response."
        )

    return (
        races,
        movers
        if isinstance(
            movers,
            list
        )
        else []
    )


def mkey(
    venue,
    race_no,
    runner
):

    return (

        str(
            venue or ""
        ).strip().lower(),

        str(
            race_no or ""
        ).strip(),

        str(
            runner or ""
        ).strip().lower(),

    )


def local_race(race):

    country = str(
        race.get(
            "country"
        ) or ""
    ).upper()

    if country in {
        "AU",
        "NZ"
    }:
        return True

    if country:
        return False

    return any(

        r.get("form")
        not in (
            None,
            "",
            []
        )

        or

        r.get(
            "barrier"
        ) is not None

        for r
        in race.get(
            "runners"
        ) or []

    )


def analyse(
    race,
    mover_index
):

    runners = []

    for r in (
        race.get(
            "runners"
        ) or []
    ):

        qs = quotes(r)

        if not qs:
            continue

        runners.append({

            "number":
                r.get(
                    "number"
                ),

            "name":
                str(
                    r.get(
                        "name"
                    )
                    or
                    "Unknown Runner"
                ),

            "box":
                (
                    r.get(
                        "barrier"
                    )

                    if r.get(
                        "barrier"
                    ) is not None

                    else r.get(
                        "box"
                    )
                ),

            "form":
                r.get(
                    "form"
                ),

            "form_score":
                form_score(
                    r.get(
                        "form"
                    )
                ),

            "quotes":
                qs,

        })

    if len(runners) < 2:
        return None

    # Build a fair/de-vigged market for each bookmaker.
    markets = {}

    for i, runner in enumerate(
        runners
    ):

        for q in runner[
            "quotes"
        ]:

            markets.setdefault(
                q["key"],
                {}
            )[i] = q[
                "price"
            ]

    fair_samples = {

        i: []

        for i in range(
            len(runners)
        )

    }

    for _, prices in markets.items():

        # Ignore badly incomplete bookmaker markets.
        if (
            len(prices)
            /
            len(runners)
            <
            .70
        ):
            continue

        implied = {

            i:
                1 / p

            for i, p
            in prices.items()

        }

        total = sum(
            implied.values()
        )

        for i, p in implied.items():

            fair_samples[i].append(
                p / total
            )

    market_raw = []

    for i, runner in enumerate(
        runners
    ):

        if fair_samples[i]:

            market_raw.append(

                statistics.median(
                    fair_samples[i]
                )

            )

        else:

            market_raw.append(

                1
                /
                statistics.median(

                    q["price"]

                    for q
                    in runner[
                        "quotes"
                    ]

                )

            )

    total = sum(
        market_raw
    )

    market_p = [

        p / total

        for p
        in market_raw

    ]

    form_raw = [

        max(
            .05,
            r[
                "form_score"
            ]
        )

        for r
        in runners

    ]

    total = sum(
        form_raw
    )

    form_p = [

        p / total

        for p
        in form_raw

    ]

    raw_model = []

    for i, runner in enumerate(
        runners
    ):

        # Market remains the dominant predictor.
        base = (
            .85
            *
            market_p[i]
            +
            .15
            *
            form_p[i]
        )

        move = mover_index.get(

            mkey(

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

        move_pct = (

            num(
                move.get(
                    "move_pct"
                )
            )

            if move

            else None

        )

        # PuntersEdge:
        # negative move = firming
        # positive move = drifting

        move_signal = (

            0

            if move_pct is None

            else max(

                -1,

                min(

                    1,

                    -move_pct
                    /
                    20

                )

            )

        )

        raw_model.append(

            base
            *
            (
                1
                +
                .08
                *
                move_signal
            )

        )

        runner[
            "move_pct"
        ] = move_pct

        runner[
            "move_direction"
        ] = (

            move.get(
                "direction"
            )

            if move

            else None

        )

    total = sum(
        raw_model
    )

    probs = [

        p / total

        for p
        in raw_model

    ]

    for i, runner in enumerate(
        runners
    ):

        best = max(

            runner[
                "quotes"
            ],

            key=lambda q:
                q[
                    "price"
                ]

        )

        sb = next(

            (
                q

                for q
                in runner[
                    "quotes"
                ]

                if sportsbet(
                    q[
                        "key"
                    ]
                )
            ),

            None

        )

        ages = [

            q[
                "age"
            ]

            for q
            in runner[
                "quotes"
            ]

            if q[
                "age"
            ] is not None

        ]

        runner[
            "prob"
        ] = probs[i]

        runner[
            "fair"
        ] = (
            1
            /
            probs[i]
        )

        runner[
            "best"
        ] = best[
            "price"
        ]

        runner[
            "best_book"
        ] = best[
            "name"
        ]

        runner[
            "sportsbet"
        ] = (

            sb[
                "price"
            ]

            if sb

            else None

        )

        runner[
            "books"
        ] = len(
            runner[
                "quotes"
            ]
        )

        runner[
            "age"
        ] = (

            statistics.mean(
                ages
            )

            if ages

            else None

        )

        runner[
            "edge"
        ] = (

            probs[i]
            *
            best[
                "price"
            ]
            -
            1

        ) * 100

        runner[
            "sd"
        ] = (

            statistics.pstdev(
                fair_samples[i]
            )

            if len(
                fair_samples[i]
            ) > 1

            else .10

        )

    runners.sort(

        key=lambda r:
            r[
                "prob"
            ],

        reverse=True

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

    coverage = min(

        1,

        winner[
            "books"
        ]
        /
        8

    )

    freshness = (

        1

        if winner[
            "age"
        ] is None

        else max(

            0,

            1
            -
            winner[
                "age"
            ]
            /
            120

        )

    )

    agreement = max(

        0,

        1
        -
        winner[
            "sd"
        ]
        /
        .08

    )

    confidence = (

        30

        +
        winner[
            "prob"
        ]
        *
        55

        +
        gap
        *
        80

        +
        coverage
        *
        8

        +
        freshness
        *
        5

        +
        agreement
        *
        4

    )

    confidence = int(

        round(

            max(

                45,

                min(
                    92,
                    confidence
                )

            )

        )

    )

    verdict = (

        "STRONG PICK"

        if confidence >= 80

        else "GOOD PICK"

        if confidence >= 70

        else "LEAN"

        if confidence >= 60

        else "LOW CONFIDENCE"

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
            (
                confidence

                +
                min(
                    6,
                    winner[
                        "books"
                    ]
                    *
                    .5
                )

                +
                min(
                    5,
                    gap
                    *
                    35
                )
            )

    }


def title(result):

    race = result[
        "race"
    ]

    distance = (

        f" · "
        f"{race.get('distance_m')}m"

        if race.get(
            "distance_m"
        )

        else ""

    )

    return (

        f"{race.get('venue') or 'Unknown venue'} "
        f"R{race.get('race_number') or '?'}"
        f"{distance} · "
        f"{local_time(race.get('start_time'))}"

    )


def edge_label(edge):

    if edge >= 8:
        return "POSITIVE MODEL EDGE"

    if edge >= 3:
        return "SMALL MODEL EDGE"

    return "NO MODEL EDGE"


def markdown(
    results,
    movers_used
):

    now = datetime.now(

        ZoneInfo(
            TZ
        )

    ).strftime(
        "%d %b %Y, %I:%M %p %Z"
    )

    lines = [

        "# 🐕 Greyhound AI Predictor V3",

        "",

        f"Generated **{now}**",

        "",

        (
            "Fresh bookmaker prices are "
            "de-vigged into market probabilities, "
            "then adjusted modestly for recent form"
            +
            (
                " and confirmed market movement."

                if movers_used

                else "."
            )
        ),

        "",

        (
            "> Estimated win chance is a model "
            "estimate, not a guaranteed or "
            "historically calibrated probability."
        ),

        "",

    ]

    if not results:

        return "\n".join(

            lines
            +
            [
                (
                    "## No suitable AU/NZ "
                    "greyhound races found."
                )
            ]

        )

    best = results[0]

    w = best[
        "winner"
    ]

    sb = (

        f"${w['sportsbet']:.2f}"

        if w[
            "sportsbet"
        ]

        else
        "No fresh quote"

    )

    lines += [

        "## 🏆 BEST PICK FROM THIS SCAN",

        "",

        (
            f"### #{w['number'] or '?'} "
            f"**{w['name'].upper()}**"
        ),

        f"**{title(best)}**",

        "",

        (
            f"- **Estimated win chance:** "
            f"{w['prob'] * 100:.1f}%"
        ),

        (
            f"- **Model confidence:** "
            f"{best['confidence']}/100 — "
            f"**{best['verdict']}**"
        ),

        (
            f"- **Sportsbet:** "
            f"{sb}"
        ),

        (
            f"- **Best available:** "
            f"${w['best']:.2f} "
            f"({w['best_book']})"
        ),

        (
            f"- **Model fair price:** "
            f"${w['fair']:.2f}"
        ),

        (
            f"- **Model edge at best price:** "
            f"{w['edge']:+.1f}% — "
            f"**{edge_label(w['edge'])}**"
        ),

        (
            f"- **Box:** "
            f"{w['box'] if w['box'] is not None else 'N/A'} "
            f"· **Form:** "
            f"`{form_text(w['form'])}`"
        ),

    ]

    if w[
        "move_pct"
    ] is not None:

        lines.append(

            (
                f"- **Market move:** "
                f"{w['move_pct']:+.1f}% "
                f"({w['move_direction'] or 'movement'})"
            )

        )

    lines += [

        "",

        "---",

        "",

        "## 📊 All races ranked strongest → weakest",

        "",

    ]

    for n, result in enumerate(
        results,
        1
    ):

        w = result[
            "winner"
        ]

        sb = (

            f"${w['sportsbet']:.2f}"

            if w[
                "sportsbet"
            ]

            else "N/A"

        )

        lines += [

            f"### {n}. {title(result)}",

            (
                f"🏆 **#{w['number'] or '?'} "
                f"{w['name']}** — "
                f"{w['prob'] * 100:.1f}% · "
                f"**{result['confidence']}/100 "
                f"{result['verdict']}**"
            ),

            "",

            (
                f"Sportsbet **{sb}** · "
                f"Best **${w['best']:.2f} "
                f"{w['best_book']}** · "
                f"Fair **${w['fair']:.2f}** · "
                f"Edge **{w['edge']:+.1f}%**"
            ),

            "",

            "**Top 3:**",

        ]

        for pos, r in enumerate(
            result[
                "top3"
            ],
            1
        ):

            lines.append(

                (
                    f"{pos}. "
                    f"**#{r['number'] or '?'} "
                    f"{r['name']}** — "
                    f"{r['prob'] * 100:.1f}% · "
                    f"best ${r['best']:.2f} · "
                    f"form `{form_text(r['form'])}` · "
                    f"box "
                    f"{r['box'] if r['box'] is not None else 'N/A'}"
                )

            )

        lines += [

            "",

            "---",

            "",

        ]

    lines += [

        "### Model notes",

        (
            "- Stale bookmaker quotes "
            "are excluded."
        ),

        (
            "- Box is shown but not given a "
            "generic advantage; a proper box "
            "edge needs track-and-distance history."
        ),

        (
            "- A positive model edge is not a "
            "guarantee of profit. Backtesting "
            "is the next step."
        ),

    ]

    return "\n".join(
        lines
    )


def text_output(results):

    if not results:

        return (
            "GREYHOUND AI PREDICTOR V3\n"
            "No suitable AU/NZ greyhound races found.\n"
        )

    best = results[0]

    w = best[
        "winner"
    ]

    lines = [

        "GREYHOUND AI PREDICTOR V3",

        "",

        "BEST PICK FROM THIS SCAN",

        title(
            best
        ),

        (
            f"WINNER: "
            f"#{w['number'] or '?'} "
            f"{w['name']}"
        ),

        (
            f"Estimated win chance: "
            f"{w['prob'] * 100:.1f}%"
        ),

        (
            f"Confidence: "
            f"{best['confidence']}/100 "
            f"- {best['verdict']}"
        ),

        (
            f"Sportsbet: "
            f"${w['sportsbet']:.2f}"

            if w[
                "sportsbet"
            ]

            else "Sportsbet: N/A"
        ),

        (
            f"Best available: "
            f"${w['best']:.2f} "
            f"({w['best_book']})"
        ),

        (
            f"Fair price: "
            f"${w['fair']:.2f}"
        ),

        (
            f"Model edge: "
            f"{w['edge']:+.1f}%"
        ),

        "",

        "RANKED PICKS",

    ]

    for n, result in enumerate(
        results,
        1
    ):

        x = result[
            "winner"
        ]

        lines.append(

            (
                f"{n}. "
                f"{title(result)} | "
                f"#{x['number'] or '?'} "
                f"{x['name']} | "
                f"{x['prob'] * 100:.1f}% | "
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


def main():

    print(
        "🐕 GREYHOUND AI PREDICTOR V3"
    )

    print(
        "Prediction-only: "
        "this program does not place bets."
    )

    try:

        races, movers = fetch_data()

        mover_index = {

            mkey(
                m.get("venue"),
                m.get("race_number"),
                m.get("runner")
            ):
                m

            for m
            in movers

            if isinstance(
                m,
                dict
            )

        }

        results = []

        for race in races:

            if local_race(
                race
            ):

                r = analyse(
                    race,
                    mover_index
                )

                if r:
                    results.append(
                        r
                    )

        results.sort(

            key=lambda r:
                r[
                    "strength"
                ],

            reverse=True

        )

        results = results[
            :MAX_RACES
        ]

        Path(
            "predictions.md"
        ).write_text(

            markdown(
                results,
                bool(movers)
            )
            +
            "\n",

            encoding="utf-8"

        )

        Path(
            "predictions.txt"
        ).write_text(

            text_output(
                results
            ),

            encoding="utf-8"

        )

        out = []

        for r in results:

            race = r[
                "race"
            ]

            w = r[
                "winner"
            ]

            out.append({

                "race_id":
                    race.get(
                        "race_id"
                    ),

                "venue":
                    race.get(
                        "venue"
                    ),

                "race_number":
                    race.get(
                        "race_number"
                    ),

                "start_time":
                    race.get(
                        "start_time"
                    ),

                "distance_m":
                    race.get(
                        "distance_m"
                    ),

                "winner": {

                    "number":
                        w[
                            "number"
                        ],

                    "name":
                        w[
                            "name"
                        ],

                    "estimated_win_probability":
                        round(
                            w[
                                "prob"
                            ],
                            4
                        ),

                    "fair_price":
                        round(
                            w[
                                "fair"
                            ],
                            2
                        ),

                    "sportsbet_price":
                        w[
                            "sportsbet"
                        ],

                    "best_price":
                        round(
                            w[
                                "best"
                            ],
                            2
                        ),

                    "best_bookmaker":
                        w[
                            "best_book"
                        ],

                    "model_edge_pct":
                        round(
                            w[
                                "edge"
                            ],
                            2
                        ),

                    "box":
                        w[
                            "box"
                        ],

                    "form":
                        form_text(
                            w[
                                "form"
                            ]
                        ),

                },

                "confidence_index":
                    r[
                        "confidence"
                    ],

                "verdict":
                    r[
                        "verdict"
                    ],

            })

        save(
            "predictions.json",
            out
        )

        print(
            text_output(
                results
            )
        )

        return 0

    except Exception as e:

        msg = (
            f"ERROR: "
            f"{e}"
        )

        print(
            msg
        )

        Path(
            "predictions.txt"
        ).write_text(

            msg + "\n",

            encoding="utf-8"

        )

        Path(
            "predictions.md"
        ).write_text(

            (
                "# Predictor error\n\n"
                f"`{msg}`\n"
            ),

            encoding="utf-8"

        )

        save(
            "predictions.json",
            {
                "error":
                    str(e)
            }
        )

        return 2


if __name__ == "__main__":

    sys.exit(
        main()
    )
