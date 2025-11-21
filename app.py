import os
import sqlite3
from datetime import datetime

import requests
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    abort,
)

DB_NAME = "portfolio.db"

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

app = Flask(__name__)


# =========================
# DB 관련 함수
# =========================
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
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


# =========================
# 공통: Groq 호출 함수
# =========================
def call_groq(prompt: str, system: str = "", temperature: float = 0.4) -> str:
    """Groq HTTP API 호출 (SDK 안 쓰고 requests로 직접 호출)."""
    if not GROQ_API_KEY:
        # 키 없으면 바로 안내
        return "GROQ_API_KEY 환경 변수가 설정되어 있지 않습니다. 관리자에게 문의해 주세요."

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": messages,
        "temperature": temperature,
    }

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    resp = requests.post(GROQ_API_URL, json=payload, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


# =========================
# 라우팅
# =========================
@app.route("/")
def index():
    conn = get_db()

    experiences = conn.execute(
        "SELECT * FROM experience ORDER BY start_date DESC, id DESC"
    ).fetchall()

    total_hours_row = conn.execute(
        "SELECT IFNULL(SUM(hours), 0) AS total_hours FROM experience"
    ).fetchone()
    total_hours = total_hours_row["total_hours"]

    categories = conn.execute(
        """
        SELECT category, COUNT(*) AS cnt
        FROM experience
        GROUP BY category
        ORDER BY cnt DESC
        """
    ).fetchall()

    # 최근 활동 5개
    recent = conn.execute(
        """
        SELECT * FROM experience
        ORDER BY start_date DESC, id DESC
        LIMIT 5
        """
    ).fetchall()

    return render_template(
        "index.html",
        experiences=experiences,
        total_hours=total_hours,
        categories=categories,
        recent=recent,
    )


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


@app.route("/experience/<int:exp_id>")
def experience_detail(exp_id):
    conn = get_db()
    exp = conn.execute(
        "SELECT * FROM experience WHERE id = ?", (exp_id,)
    ).fetchone()
    if not exp:
        abort(404)

    return render_template("experience_detail.html", exp=exp)


@app.route("/analyze")
def analyze():
    conn = get_db()
    exps = conn.execute("SELECT * FROM experience").fetchall()

    # 활동 없으면 안내만
    if not exps:
        tips = ["아직 등록된 활동이 없습니다. 최소 3개 이상 입력하면 AI 분석이 더 정확해집니다."]
        return render_template("analyze.html", experiences=exps, tips=tips)

    # 프롬프트용 텍스트 만들기
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

    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "너는 한국 대학생을 도와주는 커리어 분석 전문가이다."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        ai_text = completion.choices[0].message.content
        tips = [ai_text]

    except Exception as e:
        tips = [f"AI 분석 중 오류가 발생했습니다: {e}"]

    # 공통 리턴 한 번만
    return render_template("analyze.html", experiences=exps, tips=tips)


@app.route("/company_analyze", methods=["GET", "POST"])
def company_analyze():
    conn = get_db()
    exps = conn.execute("SELECT * FROM experience").fetchall()
    analysis_text = None
    target_company = None
    target_role = None

    if request.method == "POST":
        target_company = request.form.get("company") or ""
        target_role = request.form.get("role") or ""

        if not exps:
            analysis_text = "등록된 활동이 없어 회사 맞춤 분석을 할 수 없습니다. 먼저 활동을 추가해 주세요."
        else:
            exp_lines = []
            for e in exps:
                line = f"""
                - [{e['category']}] {e['title']}
                  기간: {e['start_date']} ~ {e['end_date'] or ''}
                  기술/키워드: {e['skills'] or ''}
                  설명: {e['description'] or ''}
                  투입 시간: {e['hours']}시간
                """
                exp_lines.append(line)

            portfolio_text = "\n".join(exp_lines)

            prompt = f"""
다음은 한 대학생의 활동 목록입니다.

[학생 정보]
- 전공: 컴퓨터공학
- 관심: 공기업 및 IT 기업 취업

[목표 회사/직무]
- 회사: {target_company}
- 직무: {target_role}

[활동 목록]
{portfolio_text}

아래 기준에 맞춰 회사 맞춤 분석을 해줘:

1) {target_company} / {target_role} 기준으로 이 학생이 가진 강점을 3~5개 bullet으로 정리
2) 해당 회사/직무에서 보완해야 할 점을 3~5개 bullet으로 정리
3) 자기소개서나 면접에서 어필하기 좋은 에피소드 후보를 3개 정도 뽑아서,
   각 에피소드마다 한 줄 요약 + 어떤 질문에 쓰면 좋을지 함께 적기
4) 전반적인 총평 (3~4문장)
5) 한국어 + 존댓말, 너무 포멀하지 않고 실제 취업 컨설턴트 느낌으로 작성
"""
            try:
                analysis_text = call_groq(
                    prompt,
                    system="너는 한국 공기업/IT기업 취업을 도와주는 커리어 컨설턴트이다.",
                    temperature=0.4,
                )
            except Exception as e:
                print("[COMPANY_ANALYZE ERROR]", e, flush=True)
                analysis_text = (
                    "AI 분석 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
                )

    return render_template(
        "company_analyze.html",
        experiences=exps,
        analysis_text=analysis_text,
        target_company=target_company,
        target_role=target_role,
    )


@app.route("/resume", methods=["GET", "POST"])
def resume():
    conn = get_db()
    exps = conn.execute("SELECT * FROM experience").fetchall()

    resume_text = None
    target_company = None
    target_role = None

    # 회사 선택용 기본 목록 (select 옵션)
    company_options = ["LH", "한국전력공사", "한국가스공사", "네이버", "카카오", "삼성전자", "기타"]

    if request.method == "POST":
        target_company = request.form.get("company") or ""
        target_role = request.form.get("role") or ""

        if not exps:
            resume_text = "등록된 활동이 없어 이력서용 요약을 생성할 수 없습니다. 먼저 활동을 추가해 주세요."
        else:
            exp_lines = []
            for e in exps:
                line = f"""
                - [{e['category']}] {e['title']}
                  기간: {e['start_date']} ~ {e['end_date'] or ''}
                  기술/키워드: {e['skills'] or ''}
                  설명: {e['description'] or ''}
                  투입 시간: {e['hours']}시간
                """
                exp_lines.append(line)

            portfolio_text = "\n".join(exp_lines)

            prompt = f"""
아래는 한 대학생의 활동 목록이다.

[학생 정보]
- 전공: 컴퓨터공학
- 목표 회사: {target_company}
- 목표 직무: {target_role}

[활동 목록]
{portfolio_text}

이 정보를 바탕으로,

1) 이력서 상단에 넣을 수 있는 "경력 및 역량 요약" 섹션을 작성해줘.
2) 5~7줄 정도의 문단 형태로 작성 (bullet이 아니라 문단).
3) {target_company} / {target_role}에 적합한 키워드를 자연스럽게 녹여줘.
4) 한국어 + 존댓말, 너무 AI티 나지 않고 실제 취업 준비생이 쓸 법한 자연스러운 표현.
"""

            try:
                resume_text = call_groq(
                    prompt,
                    system="너는 이력서 요약 섹션을 잘 써주는 커리어 코치이다.",
                    temperature=0.35,
                )
            except Exception as e:
                print("[RESUME ERROR]", e, flush=True)
                resume_text = (
                    "AI 호출 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
                )

    return render_template(
        "resume.html",
        experiences=exps,
        resume_text=resume_text,
        target_company=target_company,
        target_role=target_role,
        company_options=company_options,
    )


@app.route("/cover_letter", methods=["GET", "POST"])
def cover_letter():
    conn = get_db()
    exps = conn.execute("SELECT * FROM experience").fetchall()

    cl_text = None
    target_company = None
    target_role = None
    question_type = None

    company_options = ["LH", "한국전력공사", "한국가스공사", "네이버", "카카오", "삼성전자", "기타"]
    question_types = {
        "motivation": "지원동기/입사 후 포부",
        "strength": "직무 역량/강점",
        "failure": "실패 경험 및 극복",
        "team": "협업/갈등 해결 경험",
    }

    if request.method == "POST":
        target_company = request.form.get("company") or ""
        target_role = request.form.get("role") or ""
        question_type = request.form.get("question_type") or "motivation"

        if not exps:
            cl_text = "등록된 활동이 없어 자기소개서를 생성할 수 없습니다. 먼저 활동을 추가해 주세요."
        else:
            exp_lines = []
            for e in exps:
                line = f"""
                - [{e['category']}] {e['title']}
                  기간: {e['start_date']} ~ {e['end_date'] or ''}
                  기술/키워드: {e['skills'] or ''}
                  설명: {e['description'] or ''}
                  투입 시간: {e['hours']}시간
                """
                exp_lines.append(line)

            portfolio_text = "\n".join(exp_lines)

            if question_type == "motivation":
                guide = "지원동기와 입사 후 포부 중심으로 800~1000자 정도로 작성해줘."
            elif question_type == "strength":
                guide = "직무 역량과 강점을 보여줄 수 있는 경험을 중심으로 800~1000자 정도로 작성해줘."
            elif question_type == "failure":
                guide = "실패 경험과 그 경험을 통해 배운 점, 이후의 변화에 초점을 맞춰 800~1000자 정도로 작성해줘."
            else:  # team
                guide = "팀 프로젝트/협업 경험을 기반으로, 갈등 상황이나 어려움을 어떻게 해결했는지 중심으로 800~1000자 정도로 작성해줘."

            prompt = f"""
아래는 한 대학생의 활동 목록이다.

[학생 정보]
- 전공: 컴퓨터공학
- 목표 회사: {target_company}
- 목표 직무: {target_role}

[활동 목록]
{portfolio_text}

위 활동들을 바탕으로 {target_company} {target_role} 자기소개서 문항 중
"{question_types.get(question_type)}" 유형에 어울리는 답변을 작성해줘.

요청 조건:
- {guide}
- 문단 형식으로 작성 (bullet 금지)
- 너무 격식만 가득하지 말고, 자연스럽지만 신뢰감 있는 톤
- 회사/직무와의 연결성이 드러나게 작성
"""

            try:
                cl_text = call_groq(
                    prompt,
                    system="너는 한국 취업 자기소개서를 잘 써주는 컨설턴트이다.",
                    temperature=0.45,
                )
            except Exception as e:
                print("[COVER_LETTER ERROR]", e, flush=True)
                cl_text = (
                    "AI 호출 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
                )

    return render_template(
        "cover_letter.html",
        experiences=exps,
        cover_letter_text=cl_text,
        target_company=target_company,
        target_role=target_role,
        company_options=company_options,
        question_types=question_types,
        selected_question_type=question_type,
    )


if __name__ == "__main__":
    init_db()
    from os import environ

    app.run(host="0.0.0.0", port=int(environ.get("PORT", 5000)))
