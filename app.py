import os
import sqlite3
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, abort
from groq import Groq

# =====================
# 기본 설정
# =====================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "portfolio.db")

app = Flask(__name__)

# Groq 클라이언트 (환경변수에서 키 읽음)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


# =====================
# DB 유틸
# =====================

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """처음에 테이블 없으면 생성."""
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


# 모듈 import 시 한 번 실행 (gunicorn에서도 적용되도록)
init_db()


# =====================
# 헬퍼
# =====================

def fetch_all_experiences(order_desc: bool = True):
    with get_db() as conn:
        if order_desc:
            rows = conn.execute(
                "SELECT * FROM experience ORDER BY start_date DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM experience ORDER BY start_date ASC"
            ).fetchall()
    return rows


def fetch_experience(exp_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM experience WHERE id = ?", (exp_id,)
        ).fetchone()
    return row


# =====================
# 라우트: 대시보드
# =====================

@app.route("/")
def index():
    conn = get_db()
    experiences = conn.execute(
        "SELECT * FROM experience ORDER BY start_date DESC"
    ).fetchall()

    total_hours_row = conn.execute(
        "SELECT SUM(hours) AS total_hours FROM experience"
    ).fetchone()
    total_hours = total_hours_row["total_hours"] or 0

    category_rows = conn.execute(
        "SELECT category, COUNT(*) AS cnt FROM experience GROUP BY category"
    ).fetchall()

    category_labels = [row["category"] for row in category_rows]
    category_counts = [row["cnt"] for row in category_rows]

    recent = experiences[:3]

    return render_template(
        "index.html",
        experiences=experiences,
        total_hours=total_hours,
        category_labels=category_labels,
        category_counts=category_counts,
        recent_experiences=recent,
    )


# =====================
# 라우트: 활동 추가
# =====================

@app.route("/add", methods=["GET", "POST"])
def add():
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


# =====================
# 라우트: 활동 상세 + STAR 분석 버튼
# =====================

@app.route("/experience/<int:exp_id>")
def experience_detail(exp_id):
    exp = fetch_experience(exp_id)
    if not exp:
        abort(404)
    # STAR 분석 결과는 별도 POST에서 처리
    star_analysis = request.args.get("star")  # 쿼리스트링으로 전달 가능하게(선택)
    return render_template("experience_detail.html", exp=exp, star_analysis=star_analysis)


@app.route("/experience/<int:exp_id>/star", methods=["POST"])
def experience_star(exp_id):
    exp = fetch_experience(exp_id)
    if not exp:
        abort(404)

    if client is None:
        star_text = "GROQ_API_KEY 환경변수가 설정되어 있지 않아 AI 분석을 실행할 수 없습니다."
        return render_template("experience_detail.html", exp=exp, star_analysis=star_text)

    prompt = f"""
다음 활동을 STAR 기법으로 정리해 주세요.

[활동]
- 카테고리: {exp['category']}
- 제목: {exp['title']}
- 기간: {exp['start_date']} ~ {exp['end_date'] or ''}
- 설명: {exp['description'] or ''}
- 사용 기술/키워드: {exp['skills'] or ''}

요구사항:
1) S, T, A, R 네 부분으로 나눠서 bullet 형식으로 작성
2) 각 항목은 2~3줄 이내로 간결하게
3) 면접에서 바로 읽을 수 있는 자연스러운 한국어 존댓말
"""

    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "너는 한국 취업 컨설턴트이며 STAR 정리 전문가이다."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        star_text = completion.choices[0].message.content
    except Exception as e:
        star_text = f"AI STAR 분석 중 오류가 발생했습니다: {e}"

    return render_template("experience_detail.html", exp=exp, star_analysis=star_text)


# =====================
# 라우트: 전체 분석
# =====================

@app.route("/analyze")
def analyze():
    conn = get_db()
    exps = conn.execute("SELECT * FROM experience").fetchall()

    if not exps:
        tips = ["아직 등록된 활동이 없습니다. 최소 3개 이상 입력하면 AI 분석이 더 정확해집니다."]
        return render_template("analyze.html", experiences=exps, tips=tips)

    exp_lines = []
    for e in exps:
        line = f"""
        - 카테고리: {e['category']}
          제목: {e['title']}
          기간: {e['start_date']} ~ {e['end_date'] or ''}
          기술/키워드: {e['skills'] or ''}
          설명: {e['description'] or ''}
          투입 시간: {e['hours']}시간
        """
        exp_lines.append(line)

    portfolio_text = "\n".join(exp_lines)

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

    if client is None:
        tips = ["GROQ_API_KEY 환경변수가 설정되어 있지 않아 AI 분석을 실행할 수 없습니다."]
        return render_template("analyze.html", experiences=exps, tips=tips)

    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": "너는 한국 대학생을 도와주는 커리어 분석 전문가이다.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        ai_text = completion.choices[0].message.content
        tips = [ai_text]
    except Exception as e:
        tips = [f"AI 분석 중 오류가 발생했습니다: {e}"]

    return render_template("analyze.html", experiences=exps, tips=tips)


# =====================
# 라우트: 회사 맞춤 분석
# =====================

COMPANY_CHOICES = [
    "LH 한국토지주택공사",
    "한국전력공사",
    "한국도로공사",
    "한국중부발전",
    "한국가스공사",
    "네이버",
    "카카오",
]


@app.route("/company-analyze", methods=["GET", "POST"])
def company_analyze():
    conn = get_db()
    exps = conn.execute("SELECT * FROM experience").fetchall()

    result_text = None
    selected_company = None
    selected_role = None

    if request.method == "POST":
        selected_company = request.form.get("company", "").strip()
        selected_role = request.form.get("role", "").strip()

        if not exps:
            result_text = "먼저 활동을 최소 1개 이상 등록해 주세요."
        elif not selected_company:
            result_text = "분석할 회사를 선택해 주세요."
        elif client is None:
            result_text = "GROQ_API_KEY 환경변수가 설정되어 있지 않아 AI 분석을 실행할 수 없습니다."
        else:
            exp_lines = []
            for e in exps:
                line = f"""
                - [{e['category']}] {e['title']}
                  기간: {e['start_date']} ~ {e['end_date'] or ''}
                  기술: {e['skills'] or ''}
                  설명: {e['description'] or ''}
                """
                exp_lines.append(line)

            portfolio_text = "\n".join(exp_lines)

            prompt = f"""
너는 한국 공기업/IT기업 취업 컨설턴트이다.

지원 회사: {selected_company}
지원 직무(포지션): {selected_role or '미지정'}

[학생의 활동 목록]
{portfolio_text}

요구사항:
1) 이 학생의 경험 중에서 {selected_company} {selected_role or ''} 직무와 연결될만한 포인트를 4~6개 뽑아서 설명
2) 각 포인트마다 "활동 → 직무역량" 구조로 정리
3) 마지막에는 '이 회사 자기소개서/면접에서 어떻게 어필하면 좋을지' 요약 가이드 5줄 이내로 작성
4) 한국어 존댓말, 실제 취업 컨설턴트 말투
"""

            try:
                completion = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {
                            "role": "system",
                            "content": "너는 한국 공기업/IT기업 취업 컨설턴트이다.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.4,
                )
                result_text = completion.choices[0].message.content
            except Exception as e:
                result_text = f"AI 회사 맞춤 분석 중 오류가 발생했습니다: {e}"

    return render_template(
        "company_analyze.html",
        companies=COMPANY_CHOICES,
        result_text=result_text,
        target_company=selected_company,
        target_role=selected_role,
        experiences=exps,
    )


# =====================
# 라우트: 이력서 요약 생성
# =====================

@app.route("/resume", methods=["GET", "POST"])
def resume():
    conn = get_db()
    exps = conn.execute("SELECT * FROM experience").fetchall()

    resume_text = None
    target_company = None
    target_role = None

    if request.method == "POST":
        target_company = request.form.get("company", "").strip()
        target_role = request.form.get("role", "").strip()

        if not exps:
            resume_text = "먼저 활동을 최소 1개 이상 등록해 주세요."
        elif client is None:
            resume_text = "GROQ_API_KEY 환경변수가 설정되어 있지 않아 AI 분석을 실행할 수 없습니다."
        else:
            exp_lines = []
            for e in exps:
                line = f"""
                - [{e['category']}] {e['title']}
                  기간: {e['start_date']} ~ {e['end_date'] or ''}
                  기술: {e['skills'] or ''}
                  설명: {e['description'] or ''}
                  투입 시간: {e['hours']}시간
                """
                exp_lines.append(line)

            portfolio_text = "\n".join(exp_lines)

            prompt = f"""
너는 한국 이력서 작성 컨설턴트이다.

목표 회사: {target_company or '미지정'}
목표 직무: {target_role or '미지정'}

[학생의 활동 목록]
{portfolio_text}

요구사항:
1) 이력서 상단에 넣을 수 있는 '경력 요약 / 역량 요약'을 6~8줄 정도로 작성
2) 문단 하나로 자연스럽게 써 주되, 핵심 역량(예: 데이터 분석, 웹/앱 개발, 프로젝트 리딩 등)을 포함
3) 특정 회사/직무를 입력했다면 그 회사/직무에 맞는 키워드를 살짝 섞어서 작성
4) 한국어 존댓말
"""

            try:
                completion = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {
                            "role": "system",
                            "content": "너는 이력서 요약을 잘 써주는 커리어 컨설턴트이다.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.4,
                )
                resume_text = completion.choices[0].message.content
            except Exception as e:
                resume_text = f"AI 이력서 요약 생성 중 오류가 발생했습니다: {e}"

    return render_template(
        "resume.html",
        experiences=exps,
        resume_text=resume_text,
        companies=COMPANY_CHOICES,
        target_company=target_company,
        target_role=target_role,
    )


# =====================
# 라우트: 자기소개서 생성
# =====================

@app.route("/cover-letter", methods=["GET", "POST"])
def cover_letter():
    conn = get_db()
    exps = conn.execute("SELECT * FROM experience").fetchall()

    cover_letter_text = None
    target_company = None
    target_role = None

    if request.method == "POST":
        target_company = request.form.get("company", "").strip()
        target_role = request.form.get("role", "").strip()

        if not exps:
            cover_letter_text = "먼저 활동을 최소 1개 이상 등록해 주세요."
        elif client is None:
            cover_letter_text = "GROQ_API_KEY 환경변수가 설정되어 있지 않아 AI 분석을 실행할 수 없습니다."
        else:
            exp_lines = []
            for e in exps:
                line = f"""
                - [{e['category']}] {e['title']}
                  기간: {e['start_date']} ~ {e['end_date'] or ''}
                  기술: {e['skills'] or ''}
                  설명: {e['description'] or ''}
                """
                exp_lines.append(line)

            portfolio_text = "\n".join(exp_lines)

            prompt = f"""
너는 한국 공기업/IT기업 자기소개서 첨삭 전문가이다.

지원 회사: {target_company or '미지정'}
지원 직무: {target_role or '미지정'}

[학생의 활동 목록]
{portfolio_text}

요구사항:
1) 700~1000자 분량의 자기소개서 초안을 작성
2) 성장 과정, 강점, 지원동기, 입사 후 포부가 자연스럽게 섞이도록 구성
3) 위 활동들을 적절히 끌어와서 '근거 있는 스토리'로 만들어 줄 것
4) 특정 회사/직무에 맞는 키워드도 조금 포함
5) 한국어 존댓말
"""

            try:
                completion = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {
                            "role": "system",
                            "content": "너는 자기소개서 첨삭 및 작성 전문가이다.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.5,
                )
                cover_letter_text = completion.choices[0].message.content
            except Exception as e:
                cover_letter_text = f"AI 자기소개서 생성 중 오류가 발생했습니다: {e}"

    return render_template(
        "cover_letter.html",
        experiences=exps,
        cover_letter_text=cover_letter_text,
        companies=COMPANY_CHOICES,
        target_company=target_company,
        target_role=target_role,
    )


# =====================
# 엔트리 포인트
# =====================

if __name__ == "__main__":
    # 로컬 개발용
    app.run(debug=True)
