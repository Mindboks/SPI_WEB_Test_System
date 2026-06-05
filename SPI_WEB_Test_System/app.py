#2026-6-5 version2.3.7

# -*- coding: utf-8 -*-
import os
import csv
import json
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# --- 1. アプリと設定の初期化 ---
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default_secret_key_for_production')
DATABASE_URL = os.environ.get('DATABASE_URL')
# --- 2. データベース接続関数 ---
def get_db():
    # 環境変数をここで必ず取得する
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise ValueError("DATABASE_URLが設定されていません")   
    conn = psycopg2.connect(db_url)
    return conn

# データを取得  するためのユーティリティ関数
def query_db(query, args=(), one=False):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor) 
    cur.execute(query, args)
    rv = cur.fetchall()
    cur.close()
    conn.close()
    return (rv[0] if rv else None) if one else rv

def hash_password(password):
    return hashlib.sha256((password or "").encode('utf-8')).hexdigest()

def init_db():
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            # ユーザーテーブル
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, 
                    name TEXT, 
                    class_id TEXT, 
                    password TEXT, 
                    role TEXT
                )
            """)
            
            # テストテーブル
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tests (
                    id SERIAL PRIMARY KEY, 
                    name TEXT, 
                    target_class TEXT, 
                    duration INTEGER DEFAULT 30
                )
            """)
            
            # 質問テーブル
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
            
            # 結果テーブル
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
            print("【初期化】DB接続を閉じました")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u_id = request.form.get('id', '').strip()
        pwd = request.form.get('password')
        hashed_pwd = hash_password(pwd)
        
        try:
            conn = get_db()
            # 【重要】RealDictCursor を指定することで、user['password'] が使えるようになります
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
                return redirect(url_for('index'))
            else:
                flash("IDまたはパスワードが間違っています")
        except Exception as e:
            print(f"ログインエラー: {e}")
            flash("システムエラーが発生しました")
            
    return render_template('login.html')

@app.route('/')
def index():
    # セッションがない、またはIDがない場合はログインへ
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # 役割による振り分け
    user_role = session.get('role')
    if user_role == 'teacher':
        return redirect(url_for('teacher_admin'))
    elif user_role == 'student':
        return redirect(url_for('student_dashboard'))
    
    # どちらでもない場合は不正なセッションとみなしログアウト
    return redirect(url_for('logout'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    return render_template('register.html')

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
            try:
                conn = get_db()
                cur = conn.cursor()
                
                # 1. testsテーブルのIDを手動採番して登録
                cur.execute('SELECT COALESCE(MAX(id), 0) + 1 FROM tests')
                new_test_id = cur.fetchone()[0]
                cur.execute('INSERT INTO tests (id, name, target_class, duration) VALUES (%s, %s, %s, %s)', 
                            (new_test_id, t_name, t_class, t_duration))
                
                # 2. questionsテーブル用のIDカウンターを現在の最大値から取得
                cur.execute('SELECT COALESCE(MAX(id), 0) FROM questions')
                q_id_counter = cur.fetchone()[0]
                
                import io
                import csv
                stream = io.StringIO(file.stream.read().decode("cp932"))
                reader = csv.DictReader(stream)
                
                for row in reader:
                    # idを明示的に指定せず、DB側に自動で番号を振らせます
                    cur.execute('''
                    INSERT INTO questions (test_id, q_no, category, question, target, a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, answer, explanation)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    # q_id_counter を削除しました
                    new_test_id, 
                    row['test_number'], row['test genre'], row['test questions'], row.get('target', ''),
                    row.get('Answer_1',''), row.get('Answer_2',''), row.get('Answer_3',''), 
                    row.get('Answer_4',''), row.get('Answer_5',''), row.get('Answer_6',''), 
                    row.get('Answer_7',''), row.get('Answer_8',''), row.get('Answer_9',''), 
                    row.get('Answer_10',''), row['Answer'], row.get('Test explanation', '')
                ))
                
                conn.commit()
                cur.close()
                conn.close()
                flash(f'「{t_name}」を正常に登録しました。')
            except Exception as e:
                flash(f'CSV登録エラー: {str(e)}')
        
        return redirect(url_for('teacher_admin'))

    # GET処理（表示用データ取得）は同じ
    tests, results, classes = [], [], []
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT DISTINCT class_id FROM users WHERE class_id IS NOT NULL AND class_id != 'teacher' ORDER BY class_id ASC")
        classes = [row['class_id'] for row in cur.fetchall()]
        cur.execute('SELECT * FROM tests')
        tests = cur.fetchall()
        cur.execute('''SELECT r.id, t.name AS test_name, u.class_id, u.id AS student_id, u.name AS student_name, r.score, r.timestamp 
                       FROM results r JOIN tests t ON r.test_id = t.id JOIN users u ON r.user_id = u.id ORDER BY r.timestamp DESC''')
        results = cur.fetchall()
        cur.close()
        conn.close()
    except:
        pass
        
    return render_template('admin.html', tests=tests, results=results, classes=classes)

# app.py に追加が必要な削除用ルート
@app.route('/teacher/delete_test/<int:test_id>', methods=['POST'])
def delete_test(test_id):
    if session.get('role') != 'teacher': 
        return redirect(url_for('index'))
    
    conn = get_db()
    cur = conn.cursor()
    # 紐づく問題とテストを削除
    cur.execute('DELETE FROM questions WHERE test_id = %s', (test_id,))
    cur.execute('DELETE FROM tests WHERE id = %s', (test_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash('テストを削除しました。')
    return redirect(url_for('teacher_admin'))

@app.route('/student/test/<int:test_id>/cheated', methods=['POST'])
def cheated_test(test_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO results (test_id, user_id, score, comment) VALUES (%s, %s, %s, %s)',
                         (test_id, session.get('user_id'), 0, "失格"))
        conn.commit()
    return jsonify({'status': 'ok'}) 

@app.route('/delete_test/<int:test_id>', methods=['POST'])
def delete_test(test_id):
    if session.get('role') != 'teacher':
        return redirect(url_for('index'))
    conn = get_db()
    cur = conn.cursor()
    cur.execute('DELETE FROM questions WHERE test_id = %s', (test_id,))
    cur.execute('DELETE FROM results WHERE test_id = %s', (test_id,))
    cur.execute('DELETE FROM tests WHERE id = %s', (test_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash('テストと関連データを削除しました。')
    return redirect(url_for('teacher_admin'))

# 生徒用ダッシュボード（リダイレクト先）
@app.route('/student_dashboard')
def student_dashboard():
    if session.get('role') != 'student': 
        return redirect(url_for('index'))
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # ユーザーのクラスを取得
    cur.execute('SELECT class_id FROM users WHERE id = %s', (session.get('user_id'),))
    user = cur.fetchone()
    class_id = user['class_id'] if user else None
    
    # テストと結果を取得
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
    # 1. 権限チェック：生徒以外はアクセス禁止
    if session.get('role') != 'student':
        flash("受験には生徒アカウントでのログインが必要です。")
        return redirect(url_for('login'))
    # 2. セッションの初期化
    # 以前の回答データをクリアして、新しい受験セッションを開始します
    session['answers'] = {}
    session['current_test_id'] = test_id
    # 3. 必要に応じてテストの存在チェックを行う（任意ですが推奨）
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT * FROM tests WHERE id = %s', (test_id,))
        test = cur.fetchone()
        cur.close()
        conn.close()
        if not test:
            flash("選択したテストは見つかりませんでした。")
            return redirect(url_for('student_dashboard'))           
    except Exception as e:
        if conn: conn.close()
        flash("システムエラーが発生しました。")
        return redirect(url_for('student_dashboard'))
    # 4. 受験画面テンプレートを表示
    # test_page.html に test_id を渡して、問題読み込みを開始させます
    return render_template('test_page.html', test_id=test_id)

# 試験終了（提出）処理ルート
def calculate_analysis(test_id, user_answers):
    # DBからそのテストの全問題（正解とジャンル）を取得
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, genre, correct_answer FROM questions WHERE test_id = %s", (test_id,))
    questions = cur.fetchall() 
    genre_stats = {}
    for q in questions:
        genre = q['genre']
        if genre not in genre_stats:
            genre_stats[genre] = {'correct': 0, 'total': 0}        
        genre_stats[genre]['total'] += 1
        # ユーザーの回答が正解と一致するかチェック
        if str(user_answers.get(str(q['id']))) == str(q['correct_answer']):
            genre_stats[genre]['correct'] += 1
    
    # グラフ用データに変換
    labels = list(genre_stats.keys())
    scores = [int((genre_stats[g]['correct'] / genre_stats[g]['total']) * 100) for g in labels]
    cur.close()
    conn.close()
    return {"labels": labels, "scores": scores}

# --- 提出処理の追加 ---
@app.route('/student/test/<int:test_id>/submit', methods=['GET'])
def submit_test(test_id):
    if session.get('role') != 'student':
        return redirect(url_for('login'))
    answers = session.get('answers', {})
    # 分析データを作成(正解率のジャンル別分析など)
    analysis = calculate_analysis(test_id, answers)

    # スコア計算（正解率の平均などを算出）
    total_scores = analysis['scores']
    avg_score = sum(total_scores) / len(total_scores) if total_scores else 0 
    conn = get_db()
    cur = conn.cursor()
    # 結果を保存
    cur.execute('''
        INSERT INTO results (user_id, test_id, score, details, timestamp) 
        VALUES (%s, %s, %s, %s, NOW()) RETURNING id
    ''', (session.get('user_id'), test_id, int(avg_score), json.dumps(analysis)))
    result_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('show_result', test_id=test_id, result_id=result_id))

# --- 結果表示用 ---
@app.route('/student/test/<int:test_id>/result/<int:result_id>')
def show_result(test_id, result_id):
    if session.get('role') != 'student':
        return redirect(url_for('index'))
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM results WHERE id = %s AND user_id = %s', (result_id, session.get('user_id')))
    res = cur.fetchone()
    
    if not res:
        flash("結果が見つかりません。")
        return redirect(url_for('student_dashboard'))

    try:
        details_data = json.loads(res['details'])
    except:
        details_data = {'labels': [], 'scores': []}
    cur.close()
    conn.close()
    return render_template('result.html', res=res, details=details_data)

@app.route('/api/student/test/<int:test_id>/get_question/<int:q_no>', methods=['GET', 'POST'])
def api_get_question(test_id, q_no):
    if session.get('role') != 'student': return jsonify({'error': 'Unauthorized'}), 401
    if 'answers' not in session: session['answers'] = {}
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

@app.route('/password_reset', methods=['GET', 'POST'])
def password_reset():
    return render_template('password_reset.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)  # セッションからユーザー情報を削除
    session.pop('role', None)
    return redirect(url_for('login')) 

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)