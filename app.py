import os
import sqlite3
from datetime import datetime

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    abort,
)
from groq import Groq

# =========================
# 기본 설정
# =========================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "portfolio.db")

app = Flask(__name__)

# Groq 클라이언트 (환경변수에서 키 읽기)
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# 회사 선택 드롭다운에 쓸 목록
COMPANY_OPTIONS = [
    "LH",
    "한국전력공사",
    "한국중부발전",
    "한국도로공사",
    "한국수력원자력",
    "네이버",
    "카카오",
    "삼성전자",
]


# =========================
# DB 유틸
# =========================

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """경험 테이블 생성 (없으면)."""
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experience (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                start_date TEXT,
                end_date TEXT,
                skills TEXT,
                hours INTEGER,
                created_at TEXT
            );
            """
        )
    print("DB initialized")


def fetch_all_experiences(order_by_recent=True):
    conn = get_db()
    try:
        if order_by_recent:
            rows = conn.execute(
                "SELECT * FROM experience ORDER BY start_date DESC"
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM experience").fetchall()
        return rows
    finally:
        conn.close()


def build_portfolio_text(exps):
    """AI 프롬프트용으로 활동들을 한 덩어리 텍스트로 구성."""
    lines = []
    for e in exps:
        line = f"""
- 카테고리: {e['category']}
  제목: {e['title']}
  기간: {e['start_date']} ~ {e['end_date'] or ''}
  기술/키워드: {e['skills'] or ''}
  설명: {e['description'] or ''}
  투입 시간: {e['hours']}시간
"""
        lines.append(line)
    return "\n".join(lines)


# =========================
# Groq AI 유틸
# =========================

def call_groq(prompt: str, system_msg: str) -> str:
    """공통 AI 호출 함수. 에러는 상위에서 잡는다."""
    if not client.api_key:
        raise RuntimeError("GROQ_API_KEY 환경 변수가 설정되어 있지 않습니다.")

    completion = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
    )
    return completion.choices[0].message.content


# =========================
# 라우트
# =========================

@app.route("/")
def index():
    """대시보드 메인 화면."""
    conn = get_db()
    try:
        exps = conn.execute(
            "SELECT * FROM experience ORDER BY start_date DESC"
        ).fetchall()

        # 총 활동 수, 총 시간
        total_count = len(exps)
        total_hours_row = conn.execute(
            "SELECT SUM(hours) AS total_hours FROM experience"
        ).fetchone()
        total_hours = total_hours_row["total_hours"] or 0

        # 카테고리별 개수
        categories = conn.execute(
            "SELECT category, COUNT(*) AS cnt FROM experience GROUP BY category"
        ).fetchall()
    finally:
        conn.close()

    # 최근 활동 하나만 카드에 표시
    latest = exps[0] if exps else None

    return render_template(
        "index.html",
        experiences=exps,
        latest=latest,
        total_count=total_count,
        total_hours=total_hours,
        categories=categories,
    )


@app.route("/add", methods=["GET", "POST"])
def add():
    """활동 추가 폼."""
    if request.method == "POST":
        category = request.form.get("category", "").strip()
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        start_date = request.form.get("start_date") or None
        end_date = request.form.get("end_date") or None
        skills = request.form.get("skills", "").strip()
        hours_raw = request.form.get("hours", "").strip()

        try:
            hours = int(hours_raw) if hours_raw else 0
        except ValueError:
            hours = 0

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO experience
                (category, title, description, start_date, end_date,
                 skills, hours, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    category,
                    title,
                    description,
                    start_date,
                    end_date,
                    skills,
                    hours,
                    created_at,
                ),
            )

        return redirect(url_for("index"))

    return render_template("add.html")


@app.route("/experience/<int:exp_id>")
def experience_detail(exp_id):
    """활동 상세 페이지."""
    conn = get_db()
    try:
        exp = conn.execute(
            "SELECT * FROM experience WHERE id = ?", (exp_id,)
        ).fetchone()
    finally:
        conn.close()

    if not exp:
        abort(404)

    return render_template("experience_detail.html", exp=exp)


@app.route("/analyze")
def analyze():
    """전체 활동 기반 AI 분석."""
    exps = fetch_all_experiences(order_by_recent=False)

    if not exps:
        tips = ["아직 등록된 활동이 없습니다. 최소 3개 이상 입력하면 AI 분석이 더 정확해집니다."]
        return render_template("analyze.html", experiences=exps, tips=tips)

    portfolio_text = build_portfolio_text(exps)

    prompt = f"""
너는 한국 대학생의 포트폴리오를 분석하는 커리어 코치이다.
학생 전공: 컴퓨터공학
목표: 공기업 / IT기업 취업

[활동 목록]
{portfolio_text}

아래 기준대로 분석해줘:

1) 전체 활동을 4~5줄로 핵심 요약
2) 강점 3가지 (bullet)
3) 부족한 점(갭) 3가지 (bullet)
4) 앞으로 6개월 동안 할 만한 구체적인 액션 플랜 3~5개 제안
5) 한국어 + 존댓말
6) 너무 AI같지 않게 자연스럽고 실제 컨설턴트 느낌으로 작성
"""

    try:
        ai_text = call_groq(
            prompt,
            system_msg="너는 한국 대학생을 도와주는 커리어 분석 전문가이다.",
        )
        tips = [ai_text]
    except Exception as e:
        tips = [f"AI 분석 중 오류가 발생했습니다: {e}"]

    return render_template("analyze.html", experiences=exps, tips=tips)


@app.route("/company-analyze", methods=["GET", "POST"])
def company_analyze():
    """목표 회사/직무에 맞춘 맞춤 분석."""
    exps = fetch_all_experiences(order_by_recent=False)
    company_options = COMPANY_OPTIONS

    analysis_text = None
    error_msg = None
    target_company = None
    target_role = None

    if request.method == "POST":
        target_company = request.form.get("company") or ""
        target_role = request.form.get("role") or ""

        if not exps:
            error_msg = "먼저 활동을 1개 이상 등록해주세요."
        elif not target_company:
            error_msg = "분석할 회사를 선택해주세요."
        else:
            portfolio_text = build_portfolio_text(exps)

            prompt = f"""
너는 한국의 취업 컨설턴트이다.
학생 전공: 컴퓨터공학
목표 회사: {target_company}
목표 직무/포지션: {target_role or '미입력'}

[학생의 활동 목록]
{portfolio_text}

위 활동들을 바탕으로
{target_company}의 {target_role or '관련 직무'} 채용을 노린다고 가정하고,

1) 이 회사 / 직무와 연결되는 강점 3~5가지
2) 회사의 인재상 / 직무역량 기준에서 보이는 아쉬운 점 3가지
3) 서류 / 면접에서 어필하기 좋은 스토리 3개 (활동 이름 + 한줄 설명)
4) 앞으로 추가로 쌓으면 좋은 경험 3~5개 (예: OO 공모전, OO 자격증 등 구체적으로)
5) 말투는 자연스러운 존댓말, 실제 컨설턴트처럼 조언해줘.
"""

            try:
                analysis_text = call_groq(
                    prompt,
                    system_msg="너는 한국 공기업·대기업 취업 컨설턴트이다.",
                )
            except Exception as e:
                error_msg = f"AI 분석 중 오류가 발생했습니다: {e}"

    return render_template(
        "company_analyze.html",
        experiences=exps,
        company_options=company_options,
        analysis_text=analysis_text,
        error_msg=error_msg,
        target_company=target_company,
        target_role=target_role,
    )


@app.route("/resume", methods=["GET", "POST"])
def resume():
    """AI 기반 이력서용 경력 요약 생성."""
    exps = fetch_all_experiences(order_by_recent=False)
    company_options = COMPANY_OPTIONS

    resume_text = None
    error_msg = None
    target_company = None
    target_role = None

    if request.method == "POST":
        target_company = request.form.get("company") or ""
        target_role = request.form.get("role") or ""

        if not exps:
            error_msg = "먼저 활동을 1개 이상 등록해주세요."
        else:
            portfolio_text = build_portfolio_text(exps)

            prompt = f"""
너는 한국 대학생의 이력서를 도와주는 커리어 코치이다.
학생 전공: 컴퓨터공학

목표 회사: {target_company or '미지정'}
목표 직무/포지션: {target_role or '미지정'}

[학생의 활동 목록]
{portfolio_text}

위 활동을 기반으로, 한국식 이력서 상단에 넣을 "경력/역량 요약" 문구를 작성해줘.

요구사항:
1) 4~7줄 정도, 문단보다는 문장/불릿 위주
2) 사용 기술/툴, 성과(수상, 순위, 수치 등)가 잘 드러나게
3) {target_company or '지원 회사'}의 {target_role or '관련 직무'}를 겨냥해
   어떤 역량이 맞는지 자연스럽게 녹여줘
4) 말투는 '~했습니다' 체의 존댓말
"""

            try:
                resume_text = call_groq(
                    prompt,
                    system_msg="너는 이력서/자소서를 많이 봐 온 커리어 코치이다.",
                )
            except Exception as e:
                error_msg = f"AI 생성 중 오류가 발생했습니다: {e}"

    return render_template(
        "resume.html",
        experiences=exps,
        resume_text=resume_text,
        error_msg=error_msg,
        company_options=company_options,
        target_company=target_company,
        target_role=target_role,
    )


@app.route("/cover-letter", methods=["GET", "POST"])
def cover_letter():
    """AI 기반 자기소개서(자소서) 초안 생성."""
    exps = fetch_all_experiences(order_by_recent=False)
    company_options = COMPANY_OPTIONS

    letter_text = None
    error_msg = None
    target_company = None
    target_role = None

    if request.method == "POST":
        target_company = request.form.get("company") or ""
        target_role = request.form.get("role") or ""
        extra_request = request.form.get("extra_request", "").strip()

        if not exps:
            error_msg = "먼저 활동을 1개 이상 등록해주세요."
        else:
            portfolio_text = build_portfolio_text(exps)

            prompt = f"""
너는 한국 대학생의 자소서를 도와주는 컨설턴트이다.
학생 전공: 컴퓨터공학

지원 회사: {target_company or '미지정'}
지원 직무: {target_role or '미지정'}

[학생의 활동 목록]
{portfolio_text}

추가 요청 사항:
{extra_request or '특별한 요청 없음'}

위 정보를 바탕으로 한국 공기업/대기업 자기소개서에 들어갈
'지원 동기 + 직무 역량 + 성장 과정'이 섞인 2~3개 문단을 작성해줘.

요구사항:
1) 각 문단은 4~6줄 정도
2) 실제 지원서에 복붙해서 쓸 수 있을 정도의 자연스러운 존댓말
3) 활동 이름과 성과(수상, 순위, 기여도 등)를 적당히 섞어서 설득력 있게
4) 너무 과장된 표현이나 AI 티 나는 문장은 피하기
"""

            try:
                letter_text = call_groq(
                    prompt,
                    system_msg="너는 한국어 자소서 첨삭 전문가이다.",
                )
            except Exception as e:
                error_msg = f"AI 생성 중 오류가 발생했습니다: {e}"

    return render_template(
        "cover_letter.html",
        experiences=exps,
        letter_text=letter_text,
        error_msg=error_msg,
        company_options=company_options,
        target_company=target_company,
        target_role=target_role,
    )


# =========================
# 메인
# =========================

if __name__ == "__main__":
    # 로컬 실행 / Render 둘 다 여기부터 시작
    init_db()
    from os import environ

    app.run(host="0.0.0.0", port=int(environ.get("PORT", 5000)), debug=True)
