from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            value = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append(value)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def load_market_snapshot(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_market_snapshot(snapshot: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@dataclass(slots=True)
class CbrClient:
    timeout_seconds: int = 20
    user_agent: str = "SupplyMarginAI/1.0 market-data contact=admin@example.invalid"

    def _get(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return response.read()

    def macro_rows(self, start: date, end: date) -> list[dict[str, float | str]]:
        query = urllib.parse.urlencode(
            {
                "UniDbQuery.Posted": "True",
                "UniDbQuery.From": start.strftime("%d.%m.%Y"),
                "UniDbQuery.To": end.strftime("%d.%m.%Y"),
            }
        )
        url = f"https://www.cbr.ru/hd_base/infl/?{query}"
        html = self._get(url).decode("utf-8", errors="replace")
        parser = _TableParser()
        parser.feed(html)
        rows: list[dict[str, float | str]] = []
        for row in parser.rows:
            if len(row) < 4 or not re.fullmatch(r"\d{2}\.\d{4}", row[0]):
                continue
            try:
                rows.append(
                    {
                        "month": row[0],
                        "key_rate": float(row[1].replace(",", ".")),
                        "inflation_yoy": float(row[2].replace(",", ".")),
                        "inflation_target": float(row[3].replace(",", ".")),
                    }
                )
            except ValueError:
                continue
        if not rows:
            raise RuntimeError("Банк России вернул страницу без распознаваемой таблицы.")
        return rows

    def current_key_rate(self, start: date, end: date) -> tuple[str, float]:
        query = urllib.parse.urlencode(
            {
                "UniDbQuery.Posted": "True",
                "UniDbQuery.From": start.strftime("%d.%m.%Y"),
                "UniDbQuery.To": end.strftime("%d.%m.%Y"),
            }
        )
        url = f"https://www.cbr.ru/hd_base/keyrate/?{query}"
        html = self._get(url).decode("utf-8", errors="replace")
        parser = _TableParser()
        parser.feed(html)
        for row in parser.rows:
            if len(row) < 2 or not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", row[0]):
                continue
            try:
                return row[0], float(row[1].replace(",", "."))
            except ValueError:
                continue
        raise RuntimeError("Банк России не вернул текущую ключевую ставку.")

    def fx_daily(self, on_date: date) -> dict[str, float]:
        url = (
            "https://www.cbr.ru/scripts/XML_daily.asp?date_req="
            + on_date.strftime("%d/%m/%Y")
        )
        root = ElementTree.fromstring(self._get(url))
        result: dict[str, float] = {}
        for item in root.findall("Valute"):
            code = item.findtext("CharCode")
            nominal = float((item.findtext("Nominal") or "1").replace(",", "."))
            value = float((item.findtext("Value") or "0").replace(",", "."))
            if code:
                result[f"{code}_RUB"] = value / nominal
        return result

    def refresh_snapshot(self, target_path: str | Path) -> dict[str, Any]:
        today = date.today()
        rows = self.macro_rows(date(today.year - 1, 1, 1), today)
        latest = rows[0]
        key_rate_date, key_rate_value = self.current_key_rate(
            today.replace(day=1),
            today,
        )
        fx = self.fx_daily(today)
        current = load_market_snapshot(target_path)
        current["as_of"] = today.isoformat()
        current["updated_at"] = datetime.now().astimezone().isoformat()
        current["actual"]["key_rate_percent"] = key_rate_value
        current["actual"]["key_rate_observation_date"] = datetime.strptime(
            key_rate_date, "%d.%m.%Y"
        ).date().isoformat()
        current["actual"]["inflation_yoy_percent"] = latest["inflation_yoy"]
        current["actual"]["fx"] = fx
        current["actual"]["macro_history"] = rows
        current["sources"]["cbr_macro_table"] = (
            "https://www.cbr.ru/hd_base/infl/"
        )
        current["sources"]["cbr_fx"] = (
            "https://www.cbr.ru/currency_base/daily/"
        )
        save_market_snapshot(current, target_path)
        return current
