import os
import csv
import io
import json
import requests
from authlib.integrations.flask_client import OAuth
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import markdown
from functools import wraps
import requests
from urllib.parse import urlencode
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
from werkzeug.security import generate_password_hash, check_password_hash

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
    "공학계열": [
        "건설안전방재학과", "환경에너지학과", "소방안전관리학과",
        "전기전자공학과", "컴퓨터공학과", "건축인테리어학과", "첨단기술융합학부"
    ],
    "인문사회계열": [
        "자치행정학과", "경찰행정학과", "토지행정학과", "사회복지학과"
    ],
    "자연과학계열": [
        "호텔조리제빵학과", "뷰티코디네이션학과", "작업치료학과", "스마트팜학과"
    ]
}

# =========================
# 2. DB 유틸리티 (PostgreSQL)
# =========================
db_initialized = False

@app.before_request
def initialize_db_once():
    global db_initialized
    if not db_initialized:
        try:
            init_db()
            db_initialized = True
            print("DB initialized once")
        except Exception as e:
            print("DB init error:", e)

def get_db_connection():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        return None
    try:
        return psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"DB Error: {e}")
        return None


def init_db():
    """
    Neon(PostgreSQL)에 users / profile / experience 테이블 생성.
    before_first_request에서 한 번만 호출된다.
    """
    conn = get_db_connection()
    if not conn:
        print("❌ DB 연결 실패")
        return
    cur = conn.cursor()

    # users 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email VARCHAR(120) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            created_at VARCHAR(50)
        );
    """)

    # profile 테이블 (users와 1:1 매칭)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS profile (
            user_id INTEGER PRIMARY KEY,
            name VARCHAR(100),
            major VARCHAR(100),
            career_goal TEXT,
            strengths TEXT,
            ai_instructions TEXT,
            CONSTRAINT fk_profile_user
              FOREIGN KEY (user_id)
              REFERENCES users(id)
              ON DELETE CASCADE
        );
    """)

    # experience 테이블 (user_id FK)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS experience (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            category VARCHAR(100),
            title VARCHAR(255),
            description TEXT,
            start_date VARCHAR(20),
            end_date VARCHAR(20),
            skills TEXT,
            hours INTEGER,
            link TEXT,
            created_at VARCHAR(50),
            CONSTRAINT fk_experience_user
              FOREIGN KEY (user_id)
              REFERENCES users(id)
              ON DELETE CASCADE
        );
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("✅ DB 초기화 완료 (테이블 생성됨)")


def fetch_all_experiences(order_by_recent=True, user_id=None):
    """
    user_id가 있으면 해당 유저 것만, 없으면 전체(관리자용).
    """
    conn = get_db_connection()
    if not conn:
        return []
    cur = conn.cursor()
    sql = "SELECT * FROM experience"
    params = []
    if user_id is not None:
        sql += " WHERE user_id = %s"
        params.append(user_id)
    if order_by_recent:
        sql += " ORDER BY start_date DESC NULLS LAST"
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_profile(user_id):
    """
    유저별 프로필 1개.
    """
    if not user_id:
        return {}
    conn = get_db_connection()
    if not conn:
        return {}
    cur = conn.cursor()
    cur.execute("SELECT * FROM profile WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else {}


def build_portfolio_text(exps):
    lines = []
    today = datetime.now().strftime("%Y-%m-%d")
    for e in exps:
        status = "완료" if (e['end_date'] and e['end_date'] < today) else "진행 중"
        rating = f"{e['hours']}점" if e['hours'] else "미설정"
        line = (
            f"- [{status}] {e['title']} ({e['category']}) | 기술: {e['skills']} "
            f"| 중요도: {rating} | 내용: {e['description']}"
        )
        lines.append(line)
    return "\n".join(lines) if lines else "활동 없음"


# =========================
# 3. 유틸리티 및 미들웨어
# =========================

@app.context_processor
def inject_user():
    return dict(
        logged_in=session.get('logged_in'),
        is_admin=session.get('is_admin'),
        current_user_id=session.get('user_id')
    )


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash("로그인이 필요합니다.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            flash("관리자만 접근 가능합니다.", "danger")
            return redirect(url_for('index'))
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
    if not client.api_key:
        return "API Key Error"
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
        )
        return markdown.markdown(
            completion.choices[0].message.content,
            extensions=['extra', 'nl2br', 'tables']
        )
    except Exception as e:
        return f"AI Error: {str(e)}"


# =========================
# 4. 인증 (관리자 + 일반 유저)
# =========================

# --- 관리자 로그인 ---
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == os.getenv("ADMIN_PASSWORD", "1234"):
            session['logged_in'] = True
            session['is_admin'] = True
            session['user_id'] = None
            flash("관리자로 로그인되었습니다.", "success")
            return redirect(url_for('admin_user_list'))
        else:
            return render_template('login.html', error='비밀번호가 틀렸습니다.', mode='admin')
    return render_template('login.html', mode='admin')


@app.route("/admin/user_timeline")
@admin_required
def admin_user_timeline():
    target_user_id = request.args.get("user_id", type=int)
    if not target_user_id:
        flash("user_id가 필요합니다.", "warning")
        return redirect(url_for("admin_user_list"))

    conn = get_db_connection()
    if not conn:
        return "DB 연결 오류", 500
    cur = conn.cursor()
    cur.execute("SELECT id, email, created_at FROM users WHERE id = %s", (target_user_id,))
    user_info = cur.fetchone()

    cur.execute(
        "SELECT * FROM experience WHERE user_id = %s ORDER BY start_date DESC NULLS LAST, id DESC",
        (target_user_id,)
    )
    experiences = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "admin_user_timeline.html",
        user_info=user_info,
        experiences=experiences
    )


@app.route("/admin/user_backup")
@admin_required
def admin_user_backup():
    target_user_id = request.args.get("user_id", type=int)
    if not target_user_id:
        flash("user_id가 필요합니다.", "warning")
        return redirect(url_for("admin_user_list"))

    conn = get_db_connection()
    if not conn:
        return "DB 연결 오류", 500
    cur = conn.cursor()

    cur.execute(
        "SELECT id, email, created_at FROM users WHERE id = %s",
        (target_user_id,)
    )
    user_info = cur.fetchone()

    cur.execute(
        "SELECT COUNT(*) AS cnt FROM experience WHERE user_id = %s",
        (target_user_id,)
    )
    exp_count = cur.fetchone()["cnt"]

    cur.close()
    conn.close()

    return render_template(
        "admin_user_backup.html",
        user_info=user_info,
        exp_count=exp_count
    )
# =========================
# 4-1. 소셜 로그인 (Google + Kakao)
# =========================

from authlib.integrations.flask_client import OAuth
import requests

oauth = OAuth(app)

# ----------------------------
# 구글 OAuth 설정
# ----------------------------
google = oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    access_token_url='https://oauth2.googleapis.com/token',
    authorize_url='https://accounts.google.com/o/oauth2/v2/auth',
    api_base_url='https://www.googleapis.com/oauth2/v2/',
    client_kwargs={'scope': 'openid email profile'}
)



# ----------------------------
# 카카오 OAuth
# ----------------------------

KAKAO_CLIENT_ID = os.getenv("KAKAO_REST_KEY")

# --- 일반 유저 회원가입 ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get("email")
        password = request.form.get("password")

        conn = get_db_connection()
        if not conn:
            return "DB 연결 오류", 500
        cur = conn.cursor()

        # 이메일 중복 체크
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            cur.close()
            conn.close()
            return render_template("login.html", error="이미 가입된 이메일입니다.", mode="register")

        pw_hash = generate_password_hash(password)
        cur.execute(
            """
            INSERT INTO users (email, password_hash, created_at)
            VALUES (%s, %s, %s)
            RETURNING id;
            """,
            (email, pw_hash, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        user_id = cur.fetchone()['id']

        # 기본 프로필 생성
        cur.execute("INSERT INTO profile (user_id) VALUES (%s)", (user_id,))
        conn.commit()
        cur.close()
        conn.close()

        session['logged_in'] = True
        session['is_admin'] = False
        session['user_id'] = user_id

        flash("회원가입 및 로그인 완료", "success")
        return redirect(url_for('index'))

    return render_template("login.html", mode="register")


# --- 일반 유저 로그인 ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get("email")
        password = request.form.get("password")

        conn = get_db_connection()
        if not conn:
            return "DB 연결 오류", 500
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if not user or not check_password_hash(user['password_hash'], password):
            return render_template('login.html', error='이메일 또는 비밀번호가 틀렸습니다.', mode='login')

        session['logged_in'] = True
        session['is_admin'] = False
        session['user_id'] = user['id']

        flash("로그인되었습니다.", "success")
        return redirect(url_for('index'))

    return render_template('login.html', mode='login')


@app.route('/logout')
def logout():
    session.clear()
    flash("로그아웃되었습니다.", "info")
    return redirect(url_for('login'))


# --- 관리자: 유저 목록 ---
@app.route('/admin/users')
@admin_required
def admin_user_list():
    conn = get_db_connection()
    if not conn:
        return "DB 연결 오류", 500
    cur = conn.cursor()
    cur.execute("SELECT id, email, created_at FROM users ORDER BY id")
    users = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("admin_users.html", users=users)


# =========================
# 5. 라우트 정의 (CRUD)
# =========================

@app.route("/")
@login_required
def index():
    """
    유저: 자기 경험만, 관리자: ?user_id 로 특정 유저, 없으면 전체.
    """
    if session.get('is_admin'):
        target_user_id = request.args.get('user_id', type=int)
    else:
        target_user_id = session.get('user_id')

    exps = fetch_all_experiences(user_id=target_user_id, order_by_recent=True)
    total_hours = sum([e['hours'] for e in exps if e['hours']])

    categories = {}
    for e in exps:
        categories[e['category']] = categories.get(e['category'], 0) + 1
    cat_list = [{"category": k, "cnt": v} for k, v in categories.items()]

    processed_exps = []
    today = datetime.now().strftime("%Y-%m-%d")
    for e in exps:
        e_dict = dict(e)
        if e_dict['end_date'] and e_dict['end_date'] < today:
            e_dict.update({'status': 'completed', 'status_color': 'success'})
        else:
            e_dict.update({'status': 'ongoing', 'status_color': 'warning'})
        processed_exps.append(e_dict)

    return render_template(
        "index.html",
        experiences=processed_exps,
        total_count=len(exps),
        total_hours=total_hours,
        categories=cat_list,
        target_user_id=target_user_id
    )


@app.route("/add", methods=["GET", "POST"])
@login_required
def add():
    """
    유저: 본인 경험 추가
    관리자: /add?user_id=3 형태로 특정 유저 경험 추가
    """
    if session.get('is_admin'):
        target_user_id = request.args.get('user_id', type=int)
        if not target_user_id:
            flash("어느 유저의 경험인지 user_id가 필요합니다.", "warning")
            return redirect(url_for('admin_user_list'))
    else:
        target_user_id = session.get('user_id')

    if request.method == "POST":
        conn = get_db_connection()
        if not conn:
            return "DB 연결 오류", 500
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO experience
                (user_id, category, title, description, start_date, end_date, skills, hours, link, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                target_user_id,
                request.form.get("category"),
                request.form.get("title"),
                request.form.get("description"),
                request.form.get("start_date") or None,
                request.form.get("end_date") or None,
                request.form.get("skills"),
                request.form.get("hours", 3),
                request.form.get("link"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for("index", user_id=target_user_id if session.get('is_admin') else None))
    return render_template("add.html", target_user_id=target_user_id)


@app.route("/experience/<int:exp_id>")
@login_required
def experience_detail(exp_id):
    conn = get_db_connection()
    if not conn:
        return "DB 연결 오류", 500
    cur = conn.cursor()

    if session.get('is_admin'):
        cur.execute("SELECT * FROM experience WHERE id = %s", (exp_id,))
    else:
        cur.execute(
            "SELECT * FROM experience WHERE id = %s AND user_id = %s",
            (exp_id, session.get('user_id')),
        )

    exp = cur.fetchone()
    cur.close()
    conn.close()
    if not exp:
        abort(404)
    return render_template("experience_detail.html", exp=exp)


@app.route("/edit/<int:exp_id>", methods=["GET", "POST"])
@login_required
def edit(exp_id):
    conn = get_db_connection()
    if not conn:
        return "DB 연결 오류", 500
    cur = conn.cursor()

    if session.get('is_admin'):
        cur.execute("SELECT * FROM experience WHERE id = %s", (exp_id,))
    else:
        cur.execute(
            "SELECT * FROM experience WHERE id = %s AND user_id = %s",
            (exp_id, session.get('user_id')),
        )
    exp = cur.fetchone()
    if not exp:
        cur.close()
        conn.close()
        abort(404)

    if request.method == "POST":
        cur.execute(
            """
            UPDATE experience
            SET category=%s, title=%s, description=%s, start_date=%s, end_date=%s, hours=%s, skills=%s, link=%s
            WHERE id=%s
            """,
            (
                request.form.get("category"),
                request.form.get("title"),
                request.form.get("description"),
                request.form.get("start_date"),
                request.form.get("end_date") or None,
                request.form.get("hours"),
                request.form.get("skills"),
                request.form.get("link"),
                exp_id,
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('experience_detail', exp_id=exp_id))

    cur.close()
    conn.close()
    return render_template("add.html", exp=exp, is_edit=True)


@app.route("/delete/<int:exp_id>")
@login_required
def delete(exp_id):
    conn = get_db_connection()
    if not conn:
        return "DB 연결 오류", 500
    cur = conn.cursor()

    if session.get('is_admin'):
        cur.execute("DELETE FROM experience WHERE id=%s", (exp_id,))
    else:
        cur.execute(
            "DELETE FROM experience WHERE id=%s AND user_id=%s",
            (exp_id, session.get('user_id')),
        )

    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('index'))


# =========================
# 6. AI 분석 및 도구
# =========================

@app.route("/analyze")
@login_required
def analyze():
    """
    전체 포트폴리오 종합 분석 (유저별 / 관리자 user_id 지정 가능)
    """
    if session.get('is_admin'):
        target_user_id = request.args.get('user_id', type=int)
    else:
        target_user_id = session.get('user_id')

    exps = fetch_all_experiences(order_by_recent=False, user_id=target_user_id)
    profile = get_profile(target_user_id)

    if not exps:
        return render_template(
            "analyze.html",
            experiences=[],
            ai_result="<p>활동을 먼저 등록해주세요.</p>",
            target_user_id=target_user_id,
        )

    portfolio_text = build_portfolio_text(exps)
    prompt = f"""
    [사용자 정보] 이름: {profile.get('name')}, 전공: {profile.get('major')}, 목표: {profile.get('career_goal')}
    [활동 목록] {portfolio_text}

    위 정보를 바탕으로 포트폴리오의 일관성, 강점 3가지, 보완해야 할 점을 분석해주세요.
    """
    ai_result = call_groq(prompt, "너는 날카로운 커리어 코치다.")
    return render_template(
        "analyze.html",
        experiences=exps,
        ai_result=ai_result,
        target_user_id=target_user_id,
    )


@app.route('/career', methods=['GET', 'POST'])
@login_required
def career():
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

    return render_template(
        'career.html',
        majors=MAJORS,
        result=result,
        sel_major=selected_major,
        sel_company=selected_company,
        company_options=COMPANY_OPTIONS,
    )


@app.route("/company_analyze", methods=["GET", "POST"])
@login_required
def company_analyze():
    if session.get('is_admin'):
        target_user_id = request.args.get('user_id', type=int)
    else:
        target_user_id = session.get('user_id')

    exps = fetch_all_experiences(user_id=target_user_id)
    ai_result = None
    target_company = request.form.get("company")
    target_role = request.form.get("job")
    profile = get_profile(target_user_id)

    if request.method == "POST" and target_company:
        portfolio_text = build_portfolio_text(exps)
        search_context = get_google_search_context(f"{target_company} {target_role} 직무 기술서 핵심 역량")

        prompt = f"""
        [Web Data] {search_context}
        [Profile] 전공: {profile.get('major')}, 활동: {portfolio_text}

        지원자의 경험이 해당 직무 JD와 얼마나 일치하는지, 부족한 점은 무엇인지, 합격 확률(%)은 얼마인지 분석해줘.
        """
        ai_result = call_groq(prompt, "너는 냉철한 인사 담당자다.")

    return render_template(
        "company_analyze.html",
        company_options=COMPANY_OPTIONS,
        ai_result=ai_result,
        target_company=target_company,
        target_role=target_role,
        target_user_id=target_user_id,
    )


@app.route("/resume", methods=["GET", "POST"])
@login_required
def resume():
    if session.get('is_admin'):
        target_user_id = request.args.get('user_id', type=int)
    else:
        target_user_id = session.get('user_id')

    exps = fetch_all_experiences(order_by_recent=False, user_id=target_user_id)
    resume_text = None
    target_company = request.form.get("company")
    target_role = request.form.get("job")
    profile = get_profile(target_user_id)

    if request.method == "POST":
        portfolio_text = build_portfolio_text(exps)
        prompt = f"""
        [Target] 회사: {target_company}, 직무: {target_role}
        [User] {profile}
        [Experience] {portfolio_text}

        위 내용을 바탕으로 성과를 수치화하고 전문 용어를 사용하여 이력서 초안을 작성해줘.
        """
        resume_text = call_groq(prompt, "너는 전문 이력서 에디터다.")

    return render_template(
        "resume.html",
        experiences=exps,
        resume_text=resume_text,
        company_options=COMPANY_OPTIONS,
        target_company=target_company,
        target_role=target_role,
        target_user_id=target_user_id,
    )


@app.route("/cover_letter", methods=["GET", "POST"])
@login_required
def cover_letter():
    if session.get('is_admin'):
        target_user_id = request.args.get('user_id', type=int)
    else:
        target_user_id = session.get('user_id')

    exps = fetch_all_experiences(order_by_recent=False, user_id=target_user_id)
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

    return render_template(
        "cover_letter.html",
        experiences=exps,
        letter_text=letter_text,
        company_options=COMPANY_OPTIONS,
        target_company=target_company,
        target_role=target_role,
        target_user_id=target_user_id,
    )


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    """
    유저: 자기 프로필 설정
    관리자: ?user_id=3 으로 해당 유저 프로필 수정
    """
    if session.get('is_admin'):
        target_user_id = request.args.get('user_id', type=int)
        if not target_user_id:
            flash("어느 유저의 설정인지 user_id가 필요합니다.", "warning")
            return redirect(url_for('admin_user_list'))
    else:
        target_user_id = session.get('user_id')

    conn = get_db_connection()
    if not conn:
        return "DB 연결 오류", 500
    cur = conn.cursor()

    if request.method == "POST":
        cur.execute(
            """
            UPDATE profile
            SET name=%s, major=%s, career_goal=%s, strengths=%s, ai_instructions=%s
            WHERE user_id=%s
            """,
            (
                request.form.get("name"),
                request.form.get("major"),
                request.form.get("career_goal"),
                request.form.get("strengths"),
                request.form.get("ai_instructions"),
                target_user_id,
            ),
        )
        conn.commit()
        flash("설정이 저장되었습니다.", "success")

    cur.execute("SELECT * FROM profile WHERE user_id=%s", (target_user_id,))
    profile = cur.fetchone()
    cur.close()
    conn.close()
    return render_template(
        "settings.html",
        profile=profile or {},
        majors=MAJORS,
        target_user_id=target_user_id,
    )


# =========================
# 7. 데이터 백업/복구
# =========================

@app.route("/backup")
@login_required
def backup_page():
    return render_template("backup.html")


@app.route("/api/export")
@login_required
def export_data():
    if session.get('is_admin'):
        target_user_id = request.args.get('user_id', type=int)
    else:
        target_user_id = session.get('user_id')

    exps = fetch_all_experiences(order_by_recent=False, user_id=target_user_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['category', 'title', 'description', 'start_date', 'end_date', 'skills', 'hours', 'link'])
    for r in exps:
        writer.writerow([
            r['category'],
            r['title'],
            r['description'],
            r['start_date'],
            r['end_date'],
            r['skills'],
            r['hours'],
            r.get('link', '')
        ])
    output.seek(0)
    filename = f"portfolio_backup_user_{target_user_id or 'all'}.csv"
    return Response(
        output.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )


@app.route("/api/import", methods=["POST"])
@login_required
def import_data():
    if 'file' not in request.files:
        return "파일 없음", 400
    file = request.files['file']
    if file.filename == '':
        return "파일 선택 안함", 400

    if session.get('is_admin'):
        target_user_id = request.args.get('user_id', type=int)
        if not target_user_id:
            flash("어느 유저의 데이터인지 user_id가 필요합니다.", "warning")
            return redirect(url_for('admin_user_list'))
    else:
        target_user_id = session.get('user_id')

    try:
        stream = io.TextIOWrapper(file.stream, encoding='utf-8-sig')
        csv_input = csv.DictReader(stream)
        conn = get_db_connection()
        if not conn:
            return "DB 연결 오류", 500
        cur = conn.cursor()
        cnt = 0
        for row in csv_input:
            cur.execute(
                """
                INSERT INTO experience
                    (user_id, category, title, description, start_date, end_date, skills, hours, link, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    target_user_id,
                    row.get('category'),
                    row.get('title'),
                    row.get('description'),
                    row.get('start_date'),
                    row.get('end_date') or None,
                    row.get('skills'),
                    int(row.get('hours', 0) or 0),
                    row.get('link', ''),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            cnt += 1
        conn.commit()
        cur.close()
        conn.close()
        flash(f"{cnt}개의 데이터가 복구되었습니다.", "success")
        return redirect(url_for('index', user_id=target_user_id if session.get('is_admin') else None))
    except Exception as e:
        return f"복구 실패: {str(e)}", 500


@app.route("/admin/user_profile")
@admin_required
def admin_user_profile():
    target_user_id = request.args.get("user_id", type=int)
    if not target_user_id:
        flash("user_id가 필요합니다.", "warning")
        return redirect(url_for("admin_user_list"))

    conn = get_db_connection()
    if not conn:
        return "DB 연결 오류", 500
    cur = conn.cursor()
    cur.execute("SELECT id, email, created_at FROM users WHERE id = %s", (target_user_id,))
    user_info = cur.fetchone()

    cur.execute("SELECT * FROM profile WHERE user_id = %s", (target_user_id,))
    profile = cur.fetchone()

    cur.execute(
        "SELECT * FROM experience WHERE user_id = %s ORDER BY start_date DESC NULLS LAST",
        (target_user_id,)
    )
    experiences = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "admin_user_profile.html",
        user_info=user_info,
        profile=profile,
        experiences=experiences
    )


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    if not conn:
        return "DB 연결 오류", 500
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS cnt FROM users")
    total_users = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(*) AS cnt FROM experience")
    total_experiences = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(DISTINCT user_id) AS cnt FROM experience")
    active_users = cur.fetchone()["cnt"]

    cur.execute("""
        SELECT COALESCE(major, '미등록') AS major, COUNT(*) AS cnt
        FROM profile
        GROUP BY major
        ORDER BY cnt DESC
        LIMIT 5
    """)
    majors = cur.fetchall()

    cur.execute("""
        SELECT id, email, created_at
        FROM users
        ORDER BY created_at DESC NULLS LAST
        LIMIT 5
    """)
    recent_users = cur.fetchall()

    cur.execute("""
        SELECT u.id, u.email, COUNT(e.*) AS exp_count
        FROM users u
        LEFT JOIN experience e ON u.id = e.user_id
        GROUP BY u.id, u.email
        ORDER BY exp_count DESC, u.id
        LIMIT 5
    """)
    top_users = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "admin_dashboard.html",
        total_users=total_users,
        total_experiences=total_experiences,
        active_users=active_users,
        majors=majors,
        recent_users=recent_users,
        top_users=top_users
    )

from urllib.parse import urlencode
import requests

def social_login_process(email: str):
    """
    공통 소셜 로그인 처리:
    - email 기준으로 users에 없으면 자동 가입
    - profile도 같이 생성
    - 세션에 로그인 상태 저장
    """
    conn = get_db_connection()
    if not conn:
        return "DB 연결 오류", 500
    cur = conn.cursor()

    # 1) 기존 사용자 조회
    cur.execute("SELECT * FROM users WHERE email = %s", (email,))
    user = cur.fetchone()

    # 2) 없으면 신규 생성
    if not user:
        cur.execute(
            """
            INSERT INTO users (email, password_hash, created_at)
            VALUES (%s, %s, %s)
            RETURNING id;
            """,
            (
                email,
                "",  # 소셜 로그인은 비밀번호 없음
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        user_id = cur.fetchone()["id"]

        # profile 기본 레코드 생성
        cur.execute("INSERT INTO profile (user_id) VALUES (%s)", (user_id,))
        conn.commit()
    else:
        user_id = user["id"]

    cur.close()
    conn.close()

    # 3) 세션 로그인 처리
    session["logged_in"] = True
    session["is_admin"] = False
    session["user_id"] = user_id

    flash("소셜 계정으로 로그인되었습니다.", "success")
    return redirect(url_for("index"))

# =========================
# 네이버 로그인
# =========================

# ================================
# 네이버 로그인 (OAuth)
# ================================
import requests
import os
from flask import request, redirect, session, flash

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
NAVER_REDIRECT_URI = os.getenv("NAVER_REDIRECT_URI")

@app.route("/login/naver")
def naver_login():
    base = "https://nid.naver.com/oauth2.0/authorize"
    params = (
        f"?response_type=code"
        f"&client_id={NAVER_CLIENT_ID}"
        f"&redirect_uri={NAVER_REDIRECT_URI}"
        f"&state=naver1234"
    )
    return redirect(base + params)


@app.route("/oauth/naver/callback")
def naver_callback():
    code = request.args.get("code")
    state = request.args.get("state")

    # 토큰 요청
    token_url = "https://nid.naver.com/oauth2.0/token"
    data = {
        "grant_type": "authorization_code",
        "client_id": NAVER_CLIENT_ID,
        "client_secret": NAVER_CLIENT_SECRET,
        "code": code,
        "state": state,
    }
    token_res = requests.post(token_url, data=data).json()

    if "access_token" not in token_res:
        return f"네이버 토큰 오류 발생: {token_res}"

    access_token = token_res["access_token"]

    # 사용자 정보 요청
    user_info = requests.get(
        "https://openapi.naver.com/v1/nid/me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    if user_info["resultcode"] != "00":
        return f"네이버 사용자 정보 오류: {user_info}"

    profile = user_info["response"]
    provider_id = profile["id"]
    email = profile.get("email", None)
    name = profile.get("name", "네이버사용자")

    # DB 처리 (이미 있으면 로그인, 없으면 생성)
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE provider='naver' AND provider_id=%s", (provider_id,))
    existing = cur.fetchone()

    if existing:
        session["user"] = {
            "id": existing[0],
            "email": email,
            "name": name,
            "provider": "naver"
        }
    else:
        cur.execute(
            "INSERT INTO users (email, name, provider, provider_id) VALUES (%s, %s, %s, %s)",
            (email, name, "naver", provider_id)
        )
        conn.commit()

        cur.execute("SELECT id FROM users WHERE provider='naver' AND provider_id=%s", (provider_id,))
        new_user = cur.fetchone()

        session["user"] = {
            "id": new_user[0],
            "email": email,
            "name": name,
            "provider": "naver"
        }

    cur.close()
    return redirect(url_for("index"))





# ----- 상단 설정 근처에 추가 -----
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI",
    "https://portfolio-assistant-9jo3.onrender.com/oauth/google/callback",
)

# =========================
# 구글 로그인
# =========================

@app.route("/login/google")
def google_login():
    """
    구글 로그인 페이지로 이동
    """
    query = urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
    })
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + query)


@app.route("/oauth/google/callback")
def google_callback():
    """
    구글 로그인 콜백
    """
    code = request.args.get("code")

    # 1) 토큰 교환
    token_res = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ).json()

    access_token = token_res.get("access_token")
    if not access_token:
        return "구글 토큰 발급 오류: " + str(token_res), 500

    # 2) 사용자 정보 조회
    user_info = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    google_id = user_info.get("sub")
    email = user_info.get("email")

    if not email:
        email = f"google_user_{google_id}@noemail.com"

    # 3) DB에 자동 가입 + 로그인 처리
    conn = get_db_connection()
    if not conn:
        return "DB 연결 오류", 500
    cur = conn.cursor()

    # 기존 유저 있는지 확인
    cur.execute(
        "SELECT * FROM users WHERE provider=%s AND provider_id=%s",
        ("google", str(google_id)),
    )
    user = cur.fetchone()

    if not user:
        # 새 유저 생성
        cur.execute("""
            INSERT INTO users (email, password_hash, created_at, provider, provider_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id;
        """, (
            email,
            "",  # 소셜 로그인이라 비밀번호 없음
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "google",
            str(google_id),
        ))
        new_id = cur.fetchone()["id"]
        # 기본 프로필도 같이 생성
        cur.execute("INSERT INTO profile (user_id) VALUES (%s)", (new_id,))
        user_id = new_id
        conn.commit()
    else:
        user_id = user["id"]

    cur.close()
    conn.close()

    # 4) 세션 로그인 처리
    session["logged_in"] = True
    session["is_admin"] = False
    session["user_id"] = user_id

    flash("구글 계정으로 로그인되었습니다.", "success")
    return redirect(url_for("index"))


# =========================
# 카카오 로그인
# =========================


# =========================
# 카카오 로그인
# =========================

@app.route("/login/kakao")
def kakao_login():
    """
    카카오 로그인 페이지로 이동
    """
    redirect_uri = os.getenv("KAKAO_REDIRECT_URI")
    kakao_auth_url = (
        "https://kauth.kakao.com/oauth/authorize"
        f"?client_id={os.getenv('KAKAO_CLIENT_ID')}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
    )
    return redirect(kakao_auth_url)


@app.route("/oauth/kakao/callback")
def kakao_callback():
    """
    카카오 로그인 콜백
    """
    code = request.args.get("code")
    redirect_uri = os.getenv("KAKAO_REDIRECT_URI")

    # 1) Access Token 발급
    token_res = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": os.getenv("KAKAO_CLIENT_ID"),
            "client_secret": os.getenv("KAKAO_SECRET_KEY"),
            "redirect_uri": redirect_uri,
            "code": code,
        },
        headers={"Content-type": "application/x-www-form-urlencoded"},
    ).json()

    access_token = token_res.get("access_token")
    if not access_token:
        return f"카카오 토큰 발급 오류: {token_res}"

    # 2) 사용자 정보 요청
    user_res = requests.get(
        "https://kapi.kakao.com/v2/user/me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    kakao_id = str(user_res["id"])
    account = user_res.get("kakao_account", {})
    email = account.get("email", f"kakao_user_{kakao_id}@noemail.com")

    # 3) DB 저장 (provider, provider_id 포함)
    conn = get_db_connection()
    cur = conn.cursor()

    # 기존 유저 확인
    cur.execute(
        "SELECT * FROM users WHERE provider=%s AND provider_id=%s",
        ("kakao", kakao_id),
    )
    user = cur.fetchone()

    if not user:
        # 신규 생성
        cur.execute(
            """
            INSERT INTO users (email, password_hash, created_at, provider, provider_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (
                email,
                "",  # 카카오 로그인은 비밀번호 없음
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "kakao",
                kakao_id
            )
        )
        user_id = cur.fetchone()["id"]
        cur.execute("INSERT INTO profile (user_id) VALUES (%s)", (user_id,))
        conn.commit()
    else:
        user_id = user["id"]

    cur.close()
    conn.close()

    # 4) 세션 설정
    session["logged_in"] = True
    session["is_admin"] = False
    session["user_id"] = user_id

    flash("카카오 계정으로 로그인되었습니다.", "success")
    return redirect(url_for("index"))



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
