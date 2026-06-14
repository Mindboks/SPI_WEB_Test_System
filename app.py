#2026-4-11~2026-6-7 version2.9.5final-renovation.Version0.3.2

# -*- coding: utf-8 -*-
import os
import csv
import io
import json
import hashlib
import re
import psycopg2
import threading
from collections import OrderedDict
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify


# ========== Gemini設定 ==========
GEMINI_AVAILABLE = False
gemini_model = None

try:
    import google.generativeai as genai

    # 修正前: ライブラリ任せ
    # genai.configure(api_key=GEMINI_API_KEY)

    # 修正後: コード側で環境変数を読み込む
    import os

    # 1. 環境変数から直接APIキーを取得 (GOOGLE_API_KEYを優先)
    API_KEY = os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY')

    if API_KEY:
        genai.configure(api_key=API_KEY)
        # モデル名も最新のものに修正
        model = genai.GenerativeModel('gemini-1.5-flash')
    else:
        print("エラー: APIキーが見つかりません")
        # フォールバック処理
except ImportError:
    print("【Gemini】パッケージがインストールされていません")
except Exception as e:
    print(f"【Gemini】初期化エラー: {e}")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ========== Flaskアプリ設定 ==========
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default_secret_key_for_production')
DATABASE_URL = os.environ.get('DATABASE_URL')

app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=1800,
    SESSION_COOKIE_PERMANENT=False,
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

def update_user_password(user_id, new_password):
    """パスワード更新の共通関数"""
    hashed_pwd = hash_password(new_password)
    conn = get_db()
    cur = conn.cursor()
    cur.execute('UPDATE users SET password = %s WHERE id = %s', (hashed_pwd, user_id))
    updated = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return updated

# ========== 高速化されたコメント生成関数 ==========
def generate_ai_comment(score, details_data):
    """フォールバック用の従来型コメント生成（高速）"""
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

# キャッシュ用のLRU辞書（最大100件）
class LRUCache:
    def __init__(self, maxsize=100):
        self.cache = OrderedDict()
        self.maxsize = maxsize
    
    def get(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None
    
    def set(self, key, value):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.maxsize:
            self.cache.popitem(last=False)
    
    def clear(self):
        self.cache.clear()

_comment_cache = LRUCache(maxsize=100)

def generate_ai_comment_with_gemini(score, details_data, student_name, test_name):
    """Geminiコメント生成（タイムアウト・キャッシュ付き・高速化）"""
    
    if not GEMINI_AVAILABLE or not gemini_model:
        return generate_ai_comment(score, details_data)
    
    labels = details_data.get('labels', [])
    scores = details_data.get('scores', [])
    
    if not labels or not scores:
        return generate_ai_comment(score, details_data)
    
    # キャッシュキー
    cache_key = f"{student_name}_{test_name}_{score}_{'_'.join(map(str, scores))}"
    
    # キャッシュチェック
    cached = _comment_cache.get(cache_key)
    if cached:
        print("【キャッシュ】コメントを再利用しました")
        return cached
    
    avg_score = sum(scores) / len(scores) if scores else 0
    max_category = labels[scores.index(max(scores))] if scores else "なし"
    min_category = labels[scores.index(min(scores))] if scores else "なし"
    
    category_results = "\n".join([f"- {labels[i]}: {scores[i]}%" for i in range(len(labels)) if i < len(scores)])
    
    prompt = f"""教育カウンセラーとして、学生の試験結果を分析してください。

学生: {student_name}
試験: {test_name}
得点: {score}点/100点

カテゴリー別:
{category_results}

得意: {max_category}
苦手: {min_category}

簡潔に（150字以内）：
💡総合評価:
⭐強み:
📚改善点:
🎯次のステップ:"""
    
    try:
        result = [None]
        error = [None]
        
        def call_gemini():
            try:
                response = gemini_model.generate_content(prompt)
                result[0] = response.text.lstrip('\n\r ')
            except Exception as e:
                error[0] = e
        
        thread = threading.Thread(target=call_gemini)
        thread.start()
        thread.join(timeout=60)  # 60秒に延長
        
        if thread.is_alive():
            print("【Geminiタイムアウト】フォールバック")
            return generate_ai_comment(score, details_data)
        
        if error[0]:
            raise error[0]
        
        comment = result[0] if result[0] else generate_ai_comment(score, details_data)
        
        # キャッシュに保存
        _comment_cache.set(cache_key, comment)
        
        return comment
        
    except Exception as e:
        print(f"【Geminiエラー】: {e}")
        return generate_ai_comment(score, details_data)

# ========== リクエスト前処理 ==========
@app.before_request
def before_request():
    if request.path.startswith('/static'):
        return
    
    public_paths = ['/', '/login', '/logout', '/register', '/password_reset']
    if request.path in public_paths:
        return
    
    if 'user_id' not in session:
        return redirect(url_for('login'))

# ========== ルーティング ==========
@app.route('/')
def index():
    session.clear()
    return redirect(url_for('login'))

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
        u_id = request.form.get('id', '').strip()
        pwd = request.form.get('password', '')

        if not u_id or not pwd:
            flash("IDとパスワードを入力してください。")
            return render_template('register.html')
        
        if len(u_id) > 20 or len(pwd) > 50:
            flash("入力内容が長すぎます。")
            return render_template('register.html')

        if update_user_password(u_id, pwd):
            flash("登録が完了しました。")
        else:
            flash("IDが見つかりません。")
            
    return render_template('register.html')

@app.route('/password_reset', methods=['GET', 'POST'])
def password_reset():
    if request.method == 'POST':
        u_id = request.form.get('id', '').strip()
        pwd = request.form.get('password', '')
        
        if not u_id or not pwd:
            return jsonify({'success': False, 'message': 'IDとパスワードを入力してください。'})
        
        if update_user_password(u_id, pwd):
            return jsonify({
                'success': True, 
                'message': 'パスワードを再設定しました。ログインしてください。'
            })
        else:
            return jsonify({
                'success': False, 
                'not_found': True,
                'message': '入力されたIDは登録されていません。'
            })
    
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
                    for pattern in patterns:
                        for col in reader.fieldnames:
                            if col is None:
                                continue
                            if pattern.lower() == col.lower():
                                return col
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
                
                for row in reader:
                    q_no_raw = ''
                    if q_no_col:
                        q_no_raw = row.get(q_no_col, '').strip()
                    
                    if not q_no_raw or q_no_raw == 'end':
                        continue
                    
                    try:
                        q_no_int = int(float(q_no_raw))
                    except (ValueError, TypeError):
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
                    
                    cur.execute('''
                        INSERT INTO questions (
                            test_id, q_no, category, question, target, 
                            a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, 
                            answer, explanation
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (
                        new_test_id, q_no_int,
                        null_to_empty(category), null_to_empty(question), null_to_empty(target),
                        null_to_empty(a1), null_to_empty(a2), null_to_empty(a3), null_to_empty(a4), null_to_empty(a5),
                        null_to_empty(a6), null_to_empty(a7), null_to_empty(a8), null_to_empty(a9), null_to_empty(a10),
                        null_to_empty(answer_val), null_to_empty(explanation)
                    ))
                    inserted_count += 1
                
                conn.commit()
                cur.close()
                conn.close()
                flash(f'「{t_name}」を正常に登録しました。（{inserted_count}問登録）')
                
            except Exception as e:
                if conn:
                    conn.rollback()
                    conn.close()
                flash(f'CSV登録エラー: {str(e)}')
        
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
        
        cur.execute("SELECT id, q_no, category, answer FROM questions WHERE test_id = %s ORDER BY q_no", (test_id,))
        questions = cur.fetchall()
        
        total_q = len(questions)
        if total_q == 0:
            flash("問題が見つかりません。")
            return redirect(url_for('student_dashboard'))
        
        correct_count = 0
        genre_stats = {}
        
        for q in questions:
            q_no = str(q['q_no'])
            category = q['category'] or '未分類'
            
            if category not in genre_stats:
                genre_stats[category] = {'correct': 0, 'total': 0}
            genre_stats[category]['total'] += 1
            
            user_answer = user_answers.get(q_no, '')
            correct_answer = str(q['answer']) if q['answer'] else ''
            
            if user_answer and user_answer == correct_answer:
                correct_count += 1
                genre_stats[category]['correct'] += 1
        
        labels = list(genre_stats.keys())
        scores = []
        for g in labels:
            if genre_stats[g]['total'] > 0:
                score = int((genre_stats[g]['correct'] / genre_stats[g]['total']) * 100)
            else:
                score = 0
            scores.append(score)
        
        analysis = {"labels": labels, "scores": scores}
        final_score = int((correct_count / total_q) * 100)
        
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

# ========== 非同期AIコメント用 ==========
@app.route('/api/result/<int:result_id>/ai_comment', methods=['GET'])
def api_get_ai_comment(result_id):
    """AIコメントだけを非同期で返すAPI"""
    if session.get('role') != 'student':
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('''
            SELECT r.*, u.name as student_name, t.name as test_name
            FROM results r
            JOIN users u ON r.user_id = u.id
            JOIN tests t ON r.test_id = t.id
            WHERE r.id = %s AND r.user_id = %s
        ''', (result_id, session.get('user_id')))
        res = cur.fetchone()
        cur.close()
        conn.close()
        
        if not res:
            return jsonify({'error': 'Not found'}), 404
        
        details_data = json.loads(res['details']) if res.get('details') else {'labels': [], 'scores': []}
        
        comment = generate_ai_comment_with_gemini(
            score=res['score'],
            details_data=details_data,
            student_name=res['student_name'],
            test_name=res['test_name']
        )
        return jsonify({'comment': comment})
        
    except Exception as e:
        print(f"AI comment error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/student/test/<int:test_id>/result/<int:result_id>')
def show_result(test_id, result_id):
    """結果ページ表示（AIコメントは非同期で取得）"""
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

        # 安全なJSONパース
        try:
            details_data = json.loads(res['details']) if res.get('details') else {'labels': [], 'scores': []}
        except json.JSONDecodeError:
            details_data = {'labels': [], 'scores': []}
        
        # AIコメントは呼ばない → テンプレートに result_id だけ渡す
        return render_template('result_page.html',
            res=dict(res),
            details=details_data,
            result_id=result_id,
            test_name=test_name
        )
        
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
    if session.get('role') != 'student':
        return jsonify({'error': 'Unauthorized'}), 401
    
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO results (test_id, user_id, score, details, timestamp) 
        VALUES (%s, %s, %s, %s, NOW())
    ''', (test_id, session.get('user_id'), 0, json.dumps({"labels": [], "scores": []})))
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
            ans_dict[str(q_no)] = ""
        else:
            ans_dict[str(q_no)] = str(data.get('choice', ''))
        session['answers'] = ans_dict

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

# ========== 志望動機作成機能 ==========

@app.route('/motivation_form')
def motivation_form():
    if session.get('role') != 'student':
        return redirect(url_for('login'))
    return render_template('motivation_form.html')

def parse_ai_response(response_text):
    """AIの応答をパースして各セクションを抽出"""
    import re
    
    def extract_section(text, section_name):
        patterns = [
            rf'【{section_name}】\s*(.+?)(?=【|$)',
            rf'{section_name}\s*[:：]\s*(.+?)(?=\n\n|\n【|$)',
            rf'■{section_name}\s*(.+?)(?=■|$)'
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                return match.group(1).strip()
        return ""

    motivation = extract_section(response_text, "志望動機")
    hobby = extract_section(response_text, "趣味・特技")
    self_pr = extract_section(response_text, "自己PR")
    
    if not motivation:
        motivation = response_text[:500] if len(response_text) > 500 else response_text
    if not hobby:
        hobby = "趣味は〇〇です。そこから△△を学びました。"
    if not self_pr:
        self_pr = "私の強みは○○です。これを活かして貢献します。"
    
    return {
        'motivation': motivation,
        'hobby': hobby,
        'self_pr': self_pr
    }

def get_sample_easy_motivation():
    """Geminiがない場合のサンプル文章"""
    return {
        'motivation': """私が御社を志望する理由は、○○という事業に魅力を感じたからです。\n私は△△で培った経験を活かし、御社の△△として貢献したいと考えています。\nアルバイトでは、チームの一員として目標達成に貢献しました。\nこの経験から、協力することの大切さを学びました。\n貴社で働くことで、さらに成長し、社会に貢献できる人材になりたいです。""",
        'hobby': """私の趣味は○○です。\nこの趣味を通じて、継続する力や新しいことに挑戦する勇気を学びました。\nこれらの経験は、仕事でも必ず活きると考えています。""",
        'self_pr': """私の強みは○○です。\n例えば、アルバイトでは△△という成果を上げました。\nまた、課題があった場合は、積極的に改善案を提案するよう心がけています。\nこれからも向上心を持って、仕事に取り組みたいと思います。"""
    }

def create_easy_japanese_prompt(data):
    """やさしい日本語用のプロンプトを作成"""
    university_status_text = {
        'none': '行っていない',
        'dropout': '中退した',
        'graduate': '卒業した'
    }.get(data.get('university_status', 'none'), '行っていない')
    
    leader_exp_text = 'あり' if data.get('has_leader_exp') == 'yes' else 'なし'
    
    if data.get('other_job_exists') == 'yes':
        other_job_text = f"あり（会社名: {data.get('other_job_company', '')}）"
    else:
        other_job_text = "なし"
    
    return f"""
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

【出力形式】
【志望動機】（本文）
【趣味・特技】（本文）
【自己PR】（本文）
"""

@app.route('/api/generate_motivation', methods=['POST'])
def generate_motivation():
    """AIで志望動機・趣味特技・自己PRを生成"""
    if session.get('role') != 'student':
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    if data is None:
        return jsonify({'error': 'Invalid JSON'}), 400
    
    required_fields = [
        'company_name', 'desired_position', 'high_school_efforts',
        'current_part_time', 'part_time_description', 'has_leader_exp',
        'part_time_achievement', 'languages', 'certifications',
        'strengths', 'how_overcome', 'hobbies', 'career_plan'
    ]
    
    missing_fields = [f for f in required_fields if not data.get(f)]
    if missing_fields:
        return jsonify({'error': f'必須項目が不足しています: {missing_fields}'}), 400
    
    prompt = create_easy_japanese_prompt(data)
    
    if GEMINI_AVAILABLE and gemini_model:
        try:
            # タイムアウト60秒で実行
            result = [None]
            error = [None]
            
            def call_gemini():
                try:
                    response = gemini_model.generate_content(prompt)
                    result[0] = response.text
                except Exception as e:
                    error[0] = e
            
            thread = threading.Thread(target=call_gemini)
            thread.start()
            thread.join(timeout=60)  # 60秒タイムアウト
            
            if thread.is_alive():
                print("【Geminiタイムアウト】志望動機生成")
                return jsonify(get_sample_easy_motivation()), 200
            
            if error[0]:
                raise error[0]
            
            if result[0]:
                parsed = parse_ai_response(result[0])
                return jsonify(parsed)
            else:
                return jsonify(get_sample_easy_motivation()), 200
                
        except Exception as e:
            print(f"【Geminiエラー】: {e}")
            return jsonify(get_sample_easy_motivation()), 200
    else:
        return jsonify(get_sample_easy_motivation()), 200

# ========== サーバー起動 ==========
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)