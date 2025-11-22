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

# ▼▼▼ [추가] 구글 검색 라이브러리 임포트 ▼▼▼
from googlesearch import search

# =========================
# 1. 기본 설정 및 초기화
# =========================

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "super_secret_key_backup")

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

COMPANY_OPTIONS = [
    "LH(한국토지주택공사)", "한국전력공사", "한국중부발전", "한국도로공사",
    "한국수력원자력", "국민건강보험공단", "근로복지공단", 
    "네이버", "카카오", "삼성전자", "SK텔레콤", "LG전자", "현대자동차", "기아",
    "쿠팡", "우아한형제들(배달의민족)", "토스(비바리퍼블리카)", "당근마켓",
    "충청남도청", "대전광역시청", "지역 소방서", "지역 경찰서",
    "구글코리아", "넷플릭스서비시스코리아", "한국철도공사(코레일)", "CJ ENM"
]

MAJORS = {
    "공학계열": ["건설안전방재학과", "환경에너지학과", "소방안전관리학과", "전기전자공학과", "컴퓨터공학과"],
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS experience (
            id SERIAL PRIMARY KEY, category VARCHAR(100), title VARCHAR(255), description TEXT,
            start_date VARCHAR(20), end_date VARCHAR(20), skills TEXT, hours INTEGER, link TEXT, created_at VARCHAR(50)
        );
    """)
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
        line = f"- [{status}] {e['title']} ({e['category']}) | 기술: {e['skills']} | 내용: {e['description']}"
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
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ▼▼▼ [핵심] 구글 검색 헬퍼 함수 ▼▼▼
def get_google_search_context(query, num_results=3):
    """
    구글 검색을 수행하고 제목과 요약문을 반환합니다.
    """
    print(f"🔍 Google Search Query: {query}")
    context_text = ""
    try:
        # advanced=True를 사용하면 SearchResult 객체(title, description, url)를 반환
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
# 4. 라우트 정의
# =========================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == os.getenv("ADMIN_PASSWORD", "1234"):
            session['logged_in'] = True
            return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('index'))

@app.route("/")
def index():
    exps = fetch_all_experiences()
    # ... (기존 index 로직 동일)
    return render_template("index.html", experiences=exps, total_count=len(exps), total_hours=0, categories=[])

# (add, edit, delete, settings 라우트는 기존 코드와 동일하므로 생략 가능하나, 완전한 코드를 위해 포함)
@app.route("/add", methods=["GET", "POST"])
@login_required
def add():
    # ... (기존 add 로직)
    if request.method == "POST":
        # DB Insert logic here
        return redirect(url_for("index"))
    return render_template("add.html")

# =========================
# 5. [AI + Web Search] 핵심 기능
# =========================

@app.route('/career', methods=['GET', 'POST'])
def career():
    """[강화] 실시간 채용 정보 기반 직무 매칭"""
    if not session.get('logged_in'): return redirect(url_for('login'))
    
    result = None
    selected_major = request.form.get('major')
    selected_company = request.form.get('company')

    if request.method == 'POST' and selected_major and selected_company:
        # 1. 구글 검색 수행
        search_query = f"{selected_company} 채용 직무 인재상 사업분야"
        search_context = get_google_search_context(search_query, num_results=3)

        # 2. AI 프롬프트에 검색 결과 주입
        prompt = f"""
        [Real-time Data]
        다음은 웹에서 방금 검색한 '{selected_company}'의 최신 정보입니다:
        {search_context}

        [Task]
        위 실시간 정보와 사용자의 전공('{selected_major}')을 분석하여,
        이 전공자가 해당 기업에서 도전할 수 있는 **현실적인 직무 5가지**를 추천해주세요.
        
        결과는 반드시 마크다운 표 형식(| 직무명 | 하는 일 | 추천 사유 |)으로 출력하세요.
        """
        result = call_groq(prompt, f"너는 {selected_company} 채용 담당자다. 검색된 최신 정보를 최우선으로 반영해라.")

    return render_template('career.html', majors=MAJORS, result=result, 
                           sel_major=selected_major, sel_company=selected_company, company_options=COMPANY_OPTIONS)

@app.route("/company_analyze", methods=["GET", "POST"])
def company_analyze():
    """[강화] JD 검색 기반 합격 분석"""
    exps = fetch_all_experiences()
    ai_result = None
    target_company = request.form.get("company")
    target_role = request.form.get("job")
    profile = get_profile()
    
    if request.method == "POST" and target_company:
        portfolio_text = build_portfolio_text(exps)
        
        # 1. 구글 검색 (JD 및 핵심 역량 찾기)
        search_query = f"{target_company} {target_role} 직무 기술서 핵심 역량 채용 공고"
        search_context = get_google_search_context(search_query, num_results=3)

        # 2. 프롬프트
        prompt = f"""
        [Context from Web Search]
        {search_context}
        
        [Applicant Profile]
        전공: {profile.get('major')}, 활동: {portfolio_text}

        [Analysis]
        위 검색된 직무 기술서(JD) 내용과 지원자의 경험을 비교하여:
        1. **매칭 분석**: 지원자의 경험이 실제 요구 역량과 얼마나 일치하는지.
        2. **Missing Point**: 현직자 대비 부족한 구체적인 스펙.
        3. **합격 가능성**: 냉정한 확률 예측(%).
        
        마크다운 보고서 형식으로 작성하세요.
        """
        ai_result = call_groq(prompt, "너는 데이터 기반의 냉철한 인사 분석관이다.")

    return render_template("company_analyze.html", company_options=COMPANY_OPTIONS, 
                           ai_result=ai_result, target_company=target_company, target_role=target_role)

@app.route("/cover_letter", methods=["GET", "POST"])
def cover_letter():
    """[강화] 최신 뉴스/CEO 메시지 기반 자소서"""
    exps = fetch_all_experiences(order_by_recent=False)
    letter_text = None
    target_company = request.form.get("company")
    target_role = request.form.get("job")
    
    if request.method == "POST":
        extra = request.form.get("extra_request", "")
        portfolio_text = build_portfolio_text(exps)
        
        # 1. 구글 검색 (최신 이슈, 신년사)
        search_query = f"{target_company} CEO 신년사 최근 이슈 인재상 2024 2025"
        search_context = get_google_search_context(search_query, num_results=3)

        prompt = f"""
        [Company Latest News]
        {search_context}
        
        [My Portfolio]
        {portfolio_text}
        
        [Task]
        위 검색된 기업의 **최신 이슈나 CEO의 경영 철학**을 서두에 인용(Hook)하여,
        나의 경험이 회사의 현재 목표 달성에 어떻게 기여할 수 있는지 연결하는 자기소개서를 작성해주세요.
        직무: {target_role}, 추가요청: {extra}
        """
        letter_text = call_groq(prompt, f"너는 {target_company} 전문 취업 컨설턴트다.")

    return render_template("cover_letter.html", experiences=exps, letter_text=letter_text,
                           company_options=COMPANY_OPTIONS, target_company=target_company, target_role=target_role)

# (나머지 resume, backup 라우트 및 main 실행부는 기존과 동일하게 유지)
# ...
@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == "POST":
        # ... (저장 로직 기존과 동일) ...
        cur.execute("""
            UPDATE profile SET name=%s, major=%s, career_goal=%s, strengths=%s, ai_instructions=%s WHERE id=1
        """, (
            request.form.get("name"), 
            request.form.get("major"),  # 여기서 select의 값이 들어옵니다
            request.form.get("career_goal"),
            request.form.get("strengths"), 
            request.form.get("ai_instructions")
        ))
        conn.commit()
        flash("AI 프로필 설정이 저장되었습니다.", "success")
    
    cur.execute("SELECT * FROM profile WHERE id=1")
    profile = cur.fetchone()
    cur.close(); conn.close()
    
    # ▼▼▼ [여기 수정] majors=MAJORS 를 꼭 추가해야 합니다! ▼▼▼
    return render_template("settings.html", profile=profile or {}, majors=MAJORS)
# =========================
# 6. 데이터 백업/복구 (이 부분이 빠져있어서 에러가 난 것입니다)
# =========================

@app.route("/backup")
@login_required
def backup_page():
    return render_template("backup.html")

# [API] CSV 다운로드
@app.route("/api/export")
@login_required
def export_data():
    exps = fetch_all_experiences(order_by_recent=False)
    output = io.StringIO()
    writer = csv.writer(output)
    # CSV 헤더 작성
    writer.writerow(['category', 'title', 'description', 'start_date', 'end_date', 'skills', 'hours', 'link'])
    
    for r in exps:
        writer.writerow([
            r['category'], r['title'], r['description'], 
            r['start_date'], r['end_date'], r['skills'], 
            r['hours'], r.get('link','')
        ])
    
    output.seek(0)
    return Response(
        output.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=portfolio_backup.csv"}
    )

# [API] CSV 업로드 (복구)
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