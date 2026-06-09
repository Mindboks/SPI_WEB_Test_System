#2026-4-11~2026-6-7 version2.9.5final-renovation.Version0.0.7

# -*- coding: utf-8 -*-
import os
import csv
import io
import json
import hashlib
import re
import psycopg2

from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify


# ========== Gemini設定 ==========
# Gemini設定
GEMINI_AVAILABLE = False
gemini_model = None

try:
    import google.generativeai as genai
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        # 有料プラン対応モデル
        gemini_model = genai.GenerativeModel('models/gemini-2.5-pro')
        GEMINI_AVAILABLE = True
        print(f"【Gemini】有効化されました (APIキー長: {len(GEMINI_API_KEY)})")
    else:
        print("【Gemini】APIキーが設定されていません")
except ImportError:
    print("【Gemini】パッケージがインストールされていません")
except Exception as e:
    print(f"【Gemini】初期化エラー: {e}")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ========== Flaskアプリ設定 ==========
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default_secret_key_for_production')
DATABASE_URL = os.environ.get('DATABASE_URL')

# アプリケーションの設定
app.config.update(
    SESSION_COOKIE_SECURE=True,  # HTTPSなのでTrue
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=1800,
    SESSION_COOKIE_PERMANENT=False,  # ブラウザを閉じたらCookie削除
)

# ========== データベース接続関数 ==========
def get_db():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise ValueError("DATABASE_URLが設定されていません")   
    conn = psycopg2.connect(db_url)
    return conn

def hash_password(password):
    return hashlib.sha256((password or "").encode('utf-8')).hexdigest()

def init_db():
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, 
                    name TEXT, 
                    class_id TEXT, 
                    password TEXT, 
                    role TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tests (
                    id SERIAL PRIMARY KEY, 
                    name TEXT, 
                    target_class TEXT, 
                    duration INTEGER DEFAULT 30
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS questions (
                    id SERIAL PRIMARY KEY, 
                    test_id INTEGER, 
                    q_no INTEGER, 
                    category TEXT, 
                    question TEXT, 
                    target TEXT,
                    a1 TEXT, a2 TEXT, a3 TEXT, a4 TEXT, a5 TEXT, 
                    a6 TEXT, a7 TEXT, a8 TEXT, a9 TEXT, a10 TEXT, 
                    answer TEXT, 
                    explanation TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS results (
                    id SERIAL PRIMARY KEY, 
                    test_id INTEGER, 
                    user_id TEXT, 
                    score INTEGER, 
                    details TEXT, 
                    comment TEXT, 
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            print("【初期化】すべてのテーブルの確認・作成が完了しました")
    except Exception as e:
        print(f"【エラー】初期化中にエラーが発生しました: {e}")
        raise
    finally:
        if conn:
            conn.close()

# ========== コメント生成関数 ==========
def generate_ai_comment(score, details_data):
    """フォールバック用の従来型コメント生成"""
    labels = details_data.get('labels', [])
    scores = details_data.get('scores', [])
    
    if score >= 90:
        level = "優秀"
        message = "非常に高いレベルです。素晴らしい結果です！"
    elif score >= 75:
        level = "良好"
        message = "安定した実力があります。"
    elif score >= 52:
        level = "合格"
        message = "合格ラインです。さらなる向上を目指しましょう。"
    elif score >= 40:
        level = "要改善"
        message = "基礎的な部分の復習が必要です。"
    else:
        level = "要注意"
        message = "学習方法の見直しが必要です。"
    
    strengths = [labels[i] for i in range(len(labels)) if i < len(scores) and scores[i] >= 70]
    weaknesses = [labels[i] for i in range(len(labels)) if i < len(scores) and scores[i] <= 40]
    
    comment = f"【総合評価: {level}】\n{message}\n\n"
    
    if strengths:
        comment += f"【強み】\n{', '.join(strengths)} の分野が得意です。\n\n"
    
    if weaknesses:
        comment += f"【改善ポイント】\n{', '.join(weaknesses)} の分野を復習しましょう。\n\n"
    
    if score >= 75:
        comment += "【アドバイス】\nこの調子で学習を続けてください。"
    elif score >= 60:
        comment += "【アドバイス】\n弱点分野を集中的に学習しましょう。"
    else:
        comment += "【アドバイス】\n基礎問題を繰り返し解くことから始めましょう。"
    
    return comment

def generate_ai_comment_with_gemini(score, details_data, student_name, test_name):
    """Geminiコメント生成（利用可能な場合のみ）"""
    
    if not GEMINI_AVAILABLE or not gemini_model:
        return generate_ai_comment(score, details_data)
    
    labels = details_data.get('labels', [])
    scores = details_data.get('scores', [])
    
    if not labels or not scores:
        return generate_ai_comment(score, details_data)
    
    avg_score = sum(scores) / len(scores) if scores else 0
    max_category = labels[scores.index(max(scores))] if scores else "なし"
    min_category = labels[scores.index(min(scores))] if scores else "なし"
    
    category_results = "\n".join([f"- {labels[i]}: {scores[i]}%" for i in range(len(labels)) if i < len(scores)])
    
    prompt = f"""
あなたは経験豊富な教育カウンセラーです。以下の学生の試験結果を分析し、温かみのある励ましと具体的なアドバイスを日本語で提供してください。

【学生情報】
名前: {student_name}
受験した試験: {test_name}

【成績データ】
総合得点: {score}点 / 100点
カテゴリー別正解率:
{category_results}

【統計情報】
平均正解率: {avg_score:.1f}%
最も得意な分野: {max_category}
最も苦手な分野: {min_category}

【出力形式】
以下の4つのセクションに分けて回答してください：

💡 **総合評価**
⭐ **強み**
📚 **改善ポイント**
🎯 **次のステップ**
"""
    
    try:
        response = gemini_model.generate_content(prompt)
        comment = response.text
        # 先頭の空白や改行を削除
        comment = comment.lstrip('\n\r ')
        return comment
    except Exception as e:
        print(f"【Geminiエラー】: {e}")
        return generate_ai_comment(score, details_data)

# ========== リクエスト前処理 ==========
@app.before_request
def before_request():
    if request.path.startswith('/static'):
        return
    
    # '/' を除外、APIルートを追加
    public_paths = ['/login', '/logout', '/register', '/password_reset']
    if request.path in public_paths or request.path.startswith('/api/'):
        return
    
    if 'user_id' not in session:
        return redirect(url_for('login'))

     # 悪意のあるパスをブロック
    malicious_paths = [
        'wp-admin', 'wp-includes', 'wp-content', 'xmlrpc.php',
        'wlwmanifest.xml', 'install.php', 'wordpress', 'wp-json'
    ]
    
    for path in malicious_paths:
        if path in request.path.lower():
            # 攻撃者にサイトが存在しないことを伝える
            return "Not Found", 404
    
    # 静的ファイルはスキップ
    if request.path.startswith('/static'):
        return
    
    # 認証不要なパス
    public_paths = ['/', '/login', '/logout', '/register', '/password_reset']
    if request.path in public_paths:
        return
    
    # セッションがない場合はログイン画面へ
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    if session.get('current_test_id') and session.permanent:
        session.modified = True   
# ========== ルーティング ==========
@app.route('/')
def index():
    session.clear()  # 常にセッションクリア
    return redirect(url_for('login'))
    if session.get('role') == 'teacher':
        return redirect(url_for('teacher_admin'))
    return redirect(url_for('student_dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        session.clear()
    
    if request.method == 'POST':
        u_id = request.form.get('id', '').strip()
        pwd = request.form.get('password')
        hashed_pwd = hash_password(pwd)
        
        try:
            conn = get_db()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute('SELECT * FROM users WHERE id = %s', (u_id,))
            user = cur.fetchone()
            cur.close()
            conn.close()
            
            if user and user['password'] == hashed_pwd:
                session.clear()
                session.update({
                    'user_id': user['id'], 
                    'user_name': user['name'], 
                    'role': user['role'],
                    'class_id': user.get('class_id') 
                })
                if user['role'] == 'teacher':
                    return redirect(url_for('teacher_admin'))
                else:
                    return redirect(url_for('student_dashboard'))
            else:
                flash("IDまたはパスワードが間違っています")
        except Exception as e:
            print(f"ログインエラー: {e}")
            flash("システムエラーが発生しました")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u_id = request.form.get('id', '')
        pwd = request.form.get('password', '')

        if not u_id or not pwd:
            flash("IDとパスワードを入力してください。")
            return render_template('register.html')
        
        if len(u_id) > 20 or len(pwd) > 50:
            flash("入力内容が長すぎます。")
            return render_template('register.html')

        hashed_pwd = hash_password(pwd)
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute('UPDATE users SET password = %s WHERE id = %s', (hashed_pwd, u_id))
            if cur.rowcount == 0:
                flash("IDが見つかりません。")
            else:
                conn.commit()
                flash("登録が完了しました。")
            cur.close()
            conn.close()
        except Exception as e:
            flash("システムエラーが発生しました。")
    return render_template('register.html')

@app.route('/password_reset', methods=['GET', 'POST'])
def password_reset():
    if request.method == 'POST':
        u_id = request.form.get('id', '').strip()
        pwd = request.form.get('password', '')
        
        if not u_id or not pwd:
            return jsonify({'success': False, 'message': 'IDとパスワードを入力してください。'})
        
        hashed_pwd = hash_password(pwd)
        
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute('UPDATE users SET password = %s WHERE id = %s', (hashed_pwd, u_id))
            
            if cur.rowcount == 0:
                # IDが見つからない場合
                cur.close()
                conn.close()
                return jsonify({
                    'success': False, 
                    'not_found': True,
                    'message': '入力されたIDは登録されていません。'
                })
            else:
                conn.commit()
                cur.close()
                conn.close()
                return jsonify({
                    'success': True, 
                    'message': 'パスワードを再設定しました。ログインしてください。'
                })
        except Exception as e:
            print(f"パスワードリセットエラー: {e}")
            return jsonify({'success': False, 'message': 'システムエラーが発生しました。'})
    
    return render_template('password_reset.html')

@app.route('/teacher/admin', methods=['GET', 'POST'])
def teacher_admin():
    if session.get('role') != 'teacher': 
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        t_name = request.form.get('test_name')
        t_class = request.form.get('target_class')
        t_duration = request.form.get('duration', 30)
        file = request.files.get('test_csv')

        if t_name and t_class and file:
            conn = None
            try:
                conn = get_db()
                cur = conn.cursor()
                
                cur.execute('''
                    INSERT INTO tests (name, target_class, duration) 
                    VALUES (%s, %s, %s) RETURNING id
                ''', (t_name, t_class, t_duration))
                new_test_id = cur.fetchone()[0]
                
                cur.execute("SELECT COALESCE(MAX(id), 0) FROM questions")
                max_q_id = cur.fetchone()[0]
                next_q_id = max_q_id + 1
                
                file_content = file.read()
                
                try:
                    decoded_content = file_content.decode('utf-8-sig')
                except UnicodeDecodeError:
                    try:
                        decoded_content = file_content.decode('cp932')
                    except UnicodeDecodeError:
                        decoded_content = file_content.decode('latin-1')
                
                stream = io.StringIO(decoded_content)
                reader = csv.DictReader(stream)
                
                def find_column(reader, patterns):
                    # 完全一致を優先
                    for pattern in patterns:
                        for col in reader.fieldnames:
                            if col is None:
                                continue
                            if pattern.lower() == col.lower():
                                return col
                    # 部分一致は後回し
                    for pattern in patterns:
                        for col in reader.fieldnames:
                            if col is None:
                                continue
                            if pattern.lower() in col.lower():
                                return col
                    return None

                q_no_col = find_column(reader, ['test_number'])
                category_col = find_column(reader, ['test genre'])
                question_col = find_column(reader, ['test questions'])
                target_col = find_column(reader, ['target'])
                answer_col = find_column(reader, ['Answer'])
                explanation_col = find_column(reader, ['Test explanation'])

                choice_columns = []
                for i in range(1, 11):
                    found = find_column(reader, [f'Answer_{i}'])
                    choice_columns.append(found)
                                
                inserted_count = 0
                skipped_count = 0
                
                for row in reader:
                    q_no_raw = ''
                    if q_no_col:
                        q_no_raw = row.get(q_no_col, '').strip()
                    
                    if not q_no_raw or q_no_raw == 'end' or q_no_raw == '序号':
                        skipped_count += 1
                        continue
                    
                    try:
                        q_no_int = int(float(q_no_raw))
                    except (ValueError, TypeError):
                        skipped_count += 1
                        continue
                    
                    a1 = row.get(choice_columns[0], '').strip() if choice_columns[0] else ''
                    a2 = row.get(choice_columns[1], '').strip() if choice_columns[1] else ''
                    a3 = row.get(choice_columns[2], '').strip() if choice_columns[2] else ''
                    a4 = row.get(choice_columns[3], '').strip() if choice_columns[3] else ''
                    a5 = row.get(choice_columns[4], '').strip() if choice_columns[4] else ''
                    a6 = row.get(choice_columns[5], '').strip() if choice_columns[5] else ''
                    a7 = row.get(choice_columns[6], '').strip() if choice_columns[6] else ''
                    a8 = row.get(choice_columns[7], '').strip() if choice_columns[7] else ''
                    a9 = row.get(choice_columns[8], '').strip() if choice_columns[8] else ''
                    a10 = row.get(choice_columns[9], '').strip() if choice_columns[9] else ''
                    
                    answer_val = row.get(answer_col, '').strip() if answer_col else ''
                    explanation = row.get(explanation_col, '').strip() if explanation_col else ''
                    category = row.get(category_col, '').strip() if category_col else ''
                    question = row.get(question_col, '').strip() if question_col else ''
                    target = row.get(target_col, '').strip() if target_col else ''
                    
                    def null_to_empty(val):
                        return val if val is not None else ''
                    
                    values = (
                        new_test_id, q_no_int,
                        null_to_empty(category), null_to_empty(question), null_to_empty(target),
                        null_to_empty(a1), null_to_empty(a2), null_to_empty(a3), null_to_empty(a4), null_to_empty(a5),
                        null_to_empty(a6), null_to_empty(a7), null_to_empty(a8), null_to_empty(a9), null_to_empty(a10),
                        null_to_empty(answer_val), null_to_empty(explanation)
                    )

                    cur.execute('''
                        INSERT INTO questions (
                            test_id, q_no, category, question, target, 
                            a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, 
                            answer, explanation
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', values)
                    inserted_count += 1
                
                conn.commit()
                cur.close()
                conn.close()
                flash(f'「{t_name}」を正常に登録しました。（{inserted_count}問登録 ）')
                
            except Exception as e:
                if conn:
                    conn.rollback()
                    conn.close()
                flash(f'CSV登録エラー: {str(e)}')
             #   import traceback 削除対象
             #   traceback.print_exc()
        
        return redirect(url_for('teacher_admin'))

    tests, results, classes = [], [], []
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT DISTINCT class_id FROM users WHERE class_id IS NOT NULL AND class_id != 'teacher' ORDER BY class_id ASC")
        classes = [row['class_id'] for row in cur.fetchall()]
        cur.execute('SELECT * FROM tests ORDER BY id DESC')
        tests = cur.fetchall()
        cur.execute('''SELECT r.id, t.name AS test_name, u.class_id, u.id AS student_id, u.name AS student_name, r.score, r.timestamp 
                       FROM results r 
                       JOIN tests t ON r.test_id = t.id 
                       JOIN users u ON r.user_id = u.id 
                       ORDER BY r.timestamp DESC''')
        results = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"データ取得エラー: {e}")
        
    return render_template('admin.html', tests=tests, results=results, classes=classes)

@app.route('/teacher/delete_test/<int:test_id>', methods=['POST'])
def delete_test(test_id):
    if session.get('role') != 'teacher': 
        return redirect(url_for('index'))
    conn = get_db()
    cur = conn.cursor()
    cur.execute('DELETE FROM results WHERE test_id = %s', (test_id,))
    cur.execute('DELETE FROM questions WHERE test_id = %s', (test_id,))
    cur.execute('DELETE FROM tests WHERE id = %s', (test_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash('テストと関連するすべての結果を削除しました。')
    return redirect(url_for('teacher_admin'))

@app.route('/student_dashboard')
def student_dashboard():
    if session.get('role') != 'student': 
        return redirect(url_for('index'))
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT class_id FROM users WHERE id = %s', (session.get('user_id'),))
    user = cur.fetchone()
    class_id = user['class_id'] if user else None
    
    cur.execute('SELECT * FROM tests WHERE target_class = %s', (class_id,))
    tests = cur.fetchall()
    cur.execute('''
        SELECT r.id, t.name AS test_name, r.score, r.timestamp FROM results r
        JOIN tests t ON r.test_id = t.id WHERE r.user_id = %s ORDER BY r.timestamp DESC
    ''', (session.get('user_id'),))
    my_results = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('student_dashboard.html', tests=tests, my_results=my_results)

@app.route('/student/test/<int:test_id>/start', methods=['GET', 'POST'])
def take_test(test_id):
    if session.get('role') != 'student':
        flash("受験には学生アカウントでのログインが必要です。")
        return redirect(url_for('login'))
    
    session['answers'] = {}
    session['current_test_id'] = test_id
    
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT * FROM tests WHERE id = %s', (test_id,))
        test = cur.fetchone()
        
        cur.execute('SELECT COUNT(*) as total FROM questions WHERE test_id = %s', (test_id,))
        total_data = cur.fetchone()
        total_q = total_data['total'] if total_data else 0
        
        cur.close()
        conn.close()
        
        if not test:
            flash("選択したテストは見つかりませんでした。")
            return redirect(url_for('student_dashboard'))
        
        return render_template('test_page.html', test_id=test_id, test=test, total_q=total_q)
        
    except Exception as e:
        if conn: 
            conn.close()
        print(f"Error in take_test: {e}")
        flash("システムエラーが発生しました。")
        return redirect(url_for('student_dashboard'))

@app.route('/student/test/<int:test_id>/submit', methods=['GET', 'POST'])
def submit_test(test_id):
    if session.get('role') != 'student':
        return redirect(url_for('login'))
    
    user_answers = session.get('answers', {})
    
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 問題を取得（q_no順）
        cur.execute("SELECT id, q_no, category, answer FROM questions WHERE test_id = %s ORDER BY q_no", (test_id,))
        questions = cur.fetchall()
        
        total_q = len(questions)
        correct_count = 0
        genre_stats = {}
        
        for q in questions:
            q_no = str(q['q_no'])
            category = q['category'] or '未分類'
            
            if category not in genre_stats:
                genre_stats[category] = {'correct': 0, 'total': 0}
            genre_stats[category]['total'] += 1
            
            # ユーザーの回答を取得（キーは問題番号）
            user_answer = user_answers.get(q_no, '')
            correct_answer = str(q['answer']) if q['answer'] else ''
            
            # 回答が一致したら正解
            if user_answer and user_answer == correct_answer:
                correct_count += 1
                genre_stats[category]['correct'] += 1
        
        # 分析データ作成
        labels = list(genre_stats.keys())
        scores = []
        for g in labels:
            if genre_stats[g]['total'] > 0:
                score = int((genre_stats[g]['correct'] / genre_stats[g]['total']) * 100)
            else:
                score = 0
            scores.append(score)
        
        analysis = {
            "labels": labels,
            "scores": scores
        }
        
        final_score = int((correct_count / total_q) * 100) if total_q > 0 else 0
        
        #print(f"【デバッグ】正解数: {correct_count}/{total_q}")
        #print(f"【デバッグ】最終スコア: {final_score}")
        #print(f"【デバッグ】分析結果: {analysis}")
        
        # 結果を保存
        cur.execute('''
            INSERT INTO results (user_id, test_id, score, details, timestamp) 
            VALUES (%s, %s, %s, %s, NOW()) RETURNING id
        ''', (session.get('user_id'), test_id, final_score, json.dumps(analysis)))
        
        result_id = cur.fetchone()['id']
        conn.commit()
        cur.close()
        conn.close()
        
        session.pop('answers', None)
        session.pop('current_test_id', None)
        
        flash(f'試験を提出しました。得点: {final_score}点 / {total_q}問中{correct_count}問正解')
        
        return redirect(url_for('show_result', test_id=test_id, result_id=result_id))
        
    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        print(f"【エラー】submit_test: {e}")
        import traceback
        traceback.print_exc()
        flash("採点処理中にエラーが発生しました。")
        return redirect(url_for('student_dashboard'))


@app.route('/student/test/<int:test_id>/result/<int:result_id>')
def show_result(test_id, result_id):
    if session.get('role') != 'student':
        return redirect(url_for('login'))
    
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute('''
            SELECT r.*, u.name as student_name 
            FROM results r 
            JOIN users u ON r.user_id = u.id 
            WHERE r.id = %s AND r.user_id = %s
        ''', (result_id, session.get('user_id')))
        
        res = cur.fetchone()
        
        cur.execute('SELECT name FROM tests WHERE id = %s', (test_id,))
        test = cur.fetchone()
        test_name = test['name'] if test else "不明なテスト"
        
        cur.close()
        conn.close()
        
        if not res:
            flash("結果が見つかりません。")
            return redirect(url_for('student_dashboard'))

        details_data = json.loads(res['details']) if res.get('details') else {'labels': [], 'scores': []}
        
        ai_comment = generate_ai_comment_with_gemini(
            score=res['score'],
            details_data=details_data,
            student_name=res['student_name'],
            test_name=test_name
        )
        
        res = dict(res)  # RealDictRowを通常のdictに変換
        res['comment'] = ai_comment
        
        return render_template('result_page.html', res=res, details=details_data)

    except Exception as e:
        if conn:
            conn.close()
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        flash("結果の表示中にエラーが発生しました。")
        return redirect(url_for('student_dashboard'))

@app.route('/student/test/<int:test_id>/cheated', methods=['POST'])
def cheated_test(test_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('INSERT INTO results (test_id, user_id, score, comment) VALUES (%s, %s, %s, %s)',
                (test_id, session.get('user_id'), 0, "失格"))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'status': 'ok', 'redirect_url': url_for('student_dashboard')})

@app.route('/api/student/test/<int:test_id>/get_question/<int:q_no>', methods=['GET', 'POST'])
def api_get_question(test_id, q_no):
    if session.get('role') != 'student': 
        return jsonify({'error': 'Unauthorized'}), 401
    if 'answers' not in session: 
        session['answers'] = {}
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    if request.method == 'POST':
        data = request.get_json() or {}
        ans_dict = session['answers']
        if data.get('skip'):
            ans_dict[str(q_no)] = ""  # キーは文字列のq_no
            #print(f"【APIデバッグ】スキップ: q_no={q_no}")
        else:
            ans_dict[str(q_no)] = str(data.get('choice', ''))
            #print(f"【APIデバッグ】回答保存: q_no={q_no}, choice={data.get('choice', '')}")
        session['answers'] = ans_dict
        #print(f"【APIデバッグ】現在の回答状況: {session['answers']}")

    cur.execute('SELECT * FROM questions WHERE test_id = %s AND q_no = %s', (test_id, q_no))
    q = cur.fetchone()
    cur.execute('SELECT COUNT(*) as cnt FROM questions WHERE test_id = %s', (test_id,))
    total_q = cur.fetchone()['cnt']
    cur.close()
    conn.close()
    
    if not q:
        return jsonify({'error': 'Question not found'}), 404

    status_list = []
    for i in range(1, total_q + 1):
        status_list.append({
            'q_no': i,
            'is_answered': str(i) in session['answers'] and session['answers'][str(i)] != ""
        })

    return jsonify({
        'q_no': q['q_no'],
        'category': q['category'],
        'question': q['question'],
        'target': q.get('target', ''),
        'choices': {f'a{i}': q[f'a{i}'] for i in range(1, 11) if q.get(f'a{i}')},
        'current_answer': session['answers'].get(str(q_no), ''),
        'status_list': status_list
    })

@app.route('/student/test/<int:test_id>/abandon', methods=['POST'])
def abandon_test(test_id):
    try:
        session.pop('answers', None)
        session.pop('current_test_id', None)
        print(f"【セッション削除】ユーザー {session.get('user_id')} が試験 {test_id} を放棄しました")
        return jsonify({'status': 'ok', 'message': 'Session cleared'})
    except Exception as e:
        print(f"【エラー】abandon_test: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/student/test/<int:test_id>/check_session', methods=['GET'])
def check_test_session(test_id):
    if session.get('current_test_id') != test_id:
        return jsonify({'valid': False, 'redirect': url_for('login')})
    return jsonify({'valid': True})

# ========== 志望動機作成機能（留学生・やさしい日本語版） ==========

@app.route('/motivation_form')
def motivation_form():
    if session.get('role') != 'student':
        return redirect(url_for('login'))
    return render_template('motivation_form.html')


@app.route('/api/generate_motivation', methods=['POST'])
def generate_motivation():
    """AIで志望動機・趣味特技・自己PRを生成（やさしい日本語）"""
    if session.get('role') != 'student':
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    
    # 必須項目のバリデーション
    required_fields = [
        'company_name', 'desired_position', 'high_school_efforts',
        'current_part_time', 'part_time_description', 'has_leader_exp',
        'part_time_achievement', 'languages', 'certifications',
        'strengths', 'how_overcome', 'hobbies', 'career_plan'
    ]
    
    missing_fields = [f for f in required_fields if not data.get(f)]
    if missing_fields:
        return jsonify({'error': f'必須項目が不足しています: {missing_fields}'}), 400
    
    # プロンプトを作成
    prompt = create_easy_japanese_prompt(data)
    
    if GEMINI_AVAILABLE and gemini_model:
        try:
            response = gemini_model.generate_content(prompt)
            result = parse_ai_response(response.text)
            return jsonify(result)
        except Exception as e:
            print(f"【Geminiエラー】: {e}")
            return jsonify(get_sample_easy_motivation()), 200
    else:
        # Geminiがない場合はサンプルを返す
        return jsonify(get_sample_easy_motivation()), 200


def create_easy_japanese_prompt(data):
    """やさしい日本語用のプロンプトを作成（専門学生らしい品格ある表現版）"""
    
    # 大学状況の変換
    university_status_text = {
        'none': '行っていない',
        'dropout': '中退した',
        'graduate': '卒業した'
    }.get(data.get('university_status', 'none'), '行っていない')
    
    # リーダー経験の変換
    leader_exp_text = 'あり' if data.get('has_leader_exp') == 'yes' else 'なし'
    
    # アルバイト以外の仕事の処理
    if data.get('other_job_exists') == 'yes':
        other_job_text = f"あり（会社名: {data.get('other_job_company', '')}）"
    else:
        other_job_text = "なし"
    
    prompt = f"""
あなたは専門学校の学生が就職活動で使う「志望動機」「趣味・特技」「自己PR」を書くプロのライターです。
以下の学生の情報をもとに、**品格があり、説得力のある文章**を書いてください。

【学生の情報】
■ 基本情報
- 志望する会社: {data.get('company_name', '')}
- 志望する仕事: {data.get('desired_position', '')}

■ 学校生活
- 高校でがんばったこと: {data.get('high_school_efforts', '')}
- 大学の状況: {university_status_text}
- 大学の専攻: {data.get('major', '')}
- 大学でがんばったこと: {data.get('university_efforts', '')}

■ アルバイト・仕事
- 今のアルバイト: {data.get('current_part_time', '')}
- アルバイトの内容: {data.get('part_time_description', '')}
- アルバイト以外の仕事: {other_job_text}
- リーダー経験: {leader_exp_text}
- アルバイトでの成果: {data.get('part_time_achievement', '')}

■ スキル
- 話せる言語: {data.get('languages', '')}
- 資格: {data.get('certifications', '')}

■ 自分のこと
- 自分の強み: {data.get('strengths', '')}
- 自分の弱み: {data.get('weaknesses', '')}
- 弱みの克服方法: {data.get('how_overcome', '')}

■ 趣味・特技
- 趣味: {data.get('hobbies', '')}
- 趣味から学んだこと: {data.get('learned_from_hobby', '')}

■ 志望理由・将来
- 会社を選んだ理由: {data.get('motivation_reason', '')}
- 将来の夢: {data.get('career_plan', '')}
- アピールしたいこと: {data.get('appeal_points', '')}

【文章のルール】
1. 基本的な日本語の漢字は使用して構いません（例：私、学生、会社、将来、経験）
2. ただし、以下のような難しい言い回しは避けてください：
   - 弊社、貴社 → 「御社」
   - 〜させていただく → 使わない
   - 〜という認識でおります → 「〜だと思います」
   - 〜に対するアプローチ → 「〜への取り組み」
3. 1文は短く、明確に（体言止めは避ける）
4. 「です・ます」調で統一する
5. 具体的な数字やエピソードを入れると説得力が増す
6. 結論→理由→具体例→まとめの構造を意識する

【出力の長さ】
- 志望動機: 220〜270字
- 趣味・特技: 100〜130字
- 自己PR: 240〜290字

【出力形式】
【志望動機】
（本文）

【趣味・特技】
（本文）

【自己PR】
（本文）
"""
    return prompt


def parse_ai_response(text):
    """AIのレスポンスをパース"""
    result = {
        'motivation': '',
        'hobby': '',
        'self_pr': ''
    }
    
    # 「【志望動機】」で分割
    if '【志望動機】' in text:
        parts = text.split('【志望動機】')
        if len(parts) > 1:
            after = parts[1]
            if '【趣味・特技】' in after:
                motivation, rest = after.split('【趣味・特技】', 1)
                result['motivation'] = motivation.strip()
                if '【自己PR】' in rest:
                    hobby, self_pr = rest.split('【自己PR】', 1)
                    result['hobby'] = hobby.strip()
                    result['self_pr'] = self_pr.strip()
                else:
                    result['hobby'] = rest.strip()
            else:
                result['motivation'] = after.strip()
    elif '【志望理由】' in text:
        # 代替フォーマット
        parts = text.split('【志望理由】')
        if len(parts) > 1:
            after = parts[1]
            if '【自己PR】' in after:
                motivation, rest = after.split('【自己PR】', 1)
                result['motivation'] = motivation.strip()
                result['self_pr'] = rest.strip()
            else:
                result['motivation'] = after.strip()
    else:
        # パースに失敗したら全文を志望動機に
        result['motivation'] = text.strip()
    
    return result


def get_sample_easy_motivation():
    """サンプルデータ（Geminiがない場合・浦和専門学校版）"""
    return {
        'motivation': '''私はコンピューターの勉強をしたいと思い、浦和専門学校で学びました。
学校でプログラミングの基礎を勉強しました。

アルバイトでは、コンビニでお客さまのサポートをしていました。
そのとき、「わかりやすい説明」が大事だと学びました。

貴社は「お客さまにやさしい技術」を大切にしています。
私もこの考え方に共感しました。

私の「わかりやすく説明する力」を活かして、
お客さまに喜ばれるサービスを作りたいです。''',
        'hobby': '''私の趣味はアニメを見ることです。
アニメから、日本語の新しい表現を学びました。
この経験は、仕事でも役に立つと思います。
みんなが楽しく話せるように、コミュニケーションを大切にします。''',
        'self_pr': '''私の強みは「あきらめない心」です。

なぜなら、どんな難しいことでも続けることができるからです。

高校のとき、日本語がぜんぜん話せませんでした。
でも、毎日1時間勉強を続けました。
その結果、1年で日本語のテストに合格しました。

アルバイトでは、レジの仕事をがんばりました。
毎日「ありがとう」と笑顔で言うことを続けました。

この「あきらめない力」を会社でも活かします。
どんな仕事でも最後までやりとげます。'''
    }




# ========== サーバー起動 ==========
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)