"""Evidence from indexed report titles and recognizable spreadsheet tables."""

import datetime
import re
from pathlib import Path

from catalog import clean

DATE = re.compile(r"(?<!\d)(\d{4}|\d{2})[-.](\d{1,2})[-.](\d{1,2})(?!\d)")
CELL = re.compile(r"(?:^| \| )([A-Z]+)(\d+)=")
RECORD_QUERY = re.compile(r"(\d{3})\s+(\d{4}|\d{2})[-.](\d{1,2})[-.](\d{1,2})")
DATE_LABELS = {
    "교환일": "교환일", "교체일": "교체일", "작업일": "작업일",
    "점검날짜": "점검일", "점검일": "점검일", "완료일": "완료일",
}
EQUIPMENT_LABELS = {"호기", "장비번호", "장비", "q/c", "qc"}


def calendar_date(year, month, day):
    try:
        year = int(year) + (2000 if len(year) == 2 else 0)
        return datetime.date(year, int(month), int(day))
    except ValueError:
        return None


def record_query(query):
    match = RECORD_QUERY.fullmatch(clean(query).strip())
    if match:
        date = calendar_date(*match.groups()[1:])
        if date:
            return match[1], date
    return None


def has_date(text, date):
    return any(calendar_date(*m.groups()) == date for m in DATE.finditer(text))


def spreadsheet_rows(chunks):
    sheets = {}
    for chunk in chunks:
        location = re.fullmatch(r"(.+?)\s+(?:행\s*|行)?[A-Z]*(\d+)", chunk["location"])
        if not location:
            continue
        cells = {}
        matches = list(CELL.finditer(chunk["text"]))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(chunk["text"])
            cells[match[1]] = (match[1] + match[2], chunk["text"][match.end():end].strip())
        if cells:
            sheets.setdefault(location[1], []).append(cells)
    return sheets


def record_evidence(chunks, equipment, date):
    for sheet, rows in spreadsheet_rows(chunks).items():
        equipment_column = None
        date_columns = {}
        equipment_cells = {}
        for row in rows:
            values = {column: clean(value).strip() for column, (_, value) in row.items()}
            header = next((column for column, value in values.items() if value in EQUIPMENT_LABELS), None)
            if header:
                equipment_column = header
                date_columns = {column: DATE_LABELS[value] for column, value in values.items() if value in DATE_LABELS}
                equipment_cells = {column: cell for column, cell in row.items() if re.fullmatch(r"\d{3}", cell[1])}
                continue
            # One work record per row. A new header resets the column mapping.
            if equipment_column in row and values[equipment_column] == equipment:
                for column, label in date_columns.items():
                    if column in row and has_date(row[column][1], date):
                        return evidence(sheet, row[equipment_column], row[column], label)
            # Equipment across the columns, with the date role named in a row label.
            label = next((DATE_LABELS[value] for value in values.values() if value in DATE_LABELS), None)
            if label:
                for column, equipment_cell in equipment_cells.items():
                    if equipment_cell[1] == equipment and column in row and has_date(row[column][1], date):
                        return evidence(sheet, equipment_cell, row[column], label)
    return None


def evidence(sheet, equipment_cell, date_cell, label):
    return {
        "label": f"같은 기록에서 확인한 {label}",
        "location": f"{sheet} {equipment_cell[0]} · {date_cell[0]}",
        "snippet": f"{equipment_cell[0]} 장비 {equipment_cell[1]}호\n{date_cell[0]} {label} {date_cell[1]}",
    }


def report_period(path, query):
    year = re.search(r"(?<!\d)((?:19|20)\d{2})년", query)
    month = re.search(r"(?<!\d)(1[0-2]|0?[1-9])월", query)
    if not year or not month or not re.search(r"통계|보고", query):
        return None
    name = Path(path).stem
    if not re.search(r"통계|보고", name):
        return None
    months = {int(value) for value in re.findall(r"(?<!\d)(1[0-2]|0?[1-9])월", name)}
    if len(months) != 1:
        return None
    title_month = months.pop()
    title_years = re.findall(r"(?<!\d)((?:19|20)\d{2})년", name)
    directory_years = re.findall(r"(?:^|/)((?:19|20)\d{2})년(?:[^/]*)(?:/|$)", path)
    years = set(title_years or directory_years)
    if len(years) != 1:
        return None
    title_year = years.pop()
    label = "파일명 기준" if title_years else "파일명·경로 기준"
    return {
        "label": f"{label} {title_year}년 {title_month}월",
        "matches": title_year == year[1] and title_month == int(month[1]),
    }
