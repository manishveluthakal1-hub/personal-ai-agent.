# Personal AI Agent — safer starter (v2)

A beginner-friendly, single-user learning prototype built with Python, Streamlit, SQLite, and optional OpenAI API access.

## Features
- Chat interface with persistent chat history in SQLite
- Demo mode that works without an OpenAI API key
- Optional AI-generated plans using `gpt-4o-mini` by default
- Persistent task and note storage
- Persistent approval inbox: every proposed action waits for your approval
- Audit trail for queued, approved, rejected, and failed actions
- Narrow action allowlist: create task, save note, or prepare message draft
- An approved message draft is saved as a note; it is **not sent**

## Important safety note
This is a learning prototype, not a production-ready or multi-user system. It has no authentication or per-user isolation. Do not expose it publicly or use it to store highly sensitive information. Before production, add authentication, authorization, user isolation, rate limits, monitoring, backups, retention controls, security review, and tests.

## Run locally
Requires Python 3.10 or newer.

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Open the local URL printed by Streamlit, usually `http://localhost:8501`.

## Enable AI mode
Set `OPENAI_API_KEY` as an environment variable or through your hosting provider's secret manager. Optionally set `OPENAI_MODEL` (default: `gpt-4o-mini`).

Never put the real key in `app.py`, `.env.example`, screenshots, public repositories, or this chat. API use can cost money and is billed separately from a ChatGPT subscription. Keep usage limits and billing alerts enabled in the API platform where available.

## Replit deployment
1. Import/upload this ZIP as a project.
2. Add `OPENAI_API_KEY` in Replit Secrets only if you want AI mode.
3. Optionally add `OPENAI_MODEL` with value `gpt-4o-mini`.
4. Install dependencies from `requirements.txt`.
5. Configure the run command as:
   `streamlit run app.py --server.address 0.0.0.0 --server.port 8080`
6. Open the forwarded web preview.

If your Replit workspace has reached its usage limit, wait for reset or review available free options before upgrading. Preparing code here does not consume Replit usage. OpenAI API usage is a separate potential cost.

## Demo prompts
- `Create a task to review my weekly goals`
- `Save a note: My priority is to finish the prototype`
- `Draft a message to my team explaining that the meeting moved to Friday`
- `Help me plan a focused workday`

In demo mode, task/note/draft detection is intentionally simple. AI-generated reasoning requires the API key.

## How approvals work
1. The assistant proposes an allowed action.
2. The proposal is stored in SQLite with status `pending`.
3. You choose Approve or Reject.
4. Approving a task creates a task; approving a note saves a note; approving a message draft saves it as a note only.
5. The decision is recorded in the audit log.

The app does not send messages, make purchases, execute shell commands, or access arbitrary files.


## Booking Assistant (provider handoff)
The app includes a simple booking checklist for Swiggy, IRCTC trains, flights and buses. It opens provider websites and helps you organise details, but it does not log in, access live availability through an API, place orders, or buy tickets. Review the final details and complete the transaction yourself on the provider's site. Provider websites and flows can change.


## Run the basic tests
After installing dependencies, run this from the project directory:

```bash
python -m unittest discover -s tests -v
```

The tests use a temporary SQLite database. They do not call OpenAI or place real orders/bookings.
