"""Download a real public dataset (NYC Yellow Taxi trips) into a DuckDB database.

Usage:
    python data/download_public_data.py [--year 2024] [--months 1] [--out samples/nyc_taxi.duckdb]

Requires the backend deps (httpx, duckdb): pip install -e backend
Downloads ~50 MB/month of parquet from the NYC TLC public CDN, then registers it
as a `trips` table. Connect in DQ Sentinel with:
    duckdb:///<absolute path>/samples/nyc_taxi.duckdb
"""

import argparse
from pathlib import Path

URL = "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_{year}-{month:02d}.parquet"


def download(url: str, dest: Path) -> None:
    import httpx

    print(f"Downloading {url}")
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {done / 1e6:.0f} / {total / 1e6:.0f} MB", end="", flush=True)
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--months", type=int, default=1, help="how many months starting at January")
    parser.add_argument(
        "--out", default=str(Path(__file__).resolve().parent.parent / "samples" / "nyc_taxi.duckdb")
    )
    args = parser.parse_args()

    import duckdb

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    parquet_dir = out.parent / "parquet"
    parquet_dir.mkdir(exist_ok=True)

    files = []
    for month in range(1, args.months + 1):
        dest = parquet_dir / f"yellow_{args.year}-{month:02d}.parquet"
        if not dest.exists():
            download(URL.format(year=args.year, month=month), dest)
        else:
            print(f"Already downloaded: {dest.name}")
        files.append(dest)

    con = duckdb.connect(str(out))
    file_list = ", ".join(f"'{f.as_posix()}'" for f in files)
    con.execute(f"CREATE OR REPLACE TABLE trips AS SELECT * FROM read_parquet([{file_list}])")
    n = con.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
    con.close()

    print(f"\nWrote {out} with {n:,} trips")
    print("Connect with DSN:")
    print(f"  duckdb:///{out.as_posix()}")
    print(
        "\nThis dataset has plenty of real DQ quirks: $0 and negative fares, 0-passenger trips,"
        " far-past/future pickup datetimes, extreme trip distances — perfect for profiling."
    )


if __name__ == "__main__":
    main()
