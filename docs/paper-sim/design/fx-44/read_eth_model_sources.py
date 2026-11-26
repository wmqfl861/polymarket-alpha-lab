"""Re-read the two recorded ETH model inputs on the existing isolated project database.

Run with the project Python and pass its managed research database checkout as the sole argument.
Only the two named snapshots are read. The program prints counts and source clocks.
"""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sys

checkout = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(checkout / "src"))

from polymarket_alpha_lab.papersim import prepare_inputs, snapshots, store_psycopg
from polymarket_alpha_lab.papersim.adapters import contracts, prices


def require(matches):
    if not matches:
        raise ValueError("recorded_model_input_differs")


def read_original(conn, expected):
    digest = expected["sha256"]
    info = snapshots.verify_snapshot(conn, digest, expected_schema_version=1)
    require(info["kind"] == prepare_inputs.MODEL_RESPONSE_KIND)
    require(info["byte_length"] == expected["byte_length"])
    require(info["fetched_at"] == datetime.fromisoformat(expected["fetched_at"]))
    prepare_inputs._source_request(expected["kind"], info["source"])
    original = snapshots.read_snapshot(conn, digest)
    return contracts.FetchEvidence.from_bytes(source_kind=expected["kind"], source_reference=info["source"],
                                              fetched_at=info["fetched_at"], raw_bytes=original["content"])


def readback(database_root):
    receipt = json.loads(Path(__file__).with_name("2026-11-26-eth-b1-model-sources.json").read_bytes())
    scope, observations = receipt["prescope"], receipt["collection"]["originals"]
    require([x["kind"] for x in observations] == ["crypto-candles-binance", "crypto-spot-binance"])
    start = datetime.fromisoformat(scope["candle_start_inclusive"])
    end = datetime.fromisoformat(scope["candle_end_exclusive"])
    with closing(store_psycopg.connect_managed(database_root)) as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        candles, spot = (read_original(conn, item) for item in observations)
        require(spot.source_reference == prepare_inputs._spot_reference("binance", scope["pair"]))
        bars, gaps, duplicates, unfinished, outside = prepare_inputs._candle_rows(
            "binance", scope["pair"], (candles,), prices._epoch_seconds(start), prices._epoch_seconds(end),
            prices._epoch_seconds(candles.fetched_at))
        price = prices._parse_spot_price(spot.raw_bytes, "binance", prices._BINDINGS["binance"], "ETH")
        require(price == receipt["collection"]["adapters"][1]["spot_price"])
        require(len(bars) == scope["expected_closed_bars"] and bars[0].open_time == start and bars[-1].close_time == end)
        require((gaps, duplicates, unfinished, outside) == (0, 0, 0, 0))
        require(all(a.close_time == b.open_time for a, b in zip(bars, bars[1:])))
        return {
            "isolated_pg_originals_verified": 2, "request_metadata_checked": True,
            "complete_six_hour_bars": len(bars), "gaps": gaps, "duplicates": duplicates,
            "unfinished": unfinished, "outside_window": outside, "first_bar_open_utc": bars[0].open_time.isoformat(),
            "last_bar_close_utc": bars[-1].close_time.isoformat(), "candles_received_utc": candles.fetched_at.isoformat(),
            "spot_received_utc": spot.fetched_at.isoformat(), "spot_ethusdt": price, "public_fetches": 0,
        }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("expected one managed research database checkout")
    try:
        print(json.dumps(readback(Path(sys.argv[1])), sort_keys=True))
    except Exception:
        raise SystemExit("readback unavailable or recorded model inputs differ")
