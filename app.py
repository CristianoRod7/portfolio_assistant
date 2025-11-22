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
from googlesearch import search  # 구글 검색 라이브러리

# =========================
# 1. 기본 설정 및 초기화
# =========================

app = Flask(__name__)
# 보안 키 설정 (배포 시 환경변수로 관리 권장)
app.secret_key = os.environ.get("SECRET_KEY", "super_secret_key_backup")

# Groq 클라이언트
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# 자동완성용 기업 목록
COMPANY_OPTIONS = [
    "LH(한국토지주택공사)", "한국전력공사", "한국중부발전", "한국도로공사",
    "한국수력원자력", "국민건강보험공단", "근로복지공단", 
    "네이버", "카카오", "삼성전자", "SK텔레콤", "LG전자", "현대자동차", "기아",
    "쿠팡", "우아한형제들(배달의민족)", "토스(비바리퍼블리카)", "당근마켓",
    "충청남도청", "대전광역시청", "지역 소방서", "지역 경찰서",
    "구글코리아", "넷플릭스서비시스코리아", "한국철도공사(코레일)", "CJ ENM"
]

# 학과 목록
MAJORS = {
    "공학계열": ["건설안전방재학과", "환경에너지학과", "소방안전관리학과", "전기전자공학과", "컴퓨터공학과", "건축인테리어학과", "첨단기술융합학부"],
    "인문사회계열": ["자치행정학과", "경찰행정학과", "토지행정학과", "사회복지학과"],
    "자연과학계열": ["호텔조리제빵학과", "뷰티코디네이션학과", "작업치료학과", "스마트팜학과"]
}

# =========================
# 2. DB 유틸리티 (PostgreSQL)
# =========================

def get_db_connection():
    db_url = os.getenv("DATABASE_URL")
    if not db_url: return None
    try:
        return psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"DB Error: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if not conn: return
    cur = conn.cursor()
    # 경험 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS experience (
            id SERIAL PRIMARY KEY, category VARCHAR(100), title VARCHAR(255), description TEXT,
            start_date VARCHAR(20), end_date VARCHAR(20), skills TEXT, hours INTEGER, link TEXT, created_at VARCHAR(50)
        );
    """)
    # 프로필 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS profile (
            id INTEGER PRIMARY KEY, name VARCHAR(100), major VARCHAR(100),
            career_goal TEXT, strengths TEXT, ai_instructions TEXT
        );
    """)
    cur.execute("INSERT INTO profile (id) VALUES (1) ON CONFLICT (id) DO NOTHING;")
    conn.commit(); cur.close(); conn.close()

def fetch_all_experiences(order_by_recent=True):
    conn = get_db_connection()
    if not conn: return []
    cur = conn.cursor()
    sql = "SELECT * FROM experience" + (" ORDER BY start_date DESC" if order_by_recent else "")
    cur.execute(sql)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

def get_profile():
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
        status = "완료" if (e['end_date'] and e['end_date'] < today) else "진행 중"
        # 중요도(별점) 표기
        rating = f"{e['hours']}점" if e['hours'] else "미설정"
        line = f"- [{status}] {e['title']} ({e['category']}) | 기술: {e['skills']} | 중요도: {rating} | 내용: {e['description']}"
        lines.append(line)
    return "\n".join(lines) if lines else "활동 없음"

# =========================
# 3. 유틸리티 및 미들웨어
# =========================

@app.context_processor
def inject_user():
    return dict(logged_in=session.get('logged_in'))

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash("관리자 로그인이 필요합니다.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ▼▼▼ 구글 검색 헬퍼 함수 ▼▼▼
def get_google_search_context(query, num_results=3):
    print(f"🔍 Google Search Query: {query}")
    context_text = ""
    try:
        results = search(query, num_results=num_results, advanced=True)
        for i, res in enumerate(results, 1):
            context_text += f"""
            [검색 결과 {i}]
            - 제목: {res.title}
            - 요약: {res.description}
            - 출처: {res.url}
            """
    except Exception as e:
        print(f"❌ Search Failed: {e}")
        return "(검색 기능을 일시적으로 사용할 수 없습니다. 내부 지식으로 대체합니다.)"
    return context_text

def call_groq(prompt: str, system_msg: str) -> str:
    if not client.api_key: return "API Key Error"
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
        )
        return markdown.markdown(completion.choices[0].message.content, extensions=['extra', 'nl2br', 'tables'])
    except Exception as e:
        return f"AI Error: {str(e)}"

# =========================
# 4. 라우트 정의 (CRUD)
# =========================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == os.getenv("ADMIN_PASSWORD", "1234"):
            session['logged_in'] = True
            flash("로그인되었습니다.", "success")
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='비밀번호가 틀렸습니다.')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash("로그아웃되었습니다.", "info")
    return redirect(url_for('index'))

@app.route("/")
def index():
    exps = fetch_all_experiences()
    
    # 통계 계산 (Total Hours는 이제 '총 별점' 합계로 사용되거나 평균 계산에 쓰임)
    total_hours = sum([e['hours'] for e in exps if e['hours']])
    
    categories = {}
    for e in exps:
        categories[e['category']] = categories.get(e['category'], 0) + 1
    cat_list = [{"category": k, "cnt": v} for k, v in categories.items()]

    # 상태값 처리
    processed_exps = []
    today = datetime.now().strftime("%Y-%m-%d")
    for e in exps:
        e_dict = dict(e)
        if e_dict['end_date'] and e_dict['end_date'] < today:
            e_dict.update({'status': 'completed', 'status_color': 'success'})
        else:
            e_dict.update({'status': 'ongoing', 'status_color': 'warning'})
        processed_exps.append(e_dict)

    return render_template("index.html", experiences=processed_exps, 
                           total_count=len(exps), total_hours=total_hours, categories=cat_list)

@app.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO experience (category, title, description, start_date, end_date, skills, hours, link, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            request.form.get("category"), request.form.get("title"), request.form.get("description"),
            request.form.get("start_date") or None, request.form.get("end_date") or None,
            request.form.get("skills"), request.form.get("hours", 3), # 기본값 3점
            request.form.get("link"), datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit(); cur.close(); conn.close()
        return redirect(url_for("index"))
    return render_template("add.html")

# ▼▼▼ [빠져있던 부분] 상세 보기 / 수정 / 삭제 라우트 추가 ▼▼▼

@app.route("/experience/<int:exp_id>")
def experience_detail(exp_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM experience WHERE id = %s", (exp_id,))
    exp = cur.fetchone()
    cur.close(); conn.close()
    if not exp: abort(404)
    return render_template("experience_detail.html", exp=exp)

@app.route("/edit/<int:exp_id>", methods=["GET", "POST"])
@login_required
def edit(exp_id):
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == "POST":
        cur.execute("""
            UPDATE experience SET category=%s, title=%s, description=%s, start_date=%s, end_date=%s, hours=%s, skills=%s, link=%s
            WHERE id=%s
        """, (
            request.form.get("category"), request.form.get("title"), request.form.get("description"),
            request.form.get("start_date"), request.form.get("end_date") or None,
            request.form.get("hours"), request.form.get("skills"), request.form.get("link"), exp_id
        ))
        conn.commit(); cur.close(); conn.close()
        return redirect(url_for('experience_detail', exp_id=exp_id))
    
    cur.execute("SELECT * FROM experience WHERE id=%s", (exp_id,))
    exp = cur.fetchone()
    cur.close(); conn.close()
    if not exp: abort(404)
    return render_template("add.html", exp=exp, is_edit=True)

@app.route("/delete/<int:exp_id>")
@login_required
def delete(exp_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM experience WHERE id=%s", (exp_id,))
    conn.commit(); cur.close(); conn.close()
    return redirect(url_for('index'))

# =========================
# 5. AI 분석 및 도구
# =========================

@app.route("/analyze")
def analyze():
    """전체 포트폴리오 종합 분석"""
    exps = fetch_all_experiences(order_by_recent=False)
    profile = get_profile()
    if not exps:
        return render_template("analyze.html", experiences=[], ai_result="<p>활동을 먼저 등록해주세요.</p>")
    
    portfolio_text = build_portfolio_text(exps)
    prompt = f"""
    [사용자 정보] 이름: {profile.get('name')}, 전공: {profile.get('major')}, 목표: {profile.get('career_goal')}
    [활동 목록] {portfolio_text}
    
    위 정보를 바탕으로 포트폴리오의 일관성, 강점 3가지, 보완해야 할 점을 분석해주세요.
    """
    ai_result = call_groq(prompt, "너는 날카로운 커리어 코치다.")
    return render_template("analyze.html", experiences=exps, ai_result=ai_result)

@app.route('/career', methods=['GET', 'POST'])
def career():
    if not session.get('logged_in'): return redirect(url_for('login'))
    result = None
    selected_major = request.form.get('major')
    selected_company = request.form.get('company')

    if request.method == 'POST' and selected_major and selected_company:
        search_context = get_google_search_context(f"{selected_company} 채용 직무 인재상 사업분야")
        prompt = f"""
        [Web Data] {search_context}
        [User] 전공: {selected_major}
        
        위 정보를 바탕으로 이 전공자가 '{selected_company}'에서 도전 가능한 직무 5가지를 마크다운 표로 추천해줘.
        """
        result = call_groq(prompt, f"너는 {selected_company} 채용 전문가다.")

    return render_template('career.html', majors=MAJORS, result=result, 
                           sel_major=selected_major, sel_company=selected_company, company_options=COMPANY_OPTIONS)

@app.route("/company_analyze", methods=["GET", "POST"])
def company_analyze():
    exps = fetch_all_experiences()
    ai_result = None
    target_company = request.form.get("company")
    target_role = request.form.get("job")
    profile = get_profile()
    
    if request.method == "POST" and target_company:
        portfolio_text = build_portfolio_text(exps)
        search_context = get_google_search_context(f"{target_company} {target_role} 직무 기술서 핵심 역량")
        
        prompt = f"""
        [Web Data] {search_context}
        [Profile] 전공: {profile.get('major')}, 활동: {portfolio_text}
        
        지원자의 경험이 해당 직무 JD와 얼마나 일치하는지, 부족한 점은 무엇인지, 합격 확률(%)은 얼마인지 분석해줘.
        """
        ai_result = call_groq(prompt, "너는 냉철한 인사 담당자다.")

    return render_template("company_analyze.html", company_options=COMPANY_OPTIONS, 
                           ai_result=ai_result, target_company=target_company, target_role=target_role)

@app.route("/resume", methods=["GET", "POST"])
def resume():
    exps = fetch_all_experiences(order_by_recent=False)
    resume_text = None
    target_company = request.form.get("company")
    target_role = request.form.get("job")
    profile = get_profile()
    
    if request.method == "POST":
        portfolio_text = build_portfolio_text(exps)
        prompt = f"""
        [Target] 회사: {target_company}, 직무: {target_role}
        [User] {profile}
        [Experience] {portfolio_text}
        
        위 내용을 바탕으로 성과를 수치화하고 전문 용어를 사용하여 이력서 초안을 작성해줘.
        """
        resume_text = call_groq(prompt, "너는 전문 이력서 에디터다.")

    return render_template("resume.html", experiences=exps, resume_text=resume_text, 
                           company_options=COMPANY_OPTIONS, target_company=target_company, target_role=target_role)

@app.route("/cover_letter", methods=["GET", "POST"])
def cover_letter():
    exps = fetch_all_experiences(order_by_recent=False)
    letter_text = None
    target_company = request.form.get("company")
    target_role = request.form.get("job")
    
    if request.method == "POST":
        extra = request.form.get("extra_request", "")
        portfolio_text = build_portfolio_text(exps)
        search_context = get_google_search_context(f"{target_company} CEO 신년사 최근 이슈 인재상")

        prompt = f"""
        [Web Data] {search_context}
        [Portfolio] {portfolio_text}
        [Req] {extra}
        
        기업의 최신 이슈와 내 경험을 연결하여 '{target_role}' 직무 자기소개서를 작성해줘.
        """
        letter_text = call_groq(prompt, f"너는 {target_company} 전문 취업 컨설턴트다.")

    return render_template("cover_letter.html", experiences=exps, letter_text=letter_text,
                           company_options=COMPANY_OPTIONS, target_company=target_company, target_role=target_role)

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == "POST":
        cur.execute("""
            UPDATE profile SET name=%s, major=%s, career_goal=%s, strengths=%s, ai_instructions=%s WHERE id=1
        """, (
            request.form.get("name"), 
            request.form.get("major"), 
            request.form.get("career_goal"),
            request.form.get("strengths"), 
            request.form.get("ai_instructions")
        ))
        conn.commit()
        flash("설정이 저장되었습니다.", "success")
    
    cur.execute("SELECT * FROM profile WHERE id=1")
    profile = cur.fetchone()
    cur.close(); conn.close()
    return render_template("settings.html", profile=profile or {}, majors=MAJORS)

# =========================
# 6. 데이터 백업/복구
# =========================

@app.route("/backup")
@login_required
def backup_page():
    return render_template("backup.html")

@app.route("/api/export")
@login_required
def export_data():
    exps = fetch_all_experiences(order_by_recent=False)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['category', 'title', 'description', 'start_date', 'end_date', 'skills', 'hours', 'link'])
    for r in exps:
        writer.writerow([
            r['category'], r['title'], r['description'], 
            r['start_date'], r['end_date'], r['skills'], 
            r['hours'], r.get('link','')
        ])
    output.seek(0)
    return Response(output.getvalue().encode("utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=portfolio_backup.csv"})

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
        cnt = 0
        for row in csv_input:
            cur.execute("""
                INSERT INTO experience (category, title, description, start_date, end_date, skills, hours, link, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                row.get('category'), row.get('title'), row.get('description'),
                row.get('start_date'), row.get('end_date') or None,
                row.get('skills'), row.get('hours', 0), row.get('link', ''),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))
            cnt += 1
        conn.commit(); cur.close(); conn.close()
        flash(f"{cnt}개의 데이터가 복구되었습니다.", "success")
        return redirect(url_for('index'))
    except Exception as e:
        return f"복구 실패: {str(e)}", 500

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)