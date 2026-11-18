from __future__ import annotations

import argparse
import shutil
import urllib.error
import urllib.request
from pathlib import Path

DOI_PAGE = "https://onlinelibrary.wiley.com/doi/abs/10.1002/nav.21957"
FILES = [
    "nav21957-sup-0001-supinfo01.xlsx",
    "nav21957-sup-0002-supinfo02.xlsx",
    "nav21957-sup-0003-supinfo03.xlsx",
    "nav21957-sup-0004-supinfo04.xlsx",
    "nav21957-sup-0005-supinfo05.xlsx",
]
DOWNLOAD_TEMPLATE = (
    "https://onlinelibrary.wiley.com/action/downloadSupplement"
    "?doi=10.1002%2Fnav.21957&file={filename}"
)


def download(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for filename in FILES:
        destination = target / filename
        request = urllib.request.Request(
            DOWNLOAD_TEMPLATE.format(filename=filename),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/126 Safari/537.36"
                ),
                "Referer": DOI_PAGE,
                "Accept": (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet,application/octet-stream;q=0.9,*/*;q=0.8"
                ),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                with destination.open("wb") as output:
                    shutil.copyfileobj(response, output)
            print(f"Downloaded: {destination} ({destination.stat().st_size:,} bytes)")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            failed.append(filename)
            print(f"Failed: {filename}: {exc}")

    if failed:
        joined = "\n- ".join(failed)
        raise SystemExit(
            "Wiley ограничил автоматическую загрузку для текущего IP. "
            "Это не ошибка модели. Откройте страницу публикации в браузере:\n"
            f"{DOI_PAGE}\n"
            "и сохраните Supporting Information в указанную папку:\n- "
            f"{joined}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download the five lawful Wiley supporting XLSX files."
    )
    parser.add_argument("--output", default="data/open/supermarket_2019")
    args = parser.parse_args()
    download(Path(args.output))


if __name__ == "__main__":
    main()
