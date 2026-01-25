import re
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import List, Optional, Tuple

import streamlit as st


# =========================
# 0) 모델
# =========================
@dataclass
class KMessage:
    sender: str
    sent_at: datetime
    header_lines: List[str]   # 원문 헤더 라인(보관용)
    body_lines: List[str]     # 원문 본문 라인(보관용)

    def body_text(self) -> str:
        return "\n".join(self.body_lines).strip()

    def to_block_text(self, include_header: bool = True) -> str:
        """
        Word / 한글(HWP) 붙여넣기 최적화 포맷
        - 메시지 1개 = 하나의 블록
        - 블록 사이 빈 줄 1개
        """
        body = self.body_text()

        if include_header:
            date_iso = self.sent_at.strftime("%Y-%m-%d %H:%M")
            header = self.sender
            block = f"[{header} | {date_iso}]\n{body}"
        else:
            block = body

        return block.strip()
    
# =========================
# 1) 날짜 파서 (카톡 "입력 날짜" 기준)
# =========================
# 예) "1월 23일 오후 9:05", "01월 03일 오전 10:11"
RE_KO_MD_TIME = re.compile(
    r"(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일\s*(?:(?P<ampm>오전|오후)\s*)?(?P<h>\d{1,2})\s*:\s*(?P<min>\d{2})"
)

# 예) "2026년 1월 23일 오후 9:05"
RE_KO_YMD_TIME = re.compile(
    r"(?P<y>\d{4})\s*년\s*(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일\s*(?:(?P<ampm>오전|오후)\s*)?(?P<h>\d{1,2})\s*:\s*(?P<min>\d{2})"
)

# 날짜 구분선 예: --------------- 2026년 1월 4일 일요일 ---------------
RE_DATE_DIVIDER = re.compile(r"-+\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일"
)

# 시간만 있는 줄 예: 오전 9:18 / 오후 12:03
RE_TIME_ONLY = re.compile(r"(오전|오후)\s*(\d{1,2}):(\d{2})"

)
# 날짜 단독 줄 예: 2026년 1월 8일 목요일
RE_DATE_LINE = re.compile(
    r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일"
)

# 시간만 있는 줄 예: 오전 9:18 / 오후 12:03
RE_TIME_ONLY = re.compile(
    r"(오전|오후)\s*(\d{1,2}):(\d{2})"
)

# 한 줄 메시지 예: [이름] [오전 8:47] 본문
RE_INLINE_MSG = re.compile(
    r"^\[(?P<sender>[^\]]+)\]\s*\[(?P<ampm>오전|오후)\s*(?P<h>\d{1,2}):(?P<min>\d{2})\]\s*(?P<body>.*)$"
)

def _ampm_to_24h(h: int, ampm: Optional[str]) -> int:
    if not ampm:
        return h
    if ampm == "오전":
        return 0 if h == 12 else h
    if ampm == "오후":
        return 12 if h == 12 else h + 12
    return h


def _infer_year(month: int, day: int, today: date) -> int:
    """
    카톡에는 보통 '연도'가 없어서, 오늘 기준으로 가장 그럴듯한 연도 추정.
    - 기본: 오늘 연도
    - 만약 (month,day)가 '오늘보다 미래'면 전년도일 가능성이 높으니 -1
    """
    candidate = date(today.year, month, day)
    if candidate > today:
        return today.year - 1
    return today.year


def parse_kakao_datetime(text: str, today: date) -> Optional[Tuple[datetime, str]]:
    """
    문자열에서 카톡 헤더에 있는 '보낸 날짜/시간'을 찾아 datetime으로 변환.
    반환: (datetime, 매칭된 원문 날짜 문자열)
    """
    m = RE_KO_YMD_TIME.search(text)
    if m:
        y = int(m.group("y"))
        mo = int(m.group("m"))
        d = int(m.group("d"))
        h = int(m.group("h"))
        mi = int(m.group("min"))
        h24 = _ampm_to_24h(h, m.group("ampm"))
        dt = datetime(y, mo, d, h24, mi)
        return dt, m.group(0)

    m = RE_KO_MD_TIME.search(text)
    if m:
        mo = int(m.group("m"))
        d = int(m.group("d"))
        h = int(m.group("h"))
        mi = int(m.group("min"))
        y = _infer_year(mo, d, today)
        h24 = _ampm_to_24h(h, m.group("ampm"))
        dt = datetime(y, mo, d, h24, mi)
        return dt, m.group(0)

    return None


# =========================
# 2) 메시지 분리 (PC/모바일 혼합 대응)
# =========================
def is_datetime_line(line: str, today: date) -> bool:
    return parse_kakao_datetime(line, today) is not None


def split_messages(raw_text: str, today: date) -> List[KMessage]:
    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    messages: List[KMessage] = []

    current_date: Optional[date] = None
    current_sender: Optional[str] = None
    current_dt: Optional[datetime] = None
    current_header_lines: List[str] = []
    current_body_lines: List[str] = []

    def flush():
        nonlocal current_sender, current_dt, current_header_lines, current_body_lines
        if current_dt and current_body_lines:
            messages.append(
                KMessage(
                    sender=current_sender or "UNKNOWN",
                    sent_at=current_dt,
                    header_lines=current_header_lines[:],
                    body_lines=current_body_lines[:],
                )
            )
        current_sender = None
        current_dt = None
        current_header_lines = []
        current_body_lines = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 1️⃣ 날짜 단독 줄 인식
        m_date = RE_DATE_LINE.search(line)
        if m_date:
            y, m, d = map(int, m_date.groups())
            current_date = date(y, m, d)
            i += 1
            continue

        # 1.2️⃣ 한 줄 메시지 인식 (PC/iOS 공통)
        m_inline = RE_INLINE_MSG.match(line)
        if current_date and m_inline:
            flush()

            sender = m_inline.group("sender")
            ampm = m_inline.group("ampm")
            h = int(m_inline.group("h"))
            minute = int(m_inline.group("min"))

            if ampm == "오전":
                hour = 0 if h == 12 else h
            else:
                hour = 12 if h == 12 else h + 12

            current_sender = sender
            current_dt = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                hour,
                minute,
            )

            current_header_lines = [
                f"[{sender}] [{ampm} {h}:{minute:02d}]"
            ]
            current_body_lines = []
            body = m_inline.group("body").strip()
            if body:
                current_body_lines.append(body)

            i += 1
            continue

        # 3️⃣ 이름 + 시간 구조 (날짜가 잡힌 상태에서만)
        if (
            current_date
            and line
            and i + 1 < len(lines)
            and RE_TIME_ONLY.search(lines[i + 1])
        ):
            flush()
            current_sender = line

            m_time = RE_TIME_ONLY.search(lines[i + 1])
            ampm, hh, mm = m_time.groups()
            h = int(hh)
            minute = int(mm)

            if ampm == "오전":
                hour = 0 if h == 12 else h
            else:
                hour = 12 if h == 12 else h + 12

            current_dt = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                hour,
                minute,
            )

            current_header_lines = [line, lines[i + 1].strip()]
            current_body_lines = []
            i += 2
            continue

        #  본문 누적
        if current_dt:
            current_body_lines.append(lines[i])

        i += 1

    flush()
    return messages


# =========================
# 3) 필터
# =========================
def normalize_lines(text: str) -> List[str]:
    return [t.strip() for t in text.splitlines() if t.strip()]


def filter_messages(
    messages: List[KMessage],
    start_d: date,
    end_d: date,
    senders: List[str],
    keywords: List[str],
) -> List[KMessage]:
    """
    - 기간: start_d ~ end_d (포함)
    - 발신자: header/추정 sender에 포함 문자열 매칭(부분 포함) -> 실무 친화적
    - 키워드:
        - 비어있으면: 메시지 전부 통과
        - 있으면: 본문에 키워드 하나라도 포함되면 통과 (OR)
    """
    results: List[KMessage] = []

    for m in messages:
        md = m.sent_at.date()
        if not (start_d <= md <= end_d):
            continue

        # 발신자 필터 (필수로 쓰는 걸 권장하지만, 함수 자체는 빈 리스트면 전체 허용)
        if senders:
            # sender 자체 + 헤더 텍스트에도 포함 여부 체크
            header_join = " ".join(m.header_lines)
            if not any(s in m.sender or s in header_join for s in senders):
                continue

        if keywords:
            body = m.body_text()
            if not any(k in body for k in keywords):
                continue

        results.append(m)

    return results


# =========================
# 4) Streamlit UI
# =========================
st.set_page_config(page_title="카톡 발췌 도구", layout="wide")
st.title("📄 카카오톡 메시지 발췌 도구 (로컬)")
st.caption("입력(파일 업로드/붙여넣기) → 발신자/키워드 → 자동 기간(최근 7일) → 결과 텍스트")

today = date.today()

colL, colR = st.columns([1, 1])

with colL:
    st.subheader("① 입력")
    uploaded_file = st.file_uploader("텍스트 파일 업로드 (.txt)", type=["txt"])
    raw_text = ""

    if uploaded_file is not None:
        # 업로드 파일 우선
        data = uploaded_file.read()
        try:
            raw_text = data.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = data.decode("cp949", errors="replace")
        st.info("파일이 업로드되었습니다. 아래 붙여넣기 입력은 무시됩니다.")
    else:
        raw_text = st.text_area(
            "또는 카톡 대화 내용 붙여넣기",
            height=260,
            placeholder="PC/모바일 카톡에서 복사한 내용을 그대로 붙여넣으세요."
        )

    st.subheader("② 조건")
    sender_input = st.text_area(
        "발신자 이름 (한 줄에 한 명) — 권장: 반드시 입력",
        height=90,
        placeholder="예)\n김길동\n홍길동"
    )
    keyword_input = st.text_area(
        "포함 단어 (선택, 한 줄에 하나) — 비우면 ‘해당 발신자 메시지 전체’",
        height=110,
        placeholder="예)\n출석\n결석\n헌금\n성경읽기"
    )

with colR:
    st.subheader("③ 처리 결과")

    if raw_text.strip():
        msgs = split_messages(raw_text, today=today)

        if not msgs:
            st.error("메시지 헤더(날짜/시간)를 인식하지 못했습니다. 카톡 복사 형식을 확인해 주세요.")
        else:
            # 기준일(가장 최신 메시지 날짜)
            end_date_auto = max(m.sent_at.date() for m in msgs)
            start_date_auto = end_date_auto - timedelta(days=6)

            # 옵션: 기간 직접 조정
            manual = st.checkbox("기간 직접 조정", value=False)
            if manual:
                start_d = st.date_input("시작일", value=start_date_auto)
                end_d = st.date_input("종료일", value=end_date_auto)
                if start_d > end_d:
                    st.warning("시작일이 종료일보다 늦습니다. 자동으로 교정합니다.")
                    start_d, end_d = end_d, start_d
            else:
                start_d, end_d = start_date_auto, end_date_auto
                st.info(f"📅 자동 기간: {start_d.isoformat()} ~ {end_d.isoformat()} (기준일: {end_d.isoformat()})")

            senders = normalize_lines(sender_input)
            keywords = normalize_lines(keyword_input)

            if not senders:
                st.warning("발신자 이름이 비어 있습니다. (원하면 가능하지만) 실무에서는 발신자 입력을 권장해요.")

            filtered = filter_messages(
                messages=msgs,
                start_d=start_d,
                end_d=end_d,
                senders=senders,
                keywords=keywords,
            )

            st.write(f"총 메시지: **{len(msgs)}** / 필터 통과: **{len(filtered)}**")

            include_header = st.checkbox("결과에 헤더(이름/날짜) 포함", value=True)

            output_blocks = []
            for m in filtered:
                # 본문이 완전 비어있는 메시지는 제외(원하면 포함 가능)
                if not m.body_text():
                    continue
                output_blocks.append(m.to_block_text(include_header=include_header))

            output_text = "\n\n".join(output_blocks).strip()

            MAX_PREVIEW_CHARS = 8000  # Cloud 안전선

            preview_text = output_text[:MAX_PREVIEW_CHARS]
            if len(output_text) > MAX_PREVIEW_CHARS:
                preview_text += "\n\n... (이하 생략, 다운로드 파일에 전체 포함)"

            st.text_area(
                "④ 결과 미리보기 (일부만 표시)",
                value=preview_text,
                height=420
            )

            # 텍스트 다운로드(선택)
            st.download_button(
                "⬇️ 결과를 txt로 다운로드",
                data=output_text.encode("utf-8"),
                file_name=f"extract_{start_d.isoformat()}_{end_d.isoformat()}.txt",
                mime="text/plain"
            )

            st.caption("⚠️ 결과는 원문을 수정하지 않고, 조건에 맞는 메시지만 발췌합니다.")

    else:
        st.info("왼쪽에서 txt 파일 업로드 또는 붙여넣기를 해주세요.")
