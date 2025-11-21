import os
import sqlite3
from datetime import datetime

from groq import Groq
from flask import Flask, render_template, request, redirect, url_for

# =========================
# 설정
# =========================
DB_NAME = "portfolio.db"
COMPANY_OPTIONS = [
    "LH (한국토지주택공사)",
    "한국전력공사",
    "한국중부발전",
    "한국도로공사",
    "네이버",
    "카카오",
    "삼성전자",
    "게임회사 (넥슨/크래프톤 등)",
    "공기업 전반",
    "IT 기업 전반",
]

app = Flask(__name__)

# GROQ_API_KEY 는 환경변수에 setx 로 미리 설정해둔 상태여야 함
client = Groq(api_key=os.getenv("GROQ_API_KEY"))


# =========================
# DB 유틸
# =========================
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row  # dict처럼 접근 가능
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
# 메인 대시보드
# =========================
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

    categories = conn.execute(
        "SELECT category, COUNT(*) AS cnt FROM experience GROUP BY category"
    ).fetchall()

    return render_template(
        "index.html",
        experiences=experiences,
        total_hours=total_hours,
        categories=categories,
    )


# =========================
# 활동 추가
# =========================
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


# =========================
# 전체 포트폴리오 AI 분석
# =========================
@app.route("/analyze")
def analyze():
    conn = get_db()
    exps = conn.execute("SELECT * FROM experience").fetchall()

    # 활동 없으면 AI 안 부르고 안내 메시지
    if not exps:
        tips = ["아직 등록된 활동이 없습니다. 최소 3개 이상 입력하면 AI 분석이 더 정확해집니다."]
        return render_template("analyze.html", experiences=exps, tips=tips)

    # 경험을 프롬프트용 텍스트로 정리
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

# =========================
# 회사 맞춤 분석
# =========================
@app.route("/analyze/company", methods=["GET", "POST"])
def company_analyze():
    conn = get_db()
    exps = conn.execute("SELECT * FROM experience").fetchall()

    if not exps:
        # 활동 없으면 안내만
        msg = "아직 등록된 활동이 없습니다. 활동을 몇 개 입력한 뒤 회사 맞춤 분석을 이용해 주세요."
        return render_template(
            "company_analyze.html",
            experiences=exps,
            company_options=COMPANY_OPTIONS,
            selected_company=None,
            selected_role="",
            analysis_text=msg,
        )

    selected_company = None
    selected_role = ""
    analysis_text = None

    if request.method == "POST":
        selected_company = request.form.get("company") or None
        selected_role = request.form.get("role", "").strip()

        if not selected_company:
            analysis_text = "회사를 선택해 주세요."
        else:
            # 경험 텍스트 정리
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

            role_text = selected_role if selected_role else "관련 직무(구체 미입력)"

            prompt = f"""
너는 한국 대학생의 포트폴리오를 분석하는 커리어 코치이다.
학생 전공: 컴퓨터공학

목표 회사: {selected_company}
목표 직무: {role_text}

[학생의 활동 목록]
{portfolio_text}

아래 기준으로 {selected_company} 입사 관점에서 분석해줘:

1) 이 학생이 {selected_company} {role_text} 지원자라고 가정했을 때,
   돋보이는 강점 3가지를 bullet 형식으로 써라.

2) {selected_company} 기준에서 아쉬운 점 / 리스크 3가지를 bullet 형식으로 써라.

3) 앞으로 1~3개월 안에 하면 좋은 '단기 액션 플랜' 3가지를
   매우 구체적으로 제안해라. (예: 어떤 자격증, 어떤 프로젝트, 어떤 경험)

4) 6~12개월 안에 준비하면 좋은 '중·장기 액션 플랜' 3가지를 제안해라.

5) 마지막에, {selected_company} 1차 자기소개서나 면접에서
   바로 쓸 수 있는 한 문장 어필 포인트 2개를 만들어라.
   (예: "저는 OO 경험을 통해 △△ 역량을 검증받았습니다." 이런 느낌)

형식: 한국어, 존댓말, 실제 취업 컨설턴트가 말해주는 톤으로 작성.
"""

            try:
                completion = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {
                            "role": "system",
                            "content": "너는 한국 공기업/IT기업 취업을 도와주는 전문 커리어 코치이다.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.4,
                )
                analysis_text = completion.choices[0].message.content
            except Exception as e:
                analysis_text = f"회사 맞춤 분석 중 오류가 발생했습니다: {e}"

    # GET 이거나, POST 후 렌더링
    return render_template(
        "company_analyze.html",
        experiences=exps,
        company_options=COMPANY_OPTIONS,
        selected_company=selected_company,
        selected_role=selected_role,
        analysis_text=analysis_text,
    )
# =========================
# 자동 이력서 생성
# =========================
@app.route("/resume", methods=["GET", "POST"])
def generate_resume():
    conn = get_db()
    exps = conn.execute("SELECT * FROM experience ORDER BY start_date DESC").fetchall()

    if not exps:
        msg = "아직 등록된 활동이 없습니다. 활동을 몇 개 입력한 뒤 이력서 생성을 이용해 주세요."
        return render_template(
            "resume.html",
            experiences=exps,
            target_company="",
            target_role="",
            resume_text=msg,
            COMPANY_OPTIONS=COMPANY_OPTIONS,
        )

    target_company = ""
    target_role = ""
    resume_text = None

    if request.method == "POST":
        target_company = request.form.get("company", "").strip()
        target_role = request.form.get("role", "").strip()

        company_text = target_company if target_company else "특정 회사 미지정"
        role_text = target_role if target_role else "전산/IT 관련 직무"

        # 활동들 텍스트 정리
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
너는 한국 대학생의 이력서를 정리해주는 커리어 코치이다.

학생 전공: 컴퓨터공학
목표 회사: {company_text}
목표 직무: {role_text}

아래는 학생의 활동 목록이다.

[활동 목록]
{portfolio_text}

이 정보를 바탕으로, 한국식 이력서의 '경험 및 역량' 파트를 만드는 느낌으로 아래 형식대로 작성해줘.

1. 한 줄 요약 프로필 (예: "공공데이터 분석과 웹 개발 프로젝트 경험을 갖춘 컴퓨터공학 전공자입니다.")
2. 핵심 역량 3~5개 (bullet, 예: Python, Flask, 리눅스, 데이터 분석, 공모전 경험 등)
3. 주요 경험 정리 (각 경험마다)
   - 경험명: (활동 제목)
   - 기간:
   - 역할 / 수행 내용: (구체적으로 3~4줄)
   - 성과 / 결과: (수치, 결과 중심으로 2~3줄)

전체 분량은 A4 1장 이력서의 '경험 및 역량' 섹션 정도로 맞추고,
실제 이력서에 바로 붙여넣을 수 있을 정도로 자연스럽게 한국어 존댓말로 작성해라.
"""

        try:
            completion = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {
                        "role": "system",
                        "content": "너는 한국식 이력서 작성에 익숙한 커리어 코치이다.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
            )
            resume_text = completion.choices[0].message.content
        except Exception as e:
            resume_text = f"이력서 생성 중 오류가 발생했습니다: {e}"

    return render_template(
        "resume.html",
        experiences=exps,
        target_company=target_company,
        target_role=target_role,
        resume_text=resume_text,
        company_options=COMPANY_OPTIONS,
    )
# =========================
# 자동 자기소개서 생성
# =========================
@app.route("/cover-letter", methods=["GET", "POST"])
def generate_cover_letter():
    conn = get_db()
    exps = conn.execute("SELECT * FROM experience ORDER BY start_date DESC").fetchall()

    if not exps:
        msg = "아직 등록된 활동이 없습니다. 활동을 몇 개 입력한 뒤 자기소개서 생성을 이용해 주세요."
        return render_template(
            "cover_letter.html",
            experiences=exps,
            target_company="",
            target_role="",
            cover_text=msg,
            company_options=COMPANY_OPTIONS,
        )

    target_company = ""
    target_role = ""
    cover_text = None

    if request.method == "POST":
        target_company = request.form.get("company", "").strip()
        target_role = request.form.get("role", "").strip()

        company_text = target_company if target_company else "특정 회사 미지정"
        role_text = target_role if target_role else "전산/IT 관련 직무"

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
너는 한국 대학생의 자기소개서를 작성해주는 커리어 코치이다.

학생 전공: 컴퓨터공학
목표 회사: {company_text}
목표 직무: {role_text}

아래는 학생의 활동 목록이다.

[활동 목록]
{portfolio_text}

아래 구조로 자기소개서 초안을 작성해줘:

1) 성장 과정 및 성격 (5~7줄)
2) 전공/학업 과정에서의 강점 (5~7줄)
3) 프로젝트/대외활동/공모전 등에서의 경험 (5~7줄)
4) 지원 동기 및 입사 후 포부 (5~7줄)

각 문단은 '제목 (한 줄 요약)' + 그 아래에 본문 형식으로 작성해라.
예: 
[성장 과정] ~~~
이런 형식.

전체 분량은 A4 한 장 내에서 한국 공기업/IT 기업 자기소개서 느낌으로,
부담스럽지 않으면서도 진짜 사람이 쓴 것 같은 자연스러운 존댓말로 작성해라.
"""

        try:
            completion = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {
                        "role": "system",
                        "content": "너는 한국 공기업/IT 기업 자기소개서 작성에 익숙한 커리어 코치이다.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
            )
            cover_text = completion.choices[0].message.content
        except Exception as e:
            cover_text = f"자기소개서 생성 중 오류가 발생했습니다: {e}"

    return render_template(
        "cover_letter.html",
        experiences=exps,
        target_company=target_company,
        target_role=target_role,
        cover_text=cover_text,
        company_options = COMPANY_OPTIONS,
    )

# =========================
# 활동 상세 페이지
# =========================
@app.route("/experience/<int:exp_id>")
def experience_detail(exp_id):
    """활동 상세 페이지"""
    conn = get_db()
    exp = conn.execute(
        "SELECT * FROM experience WHERE id = ?",
        (exp_id,),
    ).fetchone()

    if exp is None:
        return "해당 활동을 찾을 수 없습니다.", 404

    return render_template("experience_detail.html", exp=exp, star_analysis=None)


# =========================
# 활동 별 AI STAR 분석
# =========================
@app.route("/experience/<int:exp_id>/star", methods=["POST"])
def experience_star(exp_id):
    """해당 활동을 AI로 STAR 형식 분석"""
    conn = get_db()
    exp = conn.execute(
        "SELECT * FROM experience WHERE id = ?",
        (exp_id,),
    ).fetchone()

    if exp is None:
        return "해당 활동을 찾을 수 없습니다.", 404

    title = exp["title"]
    category = exp["category"]
    desc = exp["description"] or ""
    skills = exp["skills"] or ""
    hours = exp["hours"] or 0
    period = f"{exp['start_date']} ~ {exp['end_date'] or ''}"

    prompt = f"""
너는 한국 대학생의 경험을 STAR 기법으로 정리해주는 커리어 코치다.

아래 활동을 STAR 형식(Situation, Task, Action, Result)으로 정리해줘.
각 항목은 3~5줄 정도, 실제 자소서에 그대로 쓸 수 있을 정도로 자연스럽게 써라.
마지막에는 면접에서 이 경험을 어떻게 말하면 좋을지 한 줄 팁도 적어줘.

[활동 정보]
- 제목: {title}
- 카테고리: {category}
- 기간: {period}
- 설명: {desc}
- 사용 기술/키워드: {skills}
- 투입 시간: {hours}시간
"""

    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": "너는 STAR 분석을 전문적으로 수행하는 커리어 코치다.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        star_text = completion.choices[0].message.content

    except Exception as e:
        star_text = f"AI STAR 분석 중 오류가 발생했습니다: {e}"

    return render_template("experience_detail.html", exp=exp, star_analysis=star_text)
@app.route("/delete/<int:exp_id>", methods=["POST"])
def delete_exp(exp_id):
    with get_db() as conn:
        conn.execute("DELETE FROM experience WHERE id = ?", (exp_id,))
    return redirect(url_for("index"))


# =========================
# 엔트리 포인트
# =========================
if __name__ == "__main__":
    init_db()
    from os import environ
    app.run(host="0.0.0.0", port=int(environ.get("PORT", 5000)))
