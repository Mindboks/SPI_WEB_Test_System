#2026-6-5 version2.4.8

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
            
            # テストテーブル（idはSERIALで自動採番）
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tests (
                    id SERIAL PRIMARY KEY, 
                    name TEXT, 
                    target_class TEXT, 
                    duration INTEGER DEFAULT 30
                )
            """)
            
            # 質問テーブル（idはSERIALで自動採番）
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
            
            # シーケンスをリセット（既存データがある場合）
            cur.execute("SELECT COUNT(*) FROM questions")
            count = cur.fetchone()[0]
            if count > 0:
                cur.execute("SELECT setval('questions_id_seq', (SELECT MAX(id) FROM questions))")
                conn.commit()
                print(f"【初期化】questions_id_seq をリセットしました（現在の最大ID: {count}）")
            
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

# 新規登録
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u_id = request.form.get('id', '')
        pwd = request.form.get('password', '')

        # バリデーション：空チェックと長さ制限
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
                
                # 1. testsテーブルに登録
                cur.execute('''
                    INSERT INTO tests (name, target_class, duration) 
                    VALUES (%s, %s, %s) RETURNING id
                ''', (t_name, t_class, t_duration))
                new_test_id = cur.fetchone()[0]
                
                # 2. CSVファイルの読み込み
                import io
                import csv
                
                # ファイルの内容を読み込み
                file_content = file.read()
                
                # エンコーディングを自動判定
                try:
                    decoded_content = file_content.decode('utf-8-sig')
                except UnicodeDecodeError:
                    try:
                        decoded_content = file_content.decode('cp932')
                    except UnicodeDecodeError:
                        decoded_content = file_content.decode('latin-1')
                
                stream = io.StringIO(decoded_content)
                reader = csv.DictReader(stream)
                
                # 3. 各行をquestionsテーブルに登録
                inserted_count = 0
                for row in reader:
                    # 'end' 行をスキップ
                    if row.get('test_number', '').strip() == 'end':
                        continue
                    
                    # test_numberが空の場合はスキップ
                    if not row.get('test_number') or str(row.get('test_number')).strip() == '':
                        continue
                    
                    # 値を取得する関数
                    def get_value(key, default=''):
                        val = row.get(key, default)
                        return val if val and str(val).strip() else default
                    
                    # 選択肢の取得（Answer_1 〜 Answer_10）
                    a1 = get_value('Answer_1')
                    a2 = get_value('Answer_2')
                    a3 = get_value('Answer_3')
                    a4 = get_value('Answer_4')
                    a5 = get_value('Answer_5')
                    a6 = get_value('Answer_6')
                    a7 = get_value('Answer_7')
                    a8 = get_value('Answer_8')
                    a9 = get_value('Answer_9')
                    a10 = get_value('Answer_10')
                    
                    # answerが数値の場合はそのまま、それ以外は空文字
                    answer_val = get_value('Answer')
                    if answer_val and answer_val.isdigit():
                        answer_val = str(answer_val)
                    else:
                        answer_val = ''
                    
                    # INSERT実行
                    cur.execute('''
                        INSERT INTO questions (
                            test_id, q_no, category, question, target, 
                            a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, 
                            answer, explanation
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (
                        new_test_id,
                        int(get_value('test_number', 0)),
                        get_value('test genre'),
                        get_value('test questions'),
                        get_value('target'),
                        a1, a2, a3, a4, a5, a6, a7, a8, a9, a10,
                        answer_val,
                        get_value('Test explanation')
                    ))
                    inserted_count += 1
                
                conn.commit()
                cur.close()
                conn.close()
                flash(f'「{t_name}」を正常に登録しました。（{inserted_count}問）')
                
            except Exception as e:
                if conn:
                    conn.rollback()
                    conn.close()
                flash(f'CSV登録エラー: {str(e)}')
                import traceback
                traceback.print_exc()
        
        return redirect(url_for('teacher_admin'))

    # GET処理（表示用データ取得）
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

#削除用
@app.route('/teacher/delete_test/<int:test_id>', methods=['POST'])
def delete_test(test_id):
    if session.get('role') != 'teacher': return redirect(url_for('index'))
    conn = get_db()
    cur = conn.cursor()
    # 削除順序: 結果 → 問題 → テスト
    cur.execute('DELETE FROM results WHERE test_id = %s', (test_id,))
    cur.execute('DELETE FROM questions WHERE test_id = %s', (test_id,))
    cur.execute('DELETE FROM tests WHERE id = %s', (test_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash('テストと関連するすべての結果を削除しました。')
    return redirect(url_for('teacher_admin'))

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

# --- 試験開始処理 ---
@app.route('/student/test/<int:test_id>/start', methods=['GET', 'POST'])
def take_test(test_id):
    if session.get('role') != 'student':
        flash("受験には生徒アカウントでのログインが必要です。")
        return redirect(url_for('login'))
    
    session['answers'] = {}
    session['current_test_id'] = test_id
    
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # テスト情報の取得
        cur.execute('SELECT * FROM tests WHERE id = %s', (test_id,))
        test = cur.fetchone()
        
        # 総問題数の取得
        cur.execute('SELECT COUNT(*) as total FROM questions WHERE test_id = %s', (test_id,))
        total_data = cur.fetchone()
        total_q = total_data['total'] if total_data else 0
        
        cur.close()
        conn.close()
        
        if not test:
            flash("選択したテストは見つかりませんでした。")
            return redirect(url_for('student_dashboard'))
        
        # 修正：test と total_q をテンプレートに渡す
        return render_template('test_page.html', test_id=test_id, test=test, total_q=total_q)
        
    except Exception as e:
        if conn: conn.close()
        print(f"Error in take_test: {e}")
        flash("システムエラーが発生しました。")
        return redirect(url_for('student_dashboard'))
    
# 試験終了（提出）処理ルート
def calculate_analysis(test_id, user_answers):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # genre → category, correct_answer → answer に修正
    cur.execute("SELECT id, category, answer FROM questions WHERE test_id = %s", (test_id,))
    questions = cur.fetchall() 
    genre_stats = {}
    for q in questions:
        category = q['category']
        if category not in genre_stats:
            genre_stats[category] = {'correct': 0, 'total': 0}       
        genre_stats[category]['total'] += 1
        if str(user_answers.get(str(q['id']))) == str(q['answer']):
            genre_stats[category]['correct'] += 1
    
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
    
    # セッションからユーザーの回答を取得
    user_answers = session.get('answers', {})
    
    # 1. データベースから全問題の正解を取得してスコアを計算
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # 全問題を取得
    cur.execute("SELECT id, category, answer FROM questions WHERE test_id = %s", (test_id,))
    questions = cur.fetchall()
    
    total_q = len(questions)
    correct_count = 0
    genre_stats = {}
    
    # スコア計算と詳細分析の作成
    for q in questions:
        q_id = str(q['id'])
        category = q['category']
        
        # ジャンルごとの初期化
        if category not in genre_stats:
            genre_stats[category] = {'correct': 0, 'total': 0}
        genre_stats[category]['total'] += 1
        
        # 回答照合
        if str(user_answers.get(q_id)) == str(q['answer']):
            correct_count += 1
            genre_stats[category]['correct'] += 1
    
    # 分析データを作成
    analysis = {
        "labels": list(genre_stats.keys()),
        "scores": [int((genre_stats[g]['correct'] / genre_stats[g]['total']) * 100) for g in genre_stats]
    }
    
    # 総スコア計算（100点満点）
    final_score = int((correct_count / total_q) * 100) if total_q > 0 else 0
    
    # 2. 結果を保存
    cur.execute('''
        INSERT INTO results (user_id, test_id, score, details, timestamp) 
        VALUES (%s, %s, %s, %s, NOW()) RETURNING id
    ''', (session.get('user_id'), test_id, final_score, json.dumps(analysis)))
    
    result_id = cur.fetchone()['id']
    conn.commit()
    
    cur.close()
    conn.close()
    
    # セッションの回答データをクリア
    session.pop('answers', None)
    
    return redirect(url_for('show_result', test_id=test_id, result_id=result_id))
# --- 結果表示用 ---
@app.route('/student/test/<int:test_id>/result/<int:result_id>')
def show_result(test_id, result_id):
    if session.get('role') != 'student':
        return redirect(url_for('login'))
    
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # JOINを使用してデータを取得
        cur.execute('''
            SELECT r.*, u.name as student_name 
            FROM results r 
            JOIN users u ON r.user_id = u.id 
            WHERE r.id = %s AND r.user_id = %s
        ''', (result_id, session.get('user_id')))
        
        res = cur.fetchone()
        cur.close()
        conn.close()
        
        if not res:
            flash("結果が見つかりません。")
            return redirect(url_for('student_dashboard'))

        # detailsの解析
        details_data = json.loads(res['details']) if res.get('details') else {'labels': [], 'scores': []}
        return render_template('result_page.html', res=res, details=details_data)

    except Exception as e:
        # 万が一エラーが起きても、アプリが白紙にならずログを残してダッシュボードに戻す
        if conn: conn.close()
        print(f"Error: {e}")
        flash("結果の表示中にエラーが発生しました。")
        return redirect(url_for('student_dashboard'))
def reset_question_sequence():
    """questionsテーブルのIDシーケンスをリセットする"""
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # 現在の最大IDを取得
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM questions")
        max_id = cur.fetchone()[0]
        
        # シーケンスをリセット
        cur.execute(f"ALTER SEQUENCE questions_id_seq RESTART WITH {max_id + 1}")
        conn.commit()
        
        cur.close()
        conn.close()
        print(f"【シーケンスリセット】questions_id_seq を {max_id + 1} に設定しました")
        return True
    except Exception as e:
        print(f"【エラー】シーケンスリセット失敗: {e}")
        if conn:
            conn.close()
        return False

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
    session.pop('user_id', None)  
    session.pop('role', None)
    return redirect(url_for('login')) 

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)