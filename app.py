#2026-6-6 version2.5.6all

# -*- coding: utf-8 -*-
import os
import csv
import json
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

# Geminiはオプション（パッケージがない場合はスキップ）
GEMINI_AVAILABLE = False
gemini_model = None

try:
    import google.generativeai as genai
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash')
        GEMINI_AVAILABLE = True
        print("【Gemini】有効化されました")
    else:
        print("【Gemini】APIキーが設定されていません - ダミーコメントを使用します")
except ImportError:
    print("【Gemini】パッケージがインストールされていません - ダミーコメントを使用します")
except Exception as e:
    print(f"【Gemini】初期化エラー: {e}")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# --- 1. アプリと設定の初期化 ---
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default_secret_key_for_production')
DATABASE_URL = os.environ.get('DATABASE_URL')

# アプリケーションの設定
app.config.update(
    SESSION_COOKIE_SECURE = False,
    SESSION_COOKIE_HTTPONLY = True,
    SESSION_COOKIE_SAMESITE = 'Lax',
    PERMANENT_SESSION_LIFETIME = 3600,
)

# --- 2. データベース接続関数 ---
def get_db():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise ValueError("DATABASE_URLが設定されていません")   
    conn = psycopg2.connect(db_url)
    return conn

def hash_password(password):
    return hashlib.sha256((password or "").encode('utf-8')).hexdigest()


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
    elif score >= 60:
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
        return response.text
    except Exception as e:
        print(f"【Geminiエラー】: {e}")
        return generate_ai_comment(score, details_data)


# ========== 以下、通常のルーティング（変更なし） ==========
# ※ ここから先は前回のコードと同じです
# （省略しますが、前回のapp.pyの内容を続けて貼り付けてください）

@app.route('/login', methods=['GET', 'POST'])
def login():
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
                return redirect(url_for('index'))
            else:
                flash("IDまたはパスワードが間違っています")
        except Exception as e:
            print(f"ログインエラー: {e}")
            flash("システムエラーが発生しました")
            
    return render_template('login.html')


@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('role')
    if user_role == 'teacher':
        return redirect(url_for('teacher_admin'))
    elif user_role == 'student':
        return redirect(url_for('student_dashboard'))
    
    return redirect(url_for('logout'))


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
                
                cur.execute("SELECT COALESCE(MAX(id), 0) FROM tests")
                max_test_id = cur.fetchone()[0]
                new_test_id = max_test_id + 1
                
                cur.execute('''
                    INSERT INTO tests (id, name, target_class, duration) 
                    VALUES (%s, %s, %s, %s)
                ''', (new_test_id, t_name, t_class, t_duration))
                
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
                    for pattern in patterns:
                        for col in reader.fieldnames:
                            if pattern.lower() in col.lower() or col == pattern:
                                return col
                    return None
                
                q_no_col = find_column(reader, ['测试编号', 'test_number', '番号', 'no', '問題番号', '序号'])
                category_col = find_column(reader, ['A-Z category', 'category', 'カテゴリ', 'ジャンル', 'test genre'])
                question_col = find_column(reader, ['A-Z question', 'question', '問題文', 'test questions'])
                target_col = find_column(reader, ['A-Z target', 'target', 'ターゲット'])
                answer_col = find_column(reader, ['A-Z answer', 'answer', '正解', 'Answer'])
                explanation_col = find_column(reader, ['Test explanation', 'explanation', '解説'])
                
                choice_columns = []
                for i in range(1, 11):
                    patterns = [f'a{i}', f'A-Z a{i}', f'Answer_{i}', f'選択肢{i}', f'option{i}']
                    found = find_column(reader, patterns)
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
                    
                    cur.execute('''
                        INSERT INTO questions (
                            id, test_id, q_no, category, question, target, 
                            a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, 
                            answer, explanation
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (
                        next_q_id, new_test_id, q_no_int, 
                        null_to_empty(category), null_to_empty(question), null_to_empty(target),
                        null_to_empty(a1), null_to_empty(a2), null_to_empty(a3), null_to_empty(a4), null_to_empty(a5),
                        null_to_empty(a6), null_to_empty(a7), null_to_empty(a8), null_to_empty(a9), null_to_empty(a10),
                        null_to_empty(answer_val), null_to_empty(explanation)
                    ))
                    
                    next_q_id += 1
                    inserted_count += 1
                
                conn.commit()
                cur.close()
                conn.close()
                flash(f'「{t_name}」を正常に登録しました。（{inserted_count}問登録 / {skipped_count}行スキップ）')
                
            except Exception as e:
                if conn:
                    conn.rollback()
                    conn.close()
                flash(f'CSV登録エラー: {str(e)}')
                import traceback
                traceback.print_exc()
        
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
        flash("受験には生徒アカウントでのログインが必要です。")
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
        
        analysis = {
            "labels": list(genre_stats.keys()),
            "scores": [int((genre_stats[g]['correct'] / genre_stats[g]['total']) * 100) if genre_stats[g]['total'] > 0 else 0 for g in genre_stats]
        }
        
        final_score = int((correct_count / total_q) * 100) if total_q > 0 else 0
        
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


@app.route('/password_reset', methods=['GET', 'POST'])
def password_reset():
    return render_template('password_reset.html')


@app.route('/logout')
def logout():
    session.pop('user_id', None)  
    session.pop('role', None)
    return redirect(url_for('login'))


@app.before_request
def check_session_expiry():
    if session.get('user_id') and session.get('current_test_id'):
        if session.permanent:
            session.modified = True


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)