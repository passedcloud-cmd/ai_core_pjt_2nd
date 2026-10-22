"""
data_prep/painters.py  (요구사항 F2001)
=======================================
화가 메타데이터(data/painters.json)를 불러와서,
화가 1명을 Vector DB에 넣을 "검색용 텍스트 1개"로 만드는 모듈.

도메인(화가)에 묶인 코드는 이 data_prep 폴더에만 모아둔다. (NF2005 모듈 분리)
다른 도메인으로 바꾸고 싶으면 이 파일만 새로 만들면 된다.

[확인 방법]  (명세서 4.1 검증)
  python -m data_prep.painters
  → 불러온 화가 수와, 화가 1명의 검색용 텍스트를 출력한다.
"""

import json
from pathlib import Path

PAINTERS_PATH = Path("data/painters.json")


# ---------------------------------------------------------------------------
# 1) 불러오기
# ---------------------------------------------------------------------------
def load_painters(path: Path = PAINTERS_PATH) -> list[dict]:
    """painters.json을 읽어서 화가 목록을 돌려준다."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} 파일이 없어요. 먼저 'python collect_painters.py'를 실행해 주세요."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def find_painter(painters: list[dict], name: str) -> dict:
    """
    이름(한글·영문) 또는 위키데이터 ID로 화가 1명을 찾는다.
    정확히 같은 이름을 먼저 찾고, 없으면 이름에 포함된 화가 중 가장 유명한 사람을 고른다.
    """
    key = name.strip().lower()
    for p in painters:
        if key in (p["id"].lower(), p["name"].lower(), (p.get("name_en") or "").lower()):
            return p
    partial = [p for p in painters if key in p["name"].lower() or key in (p.get("name_en") or "").lower()]
    if partial:
        return min(partial, key=lambda p: p.get("rank", 10**9))
    raise ValueError(f"'{name}' 화가를 찾지 못했어요. 이름을 다시 확인해 주세요.")


# ---------------------------------------------------------------------------
# 2) 검색용 텍스트 만들기
# ---------------------------------------------------------------------------
def to_century(year: int | None) -> int | None:
    """1853 → 19 (19세기). 1900년은 19세기, 1901년부터 20세기."""
    if year is None or year <= 0:
        return None
    return (year - 1) // 100 + 1


def active_period(birth: int | None, death: int | None) -> str:
    """출생·사망 연도를 '19~20세기'처럼 사람이 검색할 법한 말로 바꾼다."""
    start, end = to_century(birth), to_century(death)
    if start and end:
        return f"{start}세기" if start == end else f"{start}~{end}세기"
    if start:
        return f"{start}세기 출생"
    return ""


def lifespan(birth: int | None, death: int | None) -> str:
    """1853, 1890 → '1853년 ~ 1890년'"""
    if birth and death:
        return f"{birth}년 ~ {death}년"
    if birth:
        return f"{birth}년 출생"
    if death:
        return f"{death}년 사망"
    return ""


def join(values: list[str]) -> str:
    """['인상주의', '후기 인상주의'] → '인상주의, 후기 인상주의'"""
    return ", ".join(v for v in values if v)


def build_index_text(painter: dict) -> str:
    """
    화가 1명을 검색용 텍스트 1개로 만든다.
    '항목: 값' 형태로 한 줄씩 적고, 값이 비어 있는 줄은 아예 넣지 않는다.
    (빈 줄이 많으면 화가끼리 텍스트가 비슷해져서 검색이 흐려진다.)
    """
    birth, death = painter.get("birth_year"), painter.get("death_year")
    name = painter["name"]
    if painter.get("name_en"):
        name += f" ({painter['name_en']})"

    lines = [
        ("이름", name),
        ("소개", painter.get("description", "")),
        ("국적", join(painter.get("nationality", []))),
        ("대륙", join(painter.get("continents", []))),
        ("생애", lifespan(birth, death)),
        ("활동 시기", active_period(birth, death)),
        ("출생지", painter.get("birth_place", "")),
        ("사조", join(painter.get("movements", []))),
        ("장르", join(painter.get("genres", []))),
        ("대표작", join(painter.get("notable_works", []))),
    ]
    return "\n".join(f"{label}: {value}" for label, value in lines if value)


# ---------------------------------------------------------------------------
# 3) 텍스트 옆에 붙일 메타데이터 만들기 (F2002 색인 때 사용)
# ---------------------------------------------------------------------------
def build_metadata(painter: dict) -> dict:
    """
    검색 텍스트와 함께 저장할 정보. 검색에는 안 쓰이지만 필터링·화면 표시·출처 표시에 쓴다.
    ChromaDB 메타데이터는 글자·숫자·참거짓만 넣을 수 있어서, 목록은 쉼표로 이어 붙인다.
    """
    return {
        "painter_id": painter["id"],
        "name": painter["name"],
        "rank": painter.get("rank", 0),
        "birth_year": painter.get("birth_year") or 0,   # 모르면 0
        "death_year": painter.get("death_year") or 0,
        "continents": join(painter.get("continents", [])),
        "source_type": "화가 메타데이터",                  # 근거 표시용 (F2007)
        "title": f"{painter['name']} · 화가 메타데이터",
        "source_url": painter.get("source_url", ""),
        "wiki_title": painter.get("wiki_title", ""),
        "wiki_url": painter.get("wiki_url", ""),
    }


def build_documents(painters: list[dict]) -> list[dict]:
    """화가 목록 → [{id, text, metadata}, ...]  (색인 단계에 그대로 넘기는 형태)"""
    return [
        {
            "id": painter["id"],   # 화가 ID로 고정 → 다시 색인해도 중복 없이 덮어쓰기 (F2002)
            "text": build_index_text(painter),
            "metadata": build_metadata(painter),
        }
        for painter in painters
    ]


# ---------------------------------------------------------------------------
# 직접 실행하면: 불러온 수와 예시 텍스트를 보여준다 (검증용)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    painters = load_painters()
    documents = build_documents(painters)

    print(f"✅ 불러온 화가 수: {len(painters)}명")
    print(f"✅ 만든 검색용 텍스트 수: {len(documents)}개\n")

    # 텍스트에 빠진 정보가 얼마나 되는지 (데이터 품질 확인)
    for label in ["사조", "장르", "대표작", "활동 시기"]:
        missing = sum(1 for d in documents if f"\n{label}:" not in d["text"])
        print(f"  - '{label}' 정보가 없는 화가: {missing}명")

    print("\n[예시] 1위 화가의 검색용 텍스트")
    print("-" * 40)
    print(documents[0]["text"])
    print("-" * 40)
