import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("AGENT_DB_PATH", str(APP_DIR / "agent_data.db")))
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_ACTIONS_PER_TURN = 3


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = connect_db()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            title TEXT NOT NULL,
            action_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            decided_at TEXT,
            decision_note TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        conn.commit()
    finally:
        conn.close()


def audit(action_type, description, status):
    conn = connect_db()
    try:
        conn.execute(
            "INSERT INTO audit_log(action_type, description, status, created_at) VALUES(?,?,?,?)",
            (action_type[:80], description[:2000], status[:80], now_utc()),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_rows(table, limit=20):
    allowed = {"tasks", "notes", "approvals", "audit_log", "chat_messages"}
    if table not in allowed:
        raise ValueError("Unsupported table")
    conn = connect_db()
    try:
        return conn.execute(
            f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    finally:
        conn.close()


def fetch_chat(limit=40):
    conn = connect_db()
    try:
        rows = conn.execute(
            "SELECT role, content, created_at FROM chat_messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return list(reversed(rows))
    finally:
        conn.close()


def save_chat(role, content):
    conn = connect_db()
    try:
        conn.execute(
            "INSERT INTO chat_messages(role, content, created_at) VALUES(?,?,?)",
            (role, content[:12000], now_utc()),
        )
        conn.commit()
    finally:
        conn.close()


def validate_plan(plan):
    """Keep only known action types and bounded, correctly typed fields."""
    if not isinstance(plan, dict):
        return {"reply": "The AI returned an invalid plan. No actions were queued.", "actions": []}

    reply = plan.get("reply", "I prepared a response.")
    if not isinstance(reply, str):
        reply = "I prepared a response."

    actions = plan.get("actions", [])
    if not isinstance(actions, list):
        return {"reply": reply[:4000], "actions": []}

    safe = []
    for action in actions[:MAX_ACTIONS_PER_TURN]:
        if not isinstance(action, dict):
            continue
        kind = action.get("type")
        title = action.get("title", "")
        if not isinstance(title, str):
            continue
        title = title.strip()[:160]
        if kind == "create_task" and title:
            details = action.get("details", "")
            if not isinstance(details, str):
                details = ""
            safe.append({"type": kind, "title": title, "details": details[:2000]})
        elif kind == "save_note" and title:
            content = action.get("content", "")
            if isinstance(content, str):
                safe.append({"type": kind, "title": title, "content": content[:5000]})
        elif kind == "draft_message" and title:
            content = action.get("content", "")
            recipient = action.get("recipient", "Not specified")
            if isinstance(content, str):
                if not isinstance(recipient, str):
                    recipient = "Not specified"
                safe.append({
                    "type": kind,
                    "title": title,
                    "content": content[:5000],
                    "recipient": recipient[:300],
                })
    return {"reply": reply[:4000], "actions": safe}


def demo_plan(user_text):
    text = user_text.strip()
    lower = text.lower()
    task_prefixes = ("create a task to ", "create a task: ", "add a task to ", "add a task: ", "remind me to ")
    note_prefixes = ("save a note:", "save note:", "remember this:")
    if any(lower.startswith(prefix) for prefix in task_prefixes):
        prefix = next(p for p in task_prefixes if lower.startswith(p))
        title = text[len(prefix):].strip()
        if title:
            return {
                "reply": "I prepared a task proposal. Review and approve it before it is saved.",
                "actions": [{"type": "create_task", "title": title, "details": "Created from chat request."}],
            }
    if any(lower.startswith(prefix) for prefix in note_prefixes):
        prefix = next(p for p in note_prefixes if lower.startswith(p))
        content = text[len(prefix):].strip()
        if content:
            return {
                "reply": "I prepared a note proposal. Review and approve it before it is saved.",
                "actions": [{"type": "save_note", "title": "Note from chat", "content": content}],
            }
    if "draft a message" in lower or "write an email" in lower or "draft an email" in lower:
        return {
            "reply": "I prepared a message draft proposal. It will not be sent by this app.",
            "actions": [{
                "type": "draft_message",
                "title": "Message draft",
                "content": text,
                "recipient": "Not specified",
            }],
        }
    return {
        "reply": (
            "Demo mode is active. Try “Create a task to review my goals” or "
            "“Save a note: My priority is to finish the prototype”. "
            "AI-generated planning requires an OpenAI API key."
        ),
        "actions": [],
    }


def plan_with_openai(user_text, history):
    from openai import OpenAI

    system_prompt = """
You are a careful personal assistant. Return ONLY a valid JSON object with:
{"reply":"short helpful answer","actions":[...]}
Allowed action shapes:
{"type":"create_task","title":"...","details":"..."}
{"type":"save_note","title":"...","content":"..."}
{"type":"draft_message","title":"...","content":"...","recipient":"..."}
Rules:
- Propose an action only when the user clearly asks for it.
- The app queues every proposed action for explicit human approval. Do not say an action is completed.
- This app never sends emails or messages; an approved message draft is saved as a local note.
- Do not invent facts, recipients, deadlines, or completed actions.
- Ask a question if essential information is missing.
- Return no more than 3 actions.
- Never propose shell commands, code execution, purchases, deletions, or external actions.
"""
    messages = [{"role": "system", "content": system_prompt}]
    for item in history[-8:]:
        if item["role"] in ("user", "assistant"):
            messages.append({"role": item["role"], "content": item["content"][:4000]})
    messages.append({"role": "user", "content": user_text[:4000]})

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=1200,
    )
    raw = response.choices[0].message.content or "{}"
    try:
        return validate_plan(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return {"reply": "I couldn't safely interpret the AI response. No actions were queued.", "actions": []}


def queue_approval(action):
    conn = connect_db()
    try:
        cur = conn.execute(
            "INSERT INTO approvals(action_type, title, action_json, status, created_at) VALUES(?,?,?,?,?)",
            (action["type"], action.get("title", "Untitled action"), json.dumps(action), "pending", now_utc()),
        )
        approval_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    audit(action["type"], f"Approval #{approval_id}: {action.get('title', '')}", "awaiting_approval")
    return approval_id


def decide_approval(approval_id, approve):
    conn = connect_db()
    try:
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            return "Approval item not found."
        if row["status"] != "pending":
            return f"This item was already {row['status']}."
        if not approve:
            conn.execute(
                "UPDATE approvals SET status='rejected', decided_at=?, decision_note=? WHERE id=? AND status='pending'",
                (now_utc(), "Rejected by user", approval_id),
            )
            conn.commit()
            audit(row["action_type"], f"Approval #{approval_id}: {row['title']}", "rejected")
            return "Proposal rejected. No action was executed."

        action = json.loads(row["action_json"])
        kind = action.get("type")
        now = now_utc()
        if kind == "create_task":
            conn.execute(
                "INSERT INTO tasks(title, details, status, created_at) VALUES(?,?,?,?)",
                (action["title"][:160], action.get("details", "")[:2000], "open", now),
            )
            result = "approved_and_task_created"
        elif kind == "save_note":
            conn.execute(
                "INSERT INTO notes(title, content, created_at) VALUES(?,?,?)",
                (action["title"][:160], action["content"][:5000], now),
            )
            result = "approved_and_note_saved"
        elif kind == "draft_message":
            content = f"Recipient: {action.get('recipient', 'Not specified')}\n\n{action['content']}"
            conn.execute(
                "INSERT INTO notes(title, content, created_at) VALUES(?,?,?)",
                (f"Approved draft: {action['title']}"[:160], content[:5000], now),
            )
            result = "approved_draft_saved_as_note_not_sent"
        else:
            conn.rollback()
            return "Unsupported action. Nothing was executed."
        conn.execute(
            "UPDATE approvals SET status='approved', decided_at=?, decision_note=? WHERE id=? AND status='pending'",
            (now, result, approval_id),
        )
        conn.execute(
            "INSERT INTO audit_log(action_type, description, status, created_at) VALUES(?,?,?,?)",
            (kind, f"Approval #{approval_id}: {row['title']}", result, now),
        )
        conn.commit()
        return {
            "approved_and_task_created": "Approved. Task saved.",
            "approved_and_note_saved": "Approved. Note saved.",
            "approved_draft_saved_as_note_not_sent": "Approved. Draft saved as a note; no message was sent.",
        }[result]
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        conn.rollback()
        return "This proposal is invalid. Nothing was executed."
    finally:
        conn.close()


def render_sidebar():
    with st.sidebar:
        st.header("Agent status")
        if os.getenv("OPENAI_API_KEY"):
            st.success(f"AI mode enabled · {MODEL}")
        else:
            st.warning("Demo mode · no API key configured")
        st.caption("Every proposed action requires your approval.")
        st.markdown("**Available actions**")
        st.markdown("- Propose a task")
        st.markdown("- Propose a saved note")
        st.markdown("- Prepare a message draft")
        st.markdown("---")
        st.markdown("**Safety limits**")
        st.caption("No sending messages, purchases, shell commands, or arbitrary file access.")
        st.caption("This prototype has no login. Do not expose it publicly with private data.")


def main():
    st.set_page_config(page_title="Personal AI Agent", page_icon="🤖", layout="wide")
    init_db()
    st.title("🤖 Personal AI Agent")
    st.caption("Chat · Persistent memory · Task proposals · Human approval · Audit trail")
    render_sidebar()


    st.divider()
    with st.expander("Booking Assistant · Food, trains, flights and buses", expanded=True):
        st.write(
            "This version prepares a checklist and opens the provider website. "
            "It does not access your account, place orders, or purchase tickets."
        )
        booking_type = st.selectbox(
            "What would you like to arrange?",
            ["Swiggy food delivery", "Train ticket (IRCTC)", "Flight ticket", "Bus ticket"],
            key="booking_type",
        )

        if booking_type == "Swiggy food delivery":
            st.caption("Choose food and delivery location on Swiggy. Check delivery address, items, fees and total before you place the order.")
            st.link_button("Open official Swiggy website", "https://www.swiggy.com/", use_container_width=True)
        elif booking_type == "Train ticket (IRCTC)":
            st.caption("Search the route, date, class and availability. Verify passenger details and fare before confirming.")
            st.link_button("Open official IRCTC e-ticket booking guide", "https://contents.irctc.co.in/en/bookEticket.html", use_container_width=True)
            st.link_button("Open IRCTC website", "https://www.irctc.co.in/nget/train-search", use_container_width=True)
        elif booking_type == "Flight ticket":
            st.caption("Compare flight times, baggage allowance, passenger details, total fare and cancellation rules.")
            st.link_button("Open MakeMyTrip flights", "https://www.makemytrip.com/flights/", use_container_width=True)
        else:
            st.caption("Compare operator, boarding/drop points, seat, baggage rules, total fare and cancellation policy.")
            st.link_button("Open redBus", "https://www.redbus.in/", use_container_width=True)

        st.markdown("**Prepare your booking details**")
        if booking_type == "Swiggy food delivery":
            food = st.text_input("Food or restaurant preference", placeholder="e.g., 2 masala dosas")
            delivery_area = st.text_input("Delivery area or landmark", placeholder="Enter only what you are comfortable sharing")
            budget = st.text_input("Budget (optional)", placeholder="e.g., ₹400")
            if st.button("Prepare food-order checklist", key="prep_food"):
                summary = f"Service: Swiggy\nFood preference: {food or 'Not specified'}\nDelivery area: {delivery_area or 'Enter directly on Swiggy'}\nBudget: {budget or 'Not specified'}\nBefore ordering: verify restaurant, items, address, fees, delivery time and final total."
                st.session_state["booking_checklist"] = summary
                audit("booking_checklist", "Prepared Swiggy food-order checklist", "prepared")
        else:
            from_place = st.text_input("From", placeholder="City or station", key="booking_from")
            to_place = st.text_input("To", placeholder="City or station", key="booking_to")
            travel_date = st.date_input("Travel date", key="booking_date")
            passengers = st.number_input("Number of passengers", min_value=1, max_value=12, value=1, step=1, key="booking_passengers")
            budget = st.text_input("Budget (optional)", placeholder="e.g., ₹5,000", key="booking_budget")
            if st.button("Prepare ticket checklist", key="prep_ticket"):
                summary = (
                    f"Service: {booking_type}\nFrom: {from_place or 'Not specified'}\n"
                    f"To: {to_place or 'Not specified'}\nDate: {travel_date.isoformat()}\n"
                    f"Passengers: {passengers}\nBudget: {budget or 'Not specified'}\n"
                    "Before booking: verify schedule, passenger names, fare and fees, baggage/class, "
                    "cancellation terms, and all details on the provider's final review page."
                )
                st.session_state["booking_checklist"] = summary
                audit("booking_checklist", f"Prepared {booking_type} checklist", "prepared")

        if st.session_state.get("booking_checklist"):
            st.text_area("Your booking checklist", value=st.session_state["booking_checklist"], height=180)
            st.caption("Use this checklist while booking on the official/provider website. Never share passwords, OTPs, or payment credentials with this assistant.")

    chat_col, work_col = st.columns([1.6, 1])
    with chat_col:
        st.subheader("Chat")
        history = fetch_chat(40)
        for message in history:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
        prompt = st.chat_input("Ask a question or propose a task/note…")
        if prompt:
            save_chat("user", prompt)
            try:
                if os.getenv("OPENAI_API_KEY"):
                    plan = plan_with_openai(prompt, history)
                else:
                    plan = validate_plan(demo_plan(prompt))
                queued = []
                for action in plan["actions"]:
                    approval_id = queue_approval(action)
                    queued.append(f"- **Approval #{approval_id}**: {action['type']} — {action.get('title', '')}")
                reply = plan["reply"]
                if queued:
                    reply += "\n\n**Waiting for your review:**\n" + "\n".join(queued)
                save_chat("assistant", reply)
            except Exception as exc:
                # Do not expose request contents, API key, or raw provider error to the UI.
                error_message = (
                    f"The request failed ({type(exc).__name__}). No action was approved automatically. "
                    "Check your API key, network connection, and API billing, or use demo mode."
                )
                save_chat("assistant", error_message)
                audit("chat_request", type(exc).__name__, "failed")
            st.rerun()

    with work_col:
        st.subheader("Approval inbox")
        approvals = list(reversed(fetch_rows("approvals", 50)))
        pending = [item for item in approvals if item["status"] == "pending"]
        if not pending:
            st.info("No pending proposals.")
        for item in pending:
            action = json.loads(item["action_json"])
            with st.container(border=True):
                st.markdown(f"**#{item['id']} · {item['title']}**")
                st.caption(f"Type: {item['action_type']} · Created: {item['created_at']}")
                if action["type"] == "create_task":
                    st.write("Task details:", action.get("details", "") or "None")
                elif action["type"] == "save_note":
                    st.write(action.get("content", ""))
                elif action["type"] == "draft_message":
                    st.caption(f"Recipient: {action.get('recipient', 'Not specified')}")
                    st.write(action.get("content", ""))
                    st.warning("Approving saves this draft as a note. It does not send a message.")
                col1, col2 = st.columns(2)
                if col1.button("Approve", key=f"approve_{item['id']}", use_container_width=True):
                    st.session_state[f"notice_{item['id']}"] = decide_approval(item["id"], True)
                    st.rerun()
                if col2.button("Reject", key=f"reject_{item['id']}", use_container_width=True):
                    st.session_state[f"notice_{item['id']}"] = decide_approval(item["id"], False)
                    st.rerun()
        for item in approvals:
            notice_key = f"notice_{item['id']}"
            if notice_key in st.session_state:
                st.caption(st.session_state[notice_key])

        st.divider()
        st.subheader("Recent tasks")
        tasks = fetch_rows("tasks", 8)
        if not tasks:
            st.caption("No saved tasks yet.")
        for task in tasks:
            st.markdown(f"**{task['title']}**")
            if task["details"]:
                st.caption(task["details"])
            st.caption(f"{task['status']} · {task['created_at']}")

        st.divider()
        st.subheader("Saved notes")
        notes = fetch_rows("notes", 8)
        if not notes:
            st.caption("No saved notes yet.")
        for note in notes:
            with st.expander(note["title"]):
                st.write(note["content"])
                st.caption(note["created_at"])

        with st.expander("Audit log"):
            for event in fetch_rows("audit_log", 20):
                st.caption(f"{event['created_at']} · {event['action_type']} · {event['status']}")
                st.write(event["description"])


if __name__ == "__main__":
    main()
