import os
import sqlite3
from datetime import datetime
import markdown # 마크다운 변환 라이브러리 추가
pi_key = os.getenv("GROQ_API_KEY")
print(f"👉 현재 로드된 키 상태: {api_key[:5] if api_key else 'None'} (길이: {len(api_key) if api_key else 0})")
# ▲▲▲
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

# Groq 클라이언트
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

COMPANY_OPTIONS = [
    "LH", "한국전력공사", "한국중부발전", "한국도로공사",
    "한국수력원자력", "네이버", "카카오", "삼성전자", "SK텔레콤"
]

# =========================
# DB 유틸
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

def fetch_all_experiences(order_by_recent=True):
    conn = get_db()
    try:
        sql = "SELECT * FROM experience"
        if order_by_recent:
            sql += " ORDER BY start_date DESC"
        rows = conn.execute(sql).fetchall()
        return rows
    finally:
        conn.close()

def build_portfolio_text(exps):
    lines = []
    for e in exps:
        # DB 스키마 변경 없이 날짜로 상태 추정
        status = "진행 중"
        if e['end_date'] and e['end_date'] < datetime.now().strftime("%Y-%m-%d"):
            status = "완료"
            
        line = f"""
- [{status}] {e['category']} | {e['title']}
  기간: {e['start_date']} ~ {e['end_date'] or '현재'}
  기술: {e['skills'] or ''}
  내용: {e['description'] or ''}
  시간: {e['hours']}시간
"""
        lines.append(line)
    return "\n".join(lines)

# =========================
# Groq AI 유틸 (Markdown 적용)
# =========================

def call_groq(prompt: str, system_msg: str) -> str:
    if not client.api_key:
        return "Error: GROQ_API_KEY가 설정되지 않았습니다."

    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile", # 더 똑똑한 모델로 변경
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
        )
        raw_text = completion.choices[0].message.content
        # Markdown을 HTML로 변환 (확장기능: 테이블, 코드블럭 등)
        html_text = markdown.markdown(raw_text, extensions=['extra', 'nl2br'])
        return html_text
    except Exception as e:
        return f"<p style='color:red;'>AI 호출 중 오류 발생: {str(e)}</p>"

# =========================
# 라우트
# =========================

@app.route("/")
def index():
    conn = get_db()
    try:
        exps = conn.execute("SELECT * FROM experience ORDER BY start_date DESC").fetchall()
        
        # 통계 계산
        total_count = len(exps)
        total_hours_row = conn.execute("SELECT SUM(hours) AS total_hours FROM experience").fetchone()
        total_hours = total_hours_row["total_hours"] or 0
        
        categories = conn.execute("SELECT category, COUNT(*) AS cnt FROM experience GROUP BY category").fetchall()
    finally:
        conn.close()

    # 활동 데이터에 'status' 속성 동적 추가 (DB 마이그레이션 없이)
    processed_exps = []
    today = datetime.now().strftime("%Y-%m-%d")
    
    for row in exps:
        exp = dict(row) # 딕셔너리로 변환
        if exp['end_date'] and exp['end_date'] < today:
            exp['status'] = 'completed' # 완료
            exp['status_label'] = '완료'
            exp['status_color'] = 'success' # 초록
        else:
            exp['status'] = 'ongoing'   # 진행중
            exp['status_label'] = '진행 중'
            exp['status_color'] = 'warning' # 노랑/주황
        processed_exps.append(exp)

    return render_template(
        "index.html",
        experiences=processed_exps,
        total_count=total_count,
        total_hours=total_hours,
        categories=categories,
    )

@app.route("/add", methods=["GET", "POST"])
def add():
    if request.method == "POST":
        # (기존 코드와 동일)
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
                "INSERT INTO experience (category, title, description, start_date, end_date, skills, hours, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (category, title, description, start_date, end_date, skills, hours, created_at)
            )
        return redirect(url_for("index"))
    return render_template("add.html")

@app.route("/experience/<int:exp_id>")
def experience_detail(exp_id):
    conn = get_db()
    try:
        exp = conn.execute("SELECT * FROM experience WHERE id = ?", (exp_id,)).fetchone()
    finally:
        conn.close()
    if not exp:
        abort(404)
    return render_template("experience_detail.html", exp=exp)

@app.route("/analyze")
def analyze():
    exps = fetch_all_experiences(order_by_recent=False)
    if not exps:
        return render_template("analyze.html", experiences=[], ai_result="<p>활동을 먼저 등록해주세요.</p>")

    portfolio_text = build_portfolio_text(exps)
    
    prompt = f"""
    [학생 정보] 전공: 컴퓨터공학 / 희망: IT기업, 공기업 개발직군
    [활동 목록]
    {portfolio_text}

    위 활동을 바탕으로 다음 내용을 마크다운(Markdown) 형식으로 분석해줘:
    1. **핵심 요약** (3줄 이내)
    2. **발견된 강점 3가지** (불릿 포인트)
    3. **보완이 필요한 점** (불릿 포인트)
    4. **추천 액션 아이템** (구체적으로)
    """
    
    ai_result = call_groq(prompt, "너는 날카로운 IT 커리어 코치다.")
    return render_template("analyze.html", experiences=exps, ai_result=ai_result)

@app.route("/company-analyze", methods=["GET", "POST"])
def company_analyze():
    exps = fetch_all_experiences()
    ai_result = None
    target_company = None
    target_role = None

    if request.method == "POST":
        target_company = request.form.get("company")
        target_role = request.form.get("role")
        portfolio_text = build_portfolio_text(exps)
        
        prompt = f"""
        목표 회사: {target_company}
        목표 직무: {target_role}
        [내 활동]
        {portfolio_text}

        {target_company}의 {target_role} 채용 담당자 관점에서 마크다운 형식으로 분석해:
        1. **직무 적합도 점수** (100점 만점 중 몇 점인지)
        2. **합격 가능성을 높일 핵심 강점**
        3. **예상 면접 질문 3가지**
        """
        ai_result = call_groq(prompt, "너는 대기업 채용 담당자다.")

    return render_template("company_analyze.html", company_options=COMPANY_OPTIONS, ai_result=ai_result, target_company=target_company)

# resume, cover_letter 라우트도 위와 동일한 패턴으로 call_groq 사용
# (길이 관계상 생략하지만, 위 call_groq 함수가 HTML을 반환하므로 템플릿에서 {{ result | safe }} 만 쓰면 됨)

@app.route("/resume", methods=["GET", "POST"])
def resume():
    exps = fetch_all_experiences(order_by_recent=False)
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
            [학생 정보] 전공: 컴퓨터공학
            목표 회사: {target_company or '미지정'}
            목표 직무: {target_role or '미지정'}
            [활동 목록]
            {portfolio_text}

            위 활동을 기반으로 이력서 상단 '핵심 역량 요약'을 마크다운으로 작성해줘.
            1. **강조할 핵심 역량 3가지**
            2. **주요 성과 요약** (수치 위주)
            3. **{target_company} 맞춤 한 줄 어필**
            """
            resume_text = call_groq(prompt, "너는 이력서 컨설턴트다.")

    return render_template(
        "resume.html",
        experiences=exps,
        resume_text=resume_text,
        error_msg=error_msg,
        company_options=COMPANY_OPTIONS,
        target_company=target_company,
        target_role=target_role,
    )

@app.route("/cover-letter", methods=["GET", "POST"])
def cover_letter():
    exps = fetch_all_experiences(order_by_recent=False)
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
            지원 회사: {target_company}
            지원 직무: {target_role}
            추가 요청: {extra_request}
            [활동 목록]
            {portfolio_text}

            위 내용을 바탕으로 자기소개서 초안(지원동기+직무역량)을 마크다운으로 작성해줘.
            - 문단 나누기 필수
            - 구체적인 경험을 근거로 들 것
            """
            letter_text = call_groq(prompt, "너는 자소서 전문 작가다.")

    return render_template(
        "cover_letter.html",
        experiences=exps,
        letter_text=letter_text,
        error_msg=error_msg,
        company_options=COMPANY_OPTIONS,
        target_company=target_company,
        target_role=target_role,
    )


if __name__ == "__main__":
    init_db()
    from os import environ
    app.run(host="0.0.0.0", port=int(environ.get("PORT", 5000)), debug=True)