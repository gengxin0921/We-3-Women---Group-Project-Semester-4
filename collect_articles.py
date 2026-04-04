"""
collect_articles_optimized.py — Women's Protest News Collector
==============================================================

A safer, lighter version for local testing:
- publishers are processed sequentially
- each publisher is saved immediately
- progress is resumable
- supports running one event at a time
- default test mode uses only one publisher

Recommended first test:
python collect_articles_optimized.py --event Roe_Leak_Protests_2022 --per-publisher 5 --reset-all
"""

from __future__ import annotations

import argparse
import gc
import json
import os
from datetime import date, datetime, timedelta

import pandas as pd
from fundus import CCNewsCrawler, PublisherCollection


# ══════════════════════════════════════════════════════════════════════════════
# ❶ CONFIG
# ══════════════════════════════════════════════════════════════════════════════

OUTPUT_DIR = "news_output"
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "progress.json")

MAX_PER_PUBLISHER = 50
CONTROL_PER_PUBLISHER = 50
DEFAULT_PROCESSES = 1

OUTPUT_COLUMNS = [
    "url",
    "publisher",
    "title",
    "body",
    "authors",
    "topics",
    "publishing_date",
    "event_label",
    "event_type",
    "is_control",
]


# ══════════════════════════════════════════════════════════════════════════════
# ❷ PUBLISHERS
# ══════════════════════════════════════════════════════════════════════════════

PUBLISHERS = [
    # US
    PublisherCollection.us.APNews,
    PublisherCollection.us.Reuters,
    PublisherCollection.us.VoiceOfAmerica,
    PublisherCollection.us.WashingtonPost,
    PublisherCollection.us.TheNewYorker,
    PublisherCollection.us.TheNation,
    PublisherCollection.us.TheIntercept,
    PublisherCollection.us.RollingStone,
    PublisherCollection.us.LATimes,
    PublisherCollection.us.BusinessInsider,
    PublisherCollection.us.CNBC,
    PublisherCollection.us.FoxNews,
    PublisherCollection.us.WashingtonTimes,
    PublisherCollection.us.FreeBeacon,
    PublisherCollection.us.TheGatewayPundit,
    # UK
    PublisherCollection.uk.BBC,
    PublisherCollection.uk.TheGuardian,
    PublisherCollection.uk.TheIndependent,
    PublisherCollection.uk.EuronewsEN,
    PublisherCollection.uk.iNews,
    PublisherCollection.uk.DailyMail,
    PublisherCollection.uk.TheTelegraph,
    PublisherCollection.uk.TheSun,
    # DE
    PublisherCollection.de.DW,
    PublisherCollection.de.SpiegelOnline,
    PublisherCollection.de.DieZeit,
    PublisherCollection.de.Tagesschau,
    PublisherCollection.de.FAZ,
    PublisherCollection.de.Taz,
    # AT
    PublisherCollection.at.DerStandard,
    PublisherCollection.at.DiePresse,
    PublisherCollection.at.ORF,
    # CA
    PublisherCollection.ca.CBCNews,
    PublisherCollection.ca.NationalPost,
    PublisherCollection.ca.TheGlobeAndMail,
    # FR
    PublisherCollection.fr.LeMonde,
    PublisherCollection.fr.LeFigaro,
    PublisherCollection.fr.EuronewsFR,
    # ES
    PublisherCollection.es.ElPais,
    PublisherCollection.es.ElMundo,
    PublisherCollection.es.ElDiario,
    PublisherCollection.es.LaVanguardia,
    PublisherCollection.es.ABC,
    PublisherCollection.es.Publico,
    # IL
    PublisherCollection.il.IsraelNachrichten,
]

# Change this to PUBLISHERS later when you want the full run
ACTIVE_PUBLISHERS = PUBLISHERS


# ══════════════════════════════════════════════════════════════════════════════
# ❸ EVENTS
# ══════════════════════════════════════════════════════════════════════════════

EVENTS = [
    {
        "label": "IWD_2018_Global",
        "event_type": "single",
        "window_start": date(2018, 3, 6),
        "window_end": date(2018, 3, 13),
        "note": "IWD 2018; Spanish Feminist Strike and Aurat March merged",
    },
    {
        "label": "Polish_Womens_Strike_2020",
        "event_type": "sustained",
        "window_start": date(2020, 10, 22),
        "window_end": date(2020, 10, 29),
        "note": "Constitutional Tribunal ruling Oct 22",
    },
    {
        "label": "Roe_Leak_Protests_2022",
        "event_type": "sustained",
        "window_start": date(2022, 5, 2),
        "window_end": date(2022, 5, 9),
        "note": "Politico leak May 2; protests May 3-4",
    },
    {
        "label": "Women_Life_Freedom_2022",
        "event_type": "sustained",
        "window_start": date(2022, 9, 16),
        "window_end": date(2022, 9, 23),
        "note": "Mahsa Amini death Sep 16",
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# ❹ CONTROL WEEKS
# ══════════════════════════════════════════════════════════════════════════════

_CONTROL_CANDIDATES = [2015, 2016, 2024]


def build_control_week(event: dict) -> dict | None:
    ws, we = event["window_start"], event["window_end"]
    iso_week = ws.isocalendar()[1]
    delta_days = (we - ws).days

    for cy in _CONTROL_CANDIDATES:
        jan4 = date(cy, 1, 4)
        w1_monday = jan4 - timedelta(days=jan4.weekday())
        cw_start = w1_monday + timedelta(weeks=iso_week - 1)
        cw_end = cw_start + timedelta(days=delta_days)

        if cw_start.year != cy:
            continue

        return {
            "label": f"CONTROL_{event['label']}_{cy}",
            "event_type": event["event_type"],
            "window_start": cw_start,
            "window_end": cw_end,
            "is_control": True,
            "matched_to": event["label"],
        }
    return None


# ══════════════════════════════════════════════════════════════════════════════
# ❺ DATE WINDOW
# ══════════════════════════════════════════════════════════════════════════════

def build_window(event: dict) -> tuple[datetime, datetime]:
    """
    Lighter window than the original version:
    - single / recurring: expand by 1 day before and 1 day after
    - sustained: keep original start, add 1 day after
    """
    ws, we = event["window_start"], event["window_end"]
    et = event.get("event_type", "single")

    if et in ("single", "recurring"):
        from_d = ws - timedelta(days=1)
    else:
        from_d = ws

    to_d = we + timedelta(days=1)

    return (
        datetime(from_d.year, from_d.month, from_d.day, 0, 0, 0),
        datetime(to_d.year, to_d.month, to_d.day, 23, 59, 59),
    )


# ══════════════════════════════════════════════════════════════════════════════
# ❻ PROGRESS
# ══════════════════════════════════════════════════════════════════════════════

def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(prog: dict) -> None:
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(prog, f, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
# ❼ MERGE ONE EVENT
# ══════════════════════════════════════════════════════════════════════════════

def merge_event_data(label: str, inc_dir: str) -> None:
    files = [
        os.path.join(inc_dir, f)
        for f in os.listdir(inc_dir)
        if f.endswith(".parquet")
    ]
    if not files:
        print(f"  ⚠️  No parquet files to merge for {label}")
        return

    combined = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    combined.drop_duplicates(subset=["url"], inplace=True)

    out_dir = os.path.join(OUTPUT_DIR, label)
    os.makedirs(out_dir, exist_ok=True)

    out_path = os.path.join(out_dir, f"{label}.parquet")
    combined.to_parquet(out_path, index=False)
    combined.to_csv(out_path.replace(".parquet", ".csv"), index=False)

    print(f"  ✅ {label} merged: {len(combined):,} unique articles")
    print(f"  Saved → {out_path}")


# ══════════════════════════════════════════════════════════════════════════════
# ❽ PROCESS ONE EVENT
# ══════════════════════════════════════════════════════════════════════════════

def process_event(event: dict, progress: dict, args: argparse.Namespace) -> None:
    label = event["label"]
    event_type = event.get("event_type", "")
    is_control = event.get("is_control", False)
    start_dt, end_dt = build_window(event)
    cap = args.control_per_publisher if is_control else args.per_publisher

    print("\n" + "═" * 72)
    print(f"Event    : {label}")
    print(f"Type     : {'control' if is_control else 'event'}")
    print(f"Window   : {event['window_start']} → {event['window_end']}")
    print(f"CC-News  : {start_dt.date()} → {end_dt.date()}")
    print(f"Cap      : {cap} articles per publisher")
    print(f"Publishers in run: {len(ACTIVE_PUBLISHERS)}")
    if event.get("note"):
        print(f"Note     : {event['note']}")

    ep = progress.get(label, {"done_publishers": [], "n_articles": 0})
    done_pubs = set(ep.get("done_publishers", []))

    inc_dir = os.path.join(OUTPUT_DIR, label, "incremental")
    os.makedirs(inc_dir, exist_ok=True)

    pending = [pub for pub in ACTIVE_PUBLISHERS if str(pub) not in done_pubs]
    print(f"Pending  : {len(pending)} publishers")

    if not pending:
        print("  ✅ Nothing to do, all active publishers already completed.")
        merge_event_data(label, inc_dir)
        return

    for idx, pub in enumerate(pending, start=1):
        pub_str = str(pub)
        pub_name = pub_str.split(".")[-1]

        print(f"\n[{idx}/{len(pending)}] Starting publisher: {pub_name}")
        print(f"  Range: {start_dt.date()} → {end_dt.date()}")
        print(f"  Target: up to {cap} articles")

        records = []
        try:
            crawler = CCNewsCrawler(
                pub,
                start=start_dt,
                end=end_dt,
                processes=args.processes,
            )

            for article_idx, article in enumerate(crawler.crawl(max_articles=cap), start=1):
                print(f"    got article {article_idx}")

                pub_date = article.publishing_date
                records.append({
                    "url": str(article.html.responded_url),
                    "publisher": article.publisher,
                    "title": article.title or "",
                    "body": article.plaintext or "",
                    "authors": ", ".join(article.authors) if article.authors else "",
                    "topics": ", ".join(article.topics) if article.topics else "",
                    "publishing_date": str(pub_date.date()) if pub_date else "",
                    "event_label": label,
                    "event_type": event_type,
                    "is_control": is_control,
                })

            print(f"  Finished {pub_name}: {len(records)} articles")

            if records:
                df = pd.DataFrame(records, columns=OUTPUT_COLUMNS)
                safe_name = pub_name.replace(".", "_")
                out_file = os.path.join(inc_dir, f"{safe_name}.parquet")
                df.to_parquet(out_file, index=False)
                print(f"  Saved incremental → {out_file}")
            else:
                print(f"  No articles found for {pub_name}")

            ep["done_publishers"].append(pub_str)
            ep["n_articles"] = ep.get("n_articles", 0) + len(records)
            progress[label] = ep
            save_progress(progress)

        except KeyboardInterrupt:
            print("\nInterrupted by user. Progress has been saved so far.")
            raise
        except Exception as e:
            print(f"  ⚠️ Error in {pub_name}: {e}")

        finally:
            del records
            gc.collect()

    merge_event_data(label, inc_dir)


# ══════════════════════════════════════════════════════════════════════════════
# ❾ BUILD COMBINED CORPUS
# ══════════════════════════════════════════════════════════════════════════════

def build_corpus() -> None:
    parquet_files = [
        os.path.join(root, fn)
        for root, _, files in os.walk(OUTPUT_DIR)
        for fn in files
        if fn.endswith(".parquet")
        and "corpus" not in fn
        and "incremental" not in root
    ]

    if not parquet_files:
        print("No event parquet files found.")
        return

    corpus = pd.concat([pd.read_parquet(p) for p in parquet_files], ignore_index=True)
    corpus.drop_duplicates(subset=["url"], inplace=True)

    out = os.path.join(OUTPUT_DIR, "corpus_all.parquet")
    corpus.to_parquet(out, index=False)

    print("\n" + "═" * 72)
    print(f"Corpus total articles : {len(corpus):,}")
    print(f"Unique publishers     : {corpus['publisher'].nunique()}")
    print(f"Saved → {out}")


# ══════════════════════════════════════════════════════════════════════════════
# ❿ MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect women's protest news from CC-News via Fundus."
    )
    parser.add_argument("--event", type=str, default=None, help="Run one event by label")
    parser.add_argument("--no-control", action="store_true", help="Skip control weeks")
    parser.add_argument("--reset-all", action="store_true", help="Delete saved progress")
    parser.add_argument(
        "--per-publisher",
        type=int,
        default=MAX_PER_PUBLISHER,
        help=f"Articles per publisher for event windows (default {MAX_PER_PUBLISHER})",
    )
    parser.add_argument(
        "--control-per-publisher",
        type=int,
        default=CONTROL_PER_PUBLISHER,
        help=f"Articles per publisher for control windows (default {CONTROL_PER_PUBLISHER})",
    )
    parser.add_argument(
        "--processes",
        type=int,
        default=DEFAULT_PROCESSES,
        help=f"CCNewsCrawler internal processes (default {DEFAULT_PROCESSES})",
    )
    parser.add_argument("--corpus-only", action="store_true", help="Only merge final corpus")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if args.corpus_only:
        build_corpus()
        return

    if args.reset_all and os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        print("✓ Progress wiped.")

    progress = load_progress()

    all_events = list(EVENTS)
    if not args.no_control:
        for ev in EVENTS:
            ctrl = build_control_week(ev)
            if ctrl:
                all_events.append(ctrl)

    if args.event:
        all_events = [e for e in all_events if e["label"] == args.event]
        if not all_events:
            print(f"❌ Event not found: {args.event}")
            print("Available labels:")
            for ev in EVENTS:
                print(f"  - {ev['label']}")
            return

    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║   Women's Protest Events — Optimized Local Collector            ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print(f"Active publishers       : {len(ACTIVE_PUBLISHERS)}")
    print(f"Publisher list          : {[str(p).split('.')[-1] for p in ACTIVE_PUBLISHERS]}")
    print(f"Per publisher (events)  : {args.per_publisher}")
    print(f"Per publisher (control) : {args.control_per_publisher}")
    print(f"Processes per crawler   : {args.processes}")
    print(f"Events to run           : {len(all_events)}")
    print(f"Output dir              : {OUTPUT_DIR}/")

    for event in all_events:
        process_event(event, progress, args)

    build_corpus()


if __name__ == "__main__":
    main()
