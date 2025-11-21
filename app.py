import os
import csv
import io
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import markdown
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    abort,
    Response,
    flash,
    session
)
from groq import Groq

# =========================
# 기본 설정
# =========================

app = Flask(__name__)
# 세션 보안 키 (Render 환경변수에 SECRET_KEY 등록 권장)
app.secret_key = os.environ.get("SECRET_KEY", "dev_secret_key_12345")

# Groq 클라이언트
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

COMPANY_OPTIONS = [
    "LH", "한국전력공사", "한국중부발전", "한국도로공사",
    "한국수력원자력", "네이버", "카카오", "삼성전자", "SK텔레콤"
]

# =========================
# DB 유틸 (PostgreSQL)
# =========================

def get_db_connection():
    """PostgreSQL DB 연결 함수"""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("⚠️ 경고: DATABASE_URL이 없습니다.")
        return None
    
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    return conn

def init_db():
    """테이블 생성 (서버 시작 시 실행)"""
    try:
        conn = get_db_connection()
        if not conn: return

        cur = conn.cursor()
        # 1. 경험 테이블 생성
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
        
        # 2. link 컬럼 마이그레이션 (없으면 추가)
        cur.execute("""
            DO $$ 
            BEGIN 
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                               WHERE table_name='experience' AND column_name='link') THEN 
                    ALTER TABLE experience ADD COLUMN link TEXT; 
                END IF; 
            END $$;
        """)

        # 3. 프로필(AI 설정) 테이블 생성
        cur.execute("""
            CREATE TABLE IF NOT EXISTS profile (
                id INTEGER PRIMARY KEY, 
                name VARCHAR(100),
                major VARCHAR(100),
                career_goal TEXT,
                strengths TEXT,
                ai_instructions TEXT
            );
        """)
        # 기본 행 생성 (ID=1)
        cur.execute("INSERT INTO profile (id) VALUES (1) ON CONFLICT (id) DO NOTHING;")
        
        conn.commit()
        cur.close()
        conn.close()
        print("✅ PostgreSQL initialized & Migrated")
    except Exception as e:
        print(f"❌ DB Init Error: {e}")

def fetch_all_experiences(order_by_recent=True):
    conn = get_db_connection()
    if not conn: return []
    
    cur = conn.cursor()
    try:
        sql = "SELECT * FROM experience"
        if order_by_recent:
            sql += " ORDER BY start_date DESC"
        cur.execute(sql)
        rows = cur.fetchall()
        return rows
    except Exception as e:
        print(f"Fetch Error: {e}")
        return []
    finally:
        cur.close()
        conn.close()

def get_profile():
    """사용자 프로필 정보 가져오기"""
    conn = get_db_connection()
    if not conn: return {}
    cur = conn.cursor()
    cur.execute("SELECT * FROM profile WHERE id = 1")
    row = cur.fetchone()
    cur.close(); conn.close()
    return dict(row) if row else {}

def build_portfolio_text(exps):
    lines = []
    today = datetime.now().strftime("%Y-%m-%d")
    for e in exps:
        status = "진행 중"
        if e['end_date'] and e['end_date'] < today:
            status = "완료"
            
        link_info = f"(증빙: {e.get('link')})" if e.get('link') else ""
        
        line = f"""
- [{status}] {e['category']} | {e['title']} {link_info}
  기간: {e['start_date']} ~ {e['end_date'] or '현재'}
  기술: {e['skills'] or ''}
  내용: {e['description'] or ''}
  시간: {e['hours']}시간
"""
        lines.append(line)
    return "\n".join(lines)

# =========================
# [핵심] 모든 템플릿에 로그인 정보 주입
# =========================
@app.context_processor
def inject_user():
    """모든 HTML 페이지에서 logged_in 변수를 사용할 수 있게 해줌"""
    return dict(logged_in=session.get('logged_in'))

# =========================
# 보안 (로그인 체크)
# =========================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash("관리자 로그인이 필요합니다.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        admin_pw = os.getenv("ADMIN_PASSWORD", "1234")
        input_pw = request.form.get('password')
        if input_pw == admin_pw:
            session['logged_in'] = True
            flash("성공적으로 로그인되었습니다.", "success")
            return redirect(url_for('index'))
        else:
            error = '비밀번호가 틀렸습니다.'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash("로그아웃 되었습니다.", "info")
    return redirect(url_for('index'))

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
# 메인 라우트
# =========================

@app.route("/")
def index():
    exps = []
    total_hours = 0
    categories = []
    
    try:
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM experience ORDER BY start_date DESC")
            exps = cur.fetchall()
            
            cur.execute("SELECT SUM(hours) as total_hours FROM experience")
            row = cur.fetchone()
            total_hours = row['total_hours'] if row and row['total_hours'] else 0
            
            cur.execute("SELECT category, COUNT(*) as cnt FROM experience GROUP BY category")
            categories = cur.fetchall()
            cur.close()
            conn.close()
    except Exception as e:
        print(f"Index DB Error: {e}")

    total_count = len(exps)
    
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
        categories=categories
    )

@app.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        category = request.form.get("category", "").strip()
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        start_date = request.form.get("start_date") or None
        end_date = request.form.get("end_date") or None
        skills = request.form.get("skills", "").strip()
        link = request.form.get("link", "").strip()
        
        hours_raw = request.form.get("hours", "").strip()
        try:
            hours = int(hours_raw) if hours_raw else 0
        except ValueError:
            hours = 0
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO experience (category, title, description, start_date, end_date, skills, hours, link, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (category, title, description, start_date, end_date, skills, hours, link, created_at)
        )
        conn.commit()
        cur.close()
        conn.close()

        return redirect(url_for("index"))
    return render_template("add.html")

@app.route("/edit/<int:exp_id>", methods=["GET", "POST"])
@login_required
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
        link = request.form.get("link", "").strip()
        
        cur.execute(
            """
            UPDATE experience 
            SET category=%s, title=%s, description=%s, start_date=%s, end_date=%s, hours=%s, skills=%s, link=%s
            WHERE id=%s
            """,
            (category, title, description, start_date, end_date, hours, skills, link, exp_id)
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
@login_required
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
# 설정 (학습) 라우트
# =========================
@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == "POST":
        cur.execute("""
            UPDATE profile 
            SET name=%s, major=%s, career_goal=%s, strengths=%s, ai_instructions=%s
            WHERE id=1
        """, (
            request.form.get("name"), request.form.get("major"), 
            request.form.get("career_goal"), request.form.get("strengths"),
            request.form.get("ai_instructions")
        ))
        conn.commit()
        flash("AI 설정이 저장되었습니다!", "success")
        
    cur.execute("SELECT * FROM profile WHERE id=1")
    profile = cur.fetchone()
    cur.close(); conn.close()
    return render_template("settings.html", profile=profile or {})

# =========================
# AI 분석 (프로필 반영)
# =========================

@app.route("/analyze")
def analyze():
    exps = fetch_all_experiences(order_by_recent=False)
    profile = get_profile() # 프로필 가져오기
    
    if not exps:
        return render_template("analyze.html", experiences=[], ai_result="<p>활동을 먼저 등록해주세요.</p>")
    
    portfolio_text = build_portfolio_text(exps)
    
    user_context = f"""
    [사용자 프로필]
    이름: {profile.get('name', '학생')}
    전공: {profile.get('major', '미입력')}
    커리어 목표: {profile.get('career_goal', '미입력')}
    나의 강점/성격: {profile.get('strengths', '미입력')}
    
    [AI 페르소나/요청사항 (이대로 행동해)]:
    {profile.get('ai_instructions', '친절하고 전문적인 톤으로 분석해줘.')}
    """
    
    prompt = f"{user_context}\n\n[활동 목록]\n{portfolio_text}\n\n위 정보를 바탕으로 종합적인 포트폴리오 분석을 해줘. (마크다운 형식)"
    ai_result = call_groq(prompt, "너는 사용자의 정보를 완벽히 숙지한 전담 커리어 코치다.")
    
    return render_template("analyze.html", experiences=exps, ai_result=ai_result)

@app.route("/company-analyze", methods=["GET", "POST"])
def company_analyze():
    exps = fetch_all_experiences()
    ai_result = None
    target_company = None
    target_role = None
    profile = get_profile()
    
    if request.method == "POST":
        target_company = request.form.get("company")
        target_role = request.form.get("role")
        portfolio_text = build_portfolio_text(exps)
        
        user_context = f"""
        사용자 이름: {profile.get('name', '학생')}
        전공: {profile.get('major', '관련 전공')}
        사용자가 강조하고 싶은 점: {profile.get('strengths', '')}
        """
        
        prompt = f"목표 회사: {target_company}\n목표 직무: {target_role}\n{user_context}\n[내 활동]\n{portfolio_text}\n\n{target_company}의 {target_role} 채용 담당자 관점에서 합격 가능성과 전략을 분석해줘."
        ai_result = call_groq(prompt, "너는 대기업 인사담당자다.")

    return render_template("company_analyze.html", company_options=COMPANY_OPTIONS, ai_result=ai_result, target_company=target_company, target_role=target_role)

@app.route("/resume", methods=["GET", "POST"])
def resume():
    exps = fetch_all_experiences(order_by_recent=False)
    resume_text = None
    target_company = None
    target_role = None
    profile = get_profile()
    
    if request.method == "POST":
        target_company = request.form.get("company") or ""
        target_role = request.form.get("role") or ""
        portfolio_text = build_portfolio_text(exps)
        
        prompt = f"""
        [프로필]
        이름: {profile.get('name', 'OOO')}
        전공: {profile.get('major', '')}
        목표: {profile.get('career_goal', '')}
        
        [요청사항]
        {profile.get('ai_instructions', '깔끔한 문체로 작성해줘.')}
        
        [활동 목록]
        {portfolio_text}
        
        위 내용을 바탕으로 {target_company} {target_role} 지원용 이력서의 '핵심 역량 요약' 파트를 작성해줘.
        """
        resume_text = call_groq(prompt, "너는 이력서 전문 에디터다.")

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
    profile = get_profile()
    
    if request.method == "POST":
        target_company = request.form.get("company") or ""
        target_role = request.form.get("role") or ""
        extra_request = request.form.get("extra_request", "").strip()
        
        portfolio_text = build_portfolio_text(exps)
        
        prompt = f"""
        [지원자 정보]
        이름: {profile.get('name', 'OOO')}
        전공: {profile.get('major', '')}
        나의 강점: {profile.get('strengths', '')}
        
        [AI 지침]
        {profile.get('ai_instructions', '')}
        
        [활동 데이터]
        {portfolio_text}
        
        지원 회사: {target_company} / 직무: {target_role}
        추가 요청: {extra_request}
        
        위 정보를 종합하여 자기소개서 초안을 작성해줘.
        """
        letter_text = call_groq(prompt, "너는 자소서 작가다.")

    return render_template(
        "cover_letter.html",
        experiences=exps,
        letter_text=letter_text,
        company_options=COMPANY_OPTIONS,
        target_company=target_company,
        target_role=target_role,
    )

# =========================
# 엑셀 백업
# =========================

@app.route("/backup", methods=["GET", "POST"])
@login_required
def backup_page():
    return render_template("backup.html")

@app.route("/api/export")
@login_required
def export_data():
    exps = fetch_all_experiences(order_by_recent=False)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['id', '카테고리', '제목', '설명', '시작일', '종료일', '기술스택', '투입시간', '링크', '생성일'])
    for row in exps:
        writer.writerow([
            row['id'], row['category'], row['title'], row['description'],
            row['start_date'], row['end_date'], row['skills'],
            row['hours'], row.get('link', ''), row['created_at']
        ])
    output.seek(0)
    return Response(
        output.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=my_portfolio_backup.csv"}
    )

@app.route("/api/import", methods=["POST"])
@login_required
def import_data():
    if 'file' not in request.files: return "파일 없음", 400
    file = request.files['file']
    if file.filename == '': return "파일 선택 안함", 400

    try:
        stream = io.TextIOWrapper(file.stream, encoding='utf-8-sig')
        csv_input = csv.DictReader(stream)
        conn = get_db_connection()
        cur = conn.cursor()
        for row in csv_input:
            cur.execute(
                """
                INSERT INTO experience (category, title, description, start_date, end_date, skills, hours, link, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row.get('카테고리'), row.get('제목'), row.get('설명'),
                    row.get('시작일'), row.get('종료일') or None,
                    row.get('기술스택'), row.get('투입시간'), row.get('링크', ''), row.get('생성일')
                )
            )
        conn.commit()
        cur.close()
        conn.close()
        flash("데이터가 성공적으로 복구되었습니다.", "success")
        return redirect(url_for('index'))
    except Exception as e:
        return f"복구 실패: {str(e)}", 500

if __name__ == "__main__":
    try:
        init_db()
    except Exception as e:
        print(f"⚠️ DB Init Failed: {e}")

    from os import environ
    app.run(host="0.0.0.0", port=int(environ.get("PORT", 5000)), debug=True)