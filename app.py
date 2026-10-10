import os
import io
import csv
import sqlite3
from datetime import datetime, date
from flask import Flask, request, jsonify, render_template_string, make_response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "events.db")

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT DEFAULT '',
                date TEXT DEFAULT '',
                time TEXT DEFAULT '',
                location TEXT DEFAULT '',
                participants INTEGER DEFAULT 0,
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'Upcoming',
                created_at TEXT
            )
        """)
        # Add status column to an older database if needed.
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(events)").fetchall()]
        if "status" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN status TEXT DEFAULT 'Upcoming'")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                admin_name TEXT DEFAULT 'Admin',
                admin_email TEXT DEFAULT ''
            )
        """)
        conn.execute("INSERT OR IGNORE INTO settings (id, admin_name, admin_email) VALUES (1, 'Admin', '')")

init_db()

def event_dict(row):
    return dict(row) if row else None

def validate_event(data, existing=None):
    existing = existing or {}
    name = str(data.get("name", existing.get("name", ""))).strip()
    if not name:
        return None, "Event name is required"
    try:
        participants = int(data.get("participants", existing.get("participants", 0)) or 0)
        if participants < 0:
            raise ValueError
    except (ValueError, TypeError):
        return None, "Participants must be a non-negative whole number"
    status = str(data.get("status", existing.get("status", "Upcoming"))).strip()
    if status not in ("Upcoming", "Completed", "Cancelled"):
        status = "Upcoming"
    result = {
        "name": name,
        "type": str(data.get("type", existing.get("type", ""))).strip(),
        "date": str(data.get("date", existing.get("date", ""))).strip(),
        "time": str(data.get("time", existing.get("time", ""))).strip(),
        "location": str(data.get("location", existing.get("location", ""))).strip(),
        "participants": participants,
        "description": str(data.get("description", existing.get("description", ""))).strip(),
        "status": status
    }
    return result, None

@app.route("/")
def home():
    return render_template_string(PAGE)

@app.route("/api/events", methods=["GET", "POST"])
def events_api():
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form.to_dict()
        event, error = validate_event(data)
        if error:
            return jsonify(status="error", message=error), 400
        event["created_at"] = datetime.now().isoformat(timespec="seconds")
        with get_db() as conn:
            cur = conn.execute("""
                INSERT INTO events
                (name,type,date,time,location,participants,description,status,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, tuple(event[k] for k in ("name","type","date","time","location","participants","description","status")) + (event["created_at"],))
            row = conn.execute("SELECT * FROM events WHERE id=?", (cur.lastrowid,)).fetchone()
        return jsonify(status="success", message="Event created successfully", event=event_dict(row)), 201
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY date ASC, time ASC, id DESC").fetchall()
    return jsonify(status="success", events=[event_dict(r) for r in rows], count=len(rows))

@app.route("/api/events/<int:event_id>", methods=["GET", "PUT", "DELETE"])
def one_event_api(event_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            return jsonify(status="error", message="Event not found"), 404
        if request.method == "GET":
            return jsonify(status="success", event=event_dict(row))
        if request.method == "DELETE":
            conn.execute("DELETE FROM events WHERE id=?", (event_id,))
            return jsonify(status="success", message="Event deleted successfully")
        data = request.get_json(silent=True) or {}
        event, error = validate_event(data, dict(row))
        if error:
            return jsonify(status="error", message=error), 400
        conn.execute("""
            UPDATE events SET name=?,type=?,date=?,time=?,location=?,participants=?,description=?,status=?
            WHERE id=?
        """, tuple(event[k] for k in ("name","type","date","time","location","participants","description","status")) + (event_id,))
        updated = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    return jsonify(status="success", message="Event updated successfully", event=event_dict(updated))

@app.route("/api/analytics")
def analytics_api():
    today = date.today().isoformat()
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        participants = conn.execute("SELECT COALESCE(SUM(participants),0) FROM events").fetchone()[0]
        upcoming = conn.execute("SELECT COUNT(*) FROM events WHERE status='Upcoming' AND (date='' OR date>=?)", (today,)).fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM events WHERE status='Completed' OR (date<>'' AND date<?)", (today,)).fetchone()[0]
        by_type = conn.execute("""SELECT CASE WHEN type='' THEN 'Other' ELSE type END AS label, COUNT(*) AS total FROM events GROUP BY label ORDER BY total DESC""").fetchall()
        by_status = conn.execute("SELECT status AS label, COUNT(*) AS total FROM events GROUP BY status").fetchall()
    return jsonify(status="success", total_events=total, total_participants=participants,
                   upcoming_events=upcoming, completed_events=completed,
                   events_by_type=[dict(r) for r in by_type], events_by_status=[dict(r) for r in by_status])

@app.route("/api/settings", methods=["GET", "POST"])
def settings_api():
    with get_db() as conn:
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            name = str(data.get("admin_name", "Admin")).strip() or "Admin"
            email = str(data.get("admin_email", "")).strip()
            conn.execute("UPDATE settings SET admin_name=?, admin_email=? WHERE id=1", (name, email))
        row = conn.execute("SELECT admin_name,admin_email FROM settings WHERE id=1").fetchone()
    return jsonify(status="success", settings=dict(row))

@app.route("/api/export.csv")
def export_csv():
    with get_db() as conn:
        rows = conn.execute("SELECT id,name,type,date,time,location,participants,description,status,created_at FROM events ORDER BY date,time").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID","Event Name","Type","Date","Time","Location","Participants","Description","Status","Created At"])
    for row in rows:
        writer.writerow(list(row))
    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=events_report.csv"
    return response

def make_plan(event, question):
    name = event["name"]
    count = int(event.get("participants") or 0)
    location = event.get("location") or "the selected venue"
    tasks = [
        "Define objectives and finalize the agenda",
        "Prepare a budget and track expected expenses",
        f"Confirm venue, seating and facilities at {location}",
        "Prepare the guest list and send invitations",
        "Arrange equipment, materials and refreshments",
        "Assign responsibilities to team members",
        "Confirm registrations and attendance",
        "Prepare an event-day checklist",
        "Collect feedback and review expenses after the event"
    ]
    if count > 100:
        tasks.append("Arrange additional registration desks, seating and volunteers")
    if count > 300:
        tasks.append("Plan crowd management, first aid and emergency arrangements")
    schedule = [
        "Planning stage: finalize objectives, budget and team",
        "One week before: confirm venue, guests and equipment",
        "One day before: check materials and technical arrangements",
        f"Event day ({event.get('date') or 'date not set'}): registration and activities",
        "After the event: collect feedback and review results"
    ]
    q = question.lower()
    if any(w in q for w in ("budget", "cost", "expense")):
        answer = f"Budget plan for {name}:\n1. Estimate venue and equipment costs.\n2. Calculate food and participant material costs.\n3. Include transport, promotion and contingency costs.\n4. Record actual expenses after the event.\nExact amounts are not available because no budget was entered."
    elif any(w in q for w in ("schedule", "timeline", "when")):
        answer = "Suggested schedule for " + name + ":\n• " + "\n• ".join(schedule)
    elif any(w in q for w in ("task", "todo", "prepare", "work")):
        answer = "Preparation tasks for " + name + ":\n• " + "\n• ".join(tasks)
    elif any(w in q for w in ("idea", "suggest", "improve", "tip")):
        answer = f"Suggestions for {name}:\n• Use online registration and attendance tracking.\n• Assign one coordinator for each activity.\n• Share the agenda with participants in advance.\n• Keep a backup plan for venue and equipment.\n• Collect feedback after the event."
    else:
        answer = (f"Event Plan: {name}\nType: {event.get('type') or 'General'}\nDate: {event.get('date') or 'Not specified'}\n"
                  f"Time: {event.get('time') or 'Not specified'}\nLocation: {location}\nParticipants: {count}\n\n"
                  "Preparation Tasks:\n• " + "\n• ".join(tasks) + "\n\nSuggested Schedule:\n• " + "\n• ".join(schedule))
    return {"answer": answer, "tasks": tasks, "schedule": schedule}

@app.route("/api/ai-event-plan", methods=["POST"])
@app.route("/ai-agent", methods=["POST"])
def ai_plan_api():
    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()
    event_name = str(data.get("event_name", "")).strip()
    if not question and not event_name:
        return jsonify(status="error", response="Enter an event name or a question."), 400
    search_text = (event_name + " " + question).lower()
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY id DESC").fetchall()
    events = [dict(r) for r in rows]
    matched = next((e for e in events if e["name"].strip().lower() in search_text), None)
    if matched is None and event_name:
        matched = {"name": event_name, "type": data.get("type",""), "date": data.get("date",""),
                   "time": data.get("time",""), "location": data.get("location",""),
                   "participants": data.get("participants",0), "description": data.get("description","")}
    if matched is None and len(events) == 1:
        matched = events[0]
    if matched is None:
        return jsonify(status="success", response="I couldn't identify the event. Enter an exact saved event name, or create an event first.",
                       saved_events=[e["name"] for e in events])
    plan = make_plan(matched, question or "create a plan")
    return jsonify(status="success", event=matched, response=plan["answer"], tasks=plan["tasks"], schedule=plan["schedule"])

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Salesforce AI Event Manager</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;font-family:Arial,sans-serif}body{background:#f4f7fb;color:#1f2937}
.sidebar{position:fixed;left:0;top:0;width:240px;height:100vh;background:#0b5cab;color:white;padding:25px 15px;z-index:3}
.logo{font-size:21px;font-weight:bold;margin-bottom:35px;text-align:center}.sidebar ul{list-style:none}
.sidebar li{padding:14px 15px;margin:7px 0;border-radius:8px;cursor:pointer}.sidebar li:hover,.sidebar li.active{background:rgba(255,255,255,.18)}
.main{margin-left:240px;padding:30px;max-width:1500px}.topbar{display:flex;justify-content:space-between;align-items:center;gap:15px;margin-bottom:25px;flex-wrap:wrap}
h1{font-size:29px;color:#16325c;margin-bottom:7px} .muted{color:#64748b}.view{display:none}.view.active{display:block}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:22px 0}.stat,.card{background:white;border-radius:14px;padding:22px;box-shadow:0 4px 15px rgba(0,0,0,.06);margin-bottom:20px}
.stat .num{font-size:30px;font-weight:bold;color:#0b5cab;margin-top:10px}.card h2{color:#16325c;margin-bottom:18px}
button,.btn{border:0;border-radius:7px;background:#0b5cab;color:white;padding:11px 16px;cursor:pointer;font-size:14px;text-decoration:none;display:inline-block}
button:hover,.btn:hover{background:#084b8a}.secondary{background:#64748b}.danger{background:#dc2626}.small{padding:7px 10px;font-size:13px}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.field{display:flex;flex-direction:column;gap:7px}.full{grid-column:1/-1}
input,select,textarea{padding:11px;border:1px solid #d1d5db;border-radius:7px;font-size:15px;width:100%;background:white}textarea{min-height:90px;resize:vertical}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}.toolbar input,.toolbar select{max-width:260px}
table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid #e5e7eb;font-size:14px}th{background:#f8fafc;color:#334155}
.table-wrap{overflow-x:auto;border-radius:10px}.badge{padding:5px 9px;border-radius:20px;font-size:12px;background:#dbeafe;color:#1d4ed8}.badge.Completed{background:#dcfce7;color:#166534}.badge.Cancelled{background:#fee2e2;color:#991b1b}
.message{margin:12px 0;padding:12px;border-radius:7px;white-space:pre-wrap;display:none}.ok{background:#dcfce7;color:#166534}.err{background:#fee2e2;color:#991b1b}
.result{background:#f8fafc;border-left:4px solid #0b5cab;padding:18px;border-radius:7px;white-space:pre-wrap;line-height:1.6;margin-top:15px}
.barrow{display:grid;grid-template-columns:130px 1fr 35px;gap:10px;align-items:center;margin:12px 0}.bar{height:12px;background:#e2e8f0;border-radius:10px;overflow:hidden}.bar span{display:block;height:100%;background:#0b5cab}
@media(max-width:950px){.cards{grid-template-columns:repeat(2,1fr)}.sidebar{width:190px}.main{margin-left:190px;padding:20px}}
@media(max-width:620px){.sidebar{position:relative;width:100%;height:auto;padding:12px}.logo{margin-bottom:10px}.sidebar ul{display:grid;grid-template-columns:1fr 1fr}.sidebar li{padding:10px;margin:3px;font-size:13px}.main{margin-left:0;padding:15px}.cards{grid-template-columns:1fr 1fr}.form-grid{grid-template-columns:1fr}.full{grid-column:auto}h1{font-size:24px}.stat{padding:15px}.stat .num{font-size:24px}}
</style></head>
<body>
<aside class="sidebar"><div class="logo">⚡ Salesforce AI</div><ul>
<li class="active" data-view="dashboard">🏠 Dashboard</li><li data-view="create">📅 Create Event</li><li data-view="events">📋 My Events</li><li data-view="agent">🤖 AI Event Agent</li><li data-view="analytics">📊 Analytics</li><li data-view="settings">⚙️ Settings</li>
</ul></aside>
<main class="main">
<div class="topbar"><div><h1 id="pageTitle">Dashboard</h1><p class="muted">Plan, manage and track your events in one place.</p></div><button onclick="showView('create')">＋ Create Event</button></div>
<div id="notice" class="message"></div>

<section id="dashboard" class="view active">
<div class="cards">
<div class="stat">Total Events<div class="num" id="total">0</div></div>
<div class="stat">Upcoming Events<div class="num" id="upcoming">0</div></div>
<div class="stat">Completed Events<div class="num" id="completed">0</div></div>
<div class="stat">Participants<div class="num" id="participantsTotal">0</div></div>
</div>
<div class="card"><h2>Recent Events</h2><div id="recentTable"></div><div class="actions"><button onclick="showView('events')">View All Events</button><a class="btn" href="/api/export.csv">Download CSV Report</a></div></div>
</section>

<section id="create" class="view"><div class="card"><h2 id="formHeading">Create New Event</h2>
<form id="eventForm"><input type="hidden" id="eventId"><div class="form-grid">
<div class="field"><label>Event Name *</label><input id="name" required maxlength="120" placeholder="Enter event name"></div>
<div class="field"><label>Event Type</label><select id="type"><option value="">Select type</option><option>Conference</option><option>Workshop</option><option>Seminar</option><option>Meeting</option><option>Exhibition</option><option>Webinar</option><option>Other</option></select></div>
<div class="field"><label>Event Date</label><input id="date" type="date"></div><div class="field"><label>Event Time</label><input id="time" type="time"></div>
<div class="field"><label>Location</label><input id="location" placeholder="Venue or online"></div><div class="field"><label>Expected Participants</label><input id="participants" type="number" min="0" value="0"></div>
<div class="field"><label>Status</label><select id="status"><option>Upcoming</option><option>Completed</option><option>Cancelled</option></select></div>
<div class="field full"><label>Description</label><textarea id="description" placeholder="Describe your event..."></textarea></div></div>
<div class="actions"><button type="submit" id="saveBtn">Save Event</button><button type="button" class="secondary" onclick="resetForm()">Clear</button></div></form></div></section>

<section id="events" class="view"><div class="card"><h2>My Events</h2><div class="toolbar"><input id="search" placeholder="Search by event name or location"><select id="filterStatus"><option value="">All statuses</option><option>Upcoming</option><option>Completed</option><option>Cancelled</option></select><select id="filterType"><option value="">All types</option><option>Conference</option><option>Workshop</option><option>Seminar</option><option>Meeting</option><option>Exhibition</option><option>Webinar</option><option>Other</option></select><a class="btn" href="/api/export.csv">Export CSV</a></div><div id="eventsTable"></div></div></section>

<section id="agent" class="view"><div class="card"><h2>Generate Event Plan</h2><p class="muted" style="margin-bottom:16px">Enter a saved event name and ask for tasks, schedule, budget guidance or ideas. This built-in planner uses rules; it does not require an external AI API.</p>
<form id="aiForm"><div class="form-grid"><div class="field"><label>Event Name (optional if only one event exists)</label><input id="aiEventName" placeholder="Enter exact saved event name"></div><div class="field"><label>Question / Plan Type</label><select id="aiQuestion"><option value="create a complete event plan">Complete event plan</option><option value="give me preparation tasks">Preparation tasks</option><option value="create a schedule timeline">Schedule / timeline</option><option value="budget cost expenses">Budget guidance</option><option value="give ideas and suggestions">Ideas and suggestions</option></select></div></div><div class="actions"><button type="submit">✨ Generate Event Plan</button></div></form><div id="aiResult" class="result" style="display:none"></div></div></section>

<section id="analytics" class="view"><div class="cards"><div class="stat">Total Events<div class="num" id="aTotal">0</div></div><div class="stat">Participants<div class="num" id="aParticipants">0</div></div><div class="stat">Upcoming<div class="num" id="aUpcoming">0</div></div><div class="stat">Completed<div class="num" id="aCompleted">0</div></div></div><div class="card"><h2>Events by Type</h2><div id="typeChart"></div></div><div class="card"><h2>Events by Status</h2><div id="statusChart"></div><a class="btn" href="/api/export.csv">Download Full CSV Report</a></div></section>

<section id="settings" class="view"><div class="card"><h2>Settings</h2><form id="settingsForm"><div class="form-grid"><div class="field"><label>Admin Name</label><input id="adminName" required></div><div class="field"><label>Admin Email</label><input id="adminEmail" type="email" placeholder="admin@example.com"></div></div><div class="actions"><button type="submit">Save Settings</button></div></form><p class="muted" style="margin-top:16px">These are profile settings only. This assignment version does not include user registration or password-based authentication.</p></div></section>
</main>
<script>
let allEvents=[];
const $=id=>document.getElementById(id);
function notice(text,error=false){const n=$('notice');n.textContent=text;n.className='message '+(error?'err':'ok');n.style.display='block';setTimeout(()=>n.style.display='none',3500)}
function showView(id){document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===id));document.querySelectorAll('.sidebar li').forEach(li=>li.classList.toggle('active',li.dataset.view===id));const titles={dashboard:'Dashboard',create:'Create Event',events:'My Events',agent:'AI Event Agent',analytics:'Analytics',settings:'Settings'};$('pageTitle').textContent=titles[id]||'Dashboard';if(id==='create'&&!$('eventId').value)resetForm();if(id==='events')renderEvents();if(id==='analytics')loadAnalytics();if(id==='settings')loadSettings();if(id==='dashboard')loadDashboard();window.scrollTo({top:0,behavior:'smooth'})}
document.querySelectorAll('.sidebar li[data-view]').forEach(li=>li.addEventListener('click',()=>showView(li.dataset.view)));
async function api(url,options={}){const r=await fetch(url,{headers:{'Content-Type':'application/json',...(options.headers||{})},...options});let d={};try{d=await r.json()}catch(e){}if(!r.ok)throw new Error(d.message||'Request failed');return d}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function eventTable(events){if(!events.length)return '<p class="muted">No events found. Create your first event.</p>';return `<div class="table-wrap"><table><thead><tr><th>Event</th><th>Date</th><th>Location</th><th>Participants</th><th>Status</th><th>Actions</th></tr></thead><tbody>${events.map(e=>`<tr><td><b>${esc(e.name)}</b><br><span class="muted">${esc(e.type||'Other')}</span></td><td>${esc(e.date||'—')} ${esc(e.time||'')}</td><td>${esc(e.location||'—')}</td><td>${e.participants||0}</td><td><span class="badge ${esc(e.status||'Upcoming')}">${esc(e.status||'Upcoming')}</span></td><td><button class="small" onclick="editEvent(${e.id})">Edit</button> <button class="small danger" onclick="deleteEvent(${e.id})">Delete</button></td></tr>`).join('')}</tbody></table></div>`}
async function loadEvents(){try{const d=await api('/api/events');allEvents=d.events||[];renderEvents();loadDashboard()}catch(e){notice(e.message,true)}}
function renderEvents(){const q=($('search')?.value||'').toLowerCase();const st=$('filterStatus')?.value||'';const ty=$('filterType')?.value||'';const list=allEvents.filter(e=>(!q||(e.name+' '+e.location+' '+e.description).toLowerCase().includes(q))&&(!st||e.status===st)&&(!ty||e.type===ty));if($('eventsTable'))$('eventsTable').innerHTML=eventTable(list)}
function loadDashboard(){const today=new Date().toISOString().slice(0,10);$('total').textContent=allEvents.length;$('participantsTotal').textContent=allEvents.reduce((s,e)=>s+Number(e.participants||0),0);$('upcoming').textContent=allEvents.filter(e=>e.status==='Upcoming'&&(!e.date||e.date>=today)).length;$('completed').textContent=allEvents.filter(e=>e.status==='Completed'||(e.date&&e.date<today)).length;$('recentTable').innerHTML=eventTable(allEvents.slice(0,5))}
function resetForm(){$('eventForm').reset();$('eventId').value='';$('participants').value=0;$('formHeading').textContent='Create New Event';$('saveBtn').textContent='Save Event'}
$('eventForm').addEventListener('submit',async ev=>{ev.preventDefault();const id=$('eventId').value;const data={name:$('name').value,type:$('type').value,date:$('date').value,time:$('time').value,location:$('location').value,participants:$('participants').value,description:$('description').value,status:$('status').value};try{await api(id?'/api/events/'+id:'/api/events',{method:id?'PUT':'POST',body:JSON.stringify(data)});notice(id?'Event updated successfully':'Event created successfully');resetForm();await loadEvents();showView('events')}catch(e){notice(e.message,true)}});
function editEvent(id){const e=allEvents.find(x=>x.id===id);if(!e)return;$('eventId').value=e.id;['name','type','date','time','location','participants','description','status'].forEach(k=>$(k).value=e[k]??'');$('formHeading').textContent='Edit Event';$('saveBtn').textContent='Update Event';showView('create')}
async function deleteEvent(id){if(!confirm('Delete this event? This cannot be undone.'))return;try{await api('/api/events/'+id,{method:'DELETE'});notice('Event deleted');await loadEvents();if($('events').classList.contains('active'))renderEvents()}catch(e){notice(e.message,true)}}
$('search').addEventListener('input',renderEvents);$('filterStatus').addEventListener('change',renderEvents);$('filterType').addEventListener('change',renderEvents);
$('aiForm').addEventListener('submit',async ev=>{ev.preventDefault();$('aiResult').style.display='block';$('aiResult').textContent='Preparing event plan...';try{const d=await api('/api/ai-event-plan',{method:'POST',body:JSON.stringify({event_name:$('aiEventName').value,question:$('aiQuestion').value})});$('aiResult').textContent=d.response||'No plan available.'}catch(e){$('aiResult').textContent=e.message}});
function chartHtml(items){if(!items||!items.length)return '<p class="muted">No data yet. Create events to see analytics.</p>';const max=Math.max(...items.map(x=>x.total),1);return items.map(x=>`<div class="barrow"><span>${esc(x.label)}</span><div class="bar"><span style="width:${Math.max(3,x.total/max*100)}%"></span></div><b>${x.total}</b></div>`).join('')}
async function loadAnalytics(){try{const d=await api('/api/analytics');$('aTotal').textContent=d.total_events;$('aParticipants').textContent=d.total_participants;$('aUpcoming').textContent=d.upcoming_events;$('aCompleted').textContent=d.completed_events;$('typeChart').innerHTML=chartHtml(d.events_by_type);$('statusChart').innerHTML=chartHtml(d.events_by_status)}catch(e){notice(e.message,true)}}
async function loadSettings(){try{const d=await api('/api/settings');$('adminName').value=d.settings.admin_name||'';$('adminEmail').value=d.settings.admin_email||''}catch(e){notice(e.message,true)}}
$('settingsForm').addEventListener('submit',async ev=>{ev.preventDefault();try{await api('/api/settings',{method:'POST',body:JSON.stringify({admin_name:$('adminName').value,admin_email:$('adminEmail').value})});notice('Settings saved successfully')}catch(e){notice(e.message,true)}});
loadEvents();
</script></body></html>"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"Salesforce AI Event Manager running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
