"""从常规赛排班表追加赛程。

用法：
    python tools/append_schedule_from_xlsx.py "C:\\path\\第四届DL常规赛排班表.xlsx" --dry-run
    python tools/append_schedule_from_xlsx.py "C:\\path\\第四届DL常规赛排班表.xlsx" \
        --base-url http://127.0.0.1:8000 --token YOUR_ADMIN_TOKEN

脚本只调用追加接口，不会覆盖已有日期。默认读取第一个工作表：
日期在 B 列，出战队伍编码在 C 列，例如 ABCD。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


TEAM_NAMES = {
    "A": "nzmzd对不队",
    "B": "非日常麻将结社",
    "C": "新能源蛇螺",
    "D": "国士无双",
    "E": "爆☆杀麻将格斗俱乐部",
    "F": "鹿旺旺雪饼",
}
TEAM_NUMBERS = {code: index for index, code in enumerate(TEAM_NAMES, start=1)}
CELL_RE = re.compile(r"([A-Z]+)([0-9]+)$")
NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(book.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values = []
    for item in root.findall("x:si", NS):
        values.append("".join(node.text or "" for node in item.iter()
                               if node.tag.endswith("}t") or node.tag == "t"))
    return values


def _cell_value(cell: ET.Element, strings: list[str]):
    value = cell.find("x:v", NS)
    text = "" if value is None else (value.text or "")
    if cell.attrib.get("t") == "s" and text:
        return strings[int(text)]
    if cell.attrib.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.iter()
                       if node.tag.endswith("}t") or node.tag == "t")
    return text


def _read_rows(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as book:
        strings = _shared_strings(book)
        root = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
        rows = []
        for row in root.findall(".//x:sheetData/x:row", NS):
            cells = {}
            for cell in row.findall("x:c", NS):
                match = CELL_RE.match(cell.attrib.get("r", ""))
                if match:
                    cells[match.group(1)] = _cell_value(cell, strings)
            if not cells or not cells.get("A", "").strip().isdigit():
                continue
            try:
                day_number = int(cells["A"])
                serial = float(cells["B"])
                match_date = (dt.datetime(1899, 12, 30) +
                              dt.timedelta(days=serial)).date()
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"第 {row.attrib.get('r', '?')} 行日期无效：{exc}") from exc
            codes = re.sub(r"[^A-F]", "", str(cells.get("C", "")).upper())
            if len(codes) != 4 or len(set(codes)) != 4:
                raise ValueError(f"第 {day_number} 天队伍编码无效：{cells.get('C', '')!r}")
            rows.append({
                "date": match_date.isoformat(),
                "team_numbers": [TEAM_NUMBERS[code] for code in codes],
                "note": f"第{day_number}比赛日：" + "、".join(
                    f"{TEAM_NUMBERS[code]}号{TEAM_NAMES[code]}" for code in codes),
            })
    if not rows:
        raise ValueError("没有读取到有效赛程行，请确认表格格式为 A=序号、B=日期、C=队伍编码")
    return rows


def _post_append(base_url: str, token: str, rows: list[dict]) -> dict:
    payload = json.dumps({"rows": rows}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/admin/schedule/append",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"接口返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接服务：{exc.reason}") from exc


def _default_token() -> str:
    env_token = os.environ.get("ADMIN_TOKEN", "").strip()
    if env_token:
        return env_token
    token_file = Path(__file__).resolve().parents[1] / "data" / ".admin_token"
    if token_file.is_file():
        return token_file.read_text(encoding="utf-8").strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="从 Excel 追加四队出战赛程")
    parser.add_argument("xlsx", type=Path, help="第四届DL常规赛排班表.xlsx")
    parser.add_argument("--base-url", default=os.environ.get("MLA_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=_default_token(),
                        help="管理员 API Token，默认读取 ADMIN_TOKEN 或 data/.admin_token")
    parser.add_argument("--dry-run", action="store_true", help="只校验并打印 JSON，不请求接口")
    args = parser.parse_args()

    try:
        rows = _read_rows(args.xlsx)
        print(f"已读取 {len(rows)} 个比赛日：{rows[0]['date']} 至 {rows[-1]['date']}")
        print(json.dumps({"rows": rows}, ensure_ascii=False, indent=2))
        if args.dry_run:
            return 0
        if not args.token:
            raise RuntimeError("缺少管理员 Token，请使用 --token 或设置 ADMIN_TOKEN")
        result = _post_append(args.base_url, args.token, rows)
        print(f"追加成功：新增 {result.get('added', 0)} 天，当前共 {result.get('total', '?')} 天")
        return 0
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
