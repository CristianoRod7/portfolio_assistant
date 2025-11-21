import os
import csv
import io
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import markdown

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    abort,
    Response,
    flash
)
from groq import Groq

# =========================
# 기본 설정
# =========================

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "super_secret_key")  # 플래시 메시지 사용 시 필요

# Groq 클라이언트
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

COMPANY_OPTIONS = [
    "LH", "한국전력공사", "한국중부발전", "한국도로공사",
    "한국수력원자력", "네이버", "카카오", "삼성전자", "SK텔레콤"
]

# =========================
# DB 유틸 (PostgreSQL 버전)
# =========================

def get_db_connection():
    """PostgreSQL DB 연결 함수"""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL 환경변수가 설정되지 않았습니다!")
    
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    return conn

def init_db():
    """테이블 생성 (서버 시작 시 실행)"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # PostgreSQL용 테이블 생성 쿼리 (SERIAL = 자동증가 ID)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS experience (
            id SERIAL PRIMARY KEY,
            category VARCHAR(100) NOT NULL,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            start_date VARCHAR(20),
            end_date VARCHAR(20),
            skills TEXT,
            hours INTEGER DEFAULT 0,
            created_at VARCHAR(50)
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ PostgreSQL initialized")

def fetch_all_experiences(order_by_recent=True):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        sql = "SELECT * FROM experience"
        if order_by_recent:
            sql += " ORDER BY start_date DESC"
        cur.execute(sql)
        rows = cur.fetchall()
        return rows
    finally:
        cur.close()
        conn.close()

def build_portfolio_text(exps):
    lines = []
    today = datetime.now().strftime("%Y-%m-%d")
    for e in exps:
        # 날짜 비교 로직
        status = "진행 중"
        if e['end_date'] and e['end_date'] < today:
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
# Groq AI 유틸
# =========================

def call_groq(prompt: str, system_msg: str) -> str:
    if not client.api_key:
        return "Error: GROQ_API_KEY가 설정되지 않았습니다."

    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
        )
        raw_text = completion.choices[0].message.content
        html_text = markdown.markdown(raw_text, extensions=['extra', 'nl2br'])
        return html_text
    except Exception as e:
        return f"<p style='color:red;'>AI 호출 중 오류 발생: {str(e)}</p>"

# =========================
# 라우트
# =========================

@app.route("/")
def index():
    # DB 연결 및 조회
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM experience ORDER BY start_date DESC")
        exps = cur.fetchall()
        
        # 통계 (Postgres는 COUNT, SUM 문법 동일)
        cur.execute("SELECT SUM(hours) as total_hours FROM experience")
        row = cur.fetchone()
        total_hours = row['total_hours'] if row and row['total_hours'] else 0
        
        cur.execute("SELECT category, COUNT(*) as cnt FROM experience GROUP BY category")
        categories = cur.fetchall()
        
        cur.close()
        conn.close()
    except Exception as e:
        # DB 연결 실패 시 에러 페이지 대신 빈 데이터 보여주기 (혹은 디버깅용)
        print(f"DB Error: {e}")
        exps, total_hours, categories = [], 0, []
        total_count = 0

    total_count = len(exps)
    
    # 상태값(Status) 처리
    processed_exps = []
    today = datetime.now().strftime("%Y-%m-%d")
    
    for row in exps:
        exp = dict(row)
        if exp['end_date'] and exp['end_date'] < today:
            exp['status'] = 'completed'
            exp['status_label'] = '완료'
            exp['status_color'] = 'success'
        else:
            exp['status'] = 'ongoing'
            exp['status_label'] = '진행 중'
            exp['status_color'] = 'warning'
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

        conn = get_db_connection()
        cur = conn.cursor()
        # Postgres는 %s 플레이스홀더 사용
        cur.execute(
            """
            INSERT INTO experience (category, title, description, start_date, end_date, skills, hours, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (category, title, description, start_date, end_date, skills, hours, created_at)
        )
        conn.commit()
        cur.close()
        conn.close()

        return redirect(url_for("index"))
    return render_template("add.html")

@app.route("/edit/<int:exp_id>", methods=["GET", "POST"])
def edit(exp_id):
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == "POST":
        category = request.form["category"]
        title = request.form["title"]
        description = request.form["description"]
        start_date = request.form["start_date"]
        end_date = request.form["end_date"] or None
        hours = request.form["hours"]
        skills = request.form["skills"]
        
        cur.execute(
            """
            UPDATE experience 
            SET category=%s, title=%s, description=%s, start_date=%s, end_date=%s, hours=%s, skills=%s
            WHERE id=%s
            """,
            (category, title, description, start_date, end_date, hours, skills, exp_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('experience_detail', exp_id=exp_id))
    
    cur.execute("SELECT * FROM experience WHERE id = %s", (exp_id,))
    exp = cur.fetchone()
    cur.close()
    conn.close()

    if not exp:
        abort(404)

    return render_template("add.html", exp=exp, is_edit=True)

@app.route("/delete/<int:exp_id>")
def delete(exp_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM experience WHERE id = %s", (exp_id,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('index'))

@app.route("/experience/<int:exp_id>")
def experience_detail(exp_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM experience WHERE id = %s", (exp_id,))
    exp = cur.fetchone()
    cur.close()
    conn.close()
    if not exp:
        abort(404)
    return render_template("experience_detail.html", exp=exp)

# =========================
# AI 분석 기능들
# =========================

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

    return render_template("company_analyze.html", company_options=COMPANY_OPTIONS, ai_result=ai_result, target_company=target_company, target_role=target_role)

@app.route("/resume", methods=["GET", "POST"])
def resume():
    exps = fetch_all_experiences(order_by_recent=False)
    resume_text = None
    target_company = None
    target_role = None

    if request.method == "POST":
        target_company = request.form.get("company") or ""
        target_role = request.form.get("role") or ""
        portfolio_text = build_portfolio_text(exps)
        prompt = f"""
        [학생 정보] 전공: 컴퓨터공학
        목표 회사: {target_company or '미지정'}
        목표 직무: {target_role or '미지정'}
        [활동 목록]
        {portfolio_text}

        이력서 상단 '핵심 역량 요약'을 마크다운으로 작성해줘.
        1. **강조할 핵심 역량 3가지**
        2. **주요 성과 요약** (수치 위주)
        3. **{target_company} 맞춤 한 줄 어필**
        """
        resume_text = call_groq(prompt, "너는 이력서 컨설턴트다.")

    return render_template(
        "resume.html",
        experiences=exps,
        resume_text=resume_text,
        company_options=COMPANY_OPTIONS,
        target_company=target_company,
        target_role=target_role,
    )

@app.route("/cover-letter", methods=["GET", "POST"])
def cover_letter():
    exps = fetch_all_experiences(order_by_recent=False)
    letter_text = None
    target_company = None
    target_role = None

    if request.method == "POST":
        target_company = request.form.get("company") or ""
        target_role = request.form.get("role") or ""
        extra_request = request.form.get("extra_request", "").strip()
        
        portfolio_text = build_portfolio_text(exps)
        prompt = f"""
        지원 회사: {target_company}
        지원 직무: {target_role}
        추가 요청: {extra_request}
        [활동 목록]
        {portfolio_text}

        자기소개서 초안(지원동기+직무역량)을 마크다운으로 작성해줘.
        - 문단 나누기 필수
        - 구체적인 경험을 근거로 들 것
        """
        letter_text = call_groq(prompt, "너는 자소서 전문 작가다.")

    return render_template(
        "cover_letter.html",
        experiences=exps,
        letter_text=letter_text,
        company_options=COMPANY_OPTIONS,
        target_company=target_company,
        target_role=target_role,
    )

# =========================
# 엑셀(CSV) 백업 & 복구 (Postgres 버전)
# =========================

@app.route("/backup", methods=["GET", "POST"])
def backup_page():
    return render_template("backup.html")

@app.route("/api/export")
def export_data():
    """DB 데이터를 엑셀(CSV) 파일로 다운로드"""
    exps = fetch_all_experiences(order_by_recent=False)

    output = io.StringIO()
    writer = csv.writer(output)
    
    # 헤더
    headers = ['id', '카테고리', '제목', '설명', '시작일', '종료일', '기술스택', '투입시간', '생성일']
    writer.writerow(headers)

    for row in exps:
        writer.writerow([
            row['id'], row['category'], row['title'], row['description'],
            row['start_date'], row['end_date'], row['skills'],
            row['hours'], row['created_at']
        ])

    output.seek(0)
    return Response(
        output.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=my_portfolio_backup.csv"}
    )

@app.route("/api/import", methods=["POST"])
def import_data():
    """엑셀(CSV) 파일을 업로드해서 DB에 복구"""
    if 'file' not in request.files:
        return "파일이 없습니다.", 400
        
    file = request.files['file']
    if file.filename == '':
        return "파일을 선택해주세요.", 400

    try:
        stream = io.TextIOWrapper(file.stream, encoding='utf-8-sig')
        csv_input = csv.DictReader(stream)

        conn = get_db_connection()
        cur = conn.cursor()

        # 기존 데이터 유지한 채로 추가 (삭제 원하면 아래 주석 해제)
        # cur.execute("DELETE FROM experience") 

        for row in csv_input:
            # CSV 키값과 DB 컬럼명 매핑
            cur.execute(
                """
                INSERT INTO experience 
                (category, title, description, start_date, end_date, skills, hours, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row.get('카테고리'), row.get('제목'), row.get('설명'),
                    row.get('시작일'), row.get('종료일') or None,
                    row.get('기술스택'), row.get('투입시간'), row.get('생성일')
                )
            )
        
        conn.commit()
        cur.close()
        conn.close()
        
        return redirect(url_for('index'))
        
    except Exception as e:
        return f"엑셀 복구 실패: {str(e)}", 500

# =========================
# 메인 실행
# =========================

if __name__ == "__main__":
    # 서버 시작 시 테이블 생성 시도
    try:
        init_db()
    except Exception as e:
        print(f"⚠️ DB Init Failed: {e}")
        print("환경변수 DATABASE_URL을 확인하세요.")

    from os import environ
    app.run(host="0.0.0.0", port=int(environ.get("PORT", 5000)), debug=True)