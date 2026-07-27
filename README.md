# SmartBiz AI

AI Assistant for small businesses - Free AI powered (Groq API)

## Quick Start

### 1. Get Free Groq API Key
- Go to: https://console.groq.com
- Create account
- Go to: API Keys -> Create API Key
- Copy the key

### 2. Install Requirements
```bash
pip install -r requirements.txt
```

### 3. Setup API Key
- Open `.env` file
- Replace `gsk_your_groq_api_key_here` with your real key

### 4. (Optional) Enable email sending
The "Send as email" button in the Email tool sends through your own SMTP account.
Add these to `.env` (Gmail example, use an "app password" not your normal password):
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your_app_password
```
If these are left empty, everything else still works - only the "send" action is disabled
and returns a clear error telling the user it isn't configured yet.

### 5. Run the App
```bash
python app.py
```

### 6. Open Browser
- Go to: http://localhost:5000

## Project Structure
```
smartbiz_ai/
├── app.py              <- Flask Backend
├── requirements.txt    <- Dependencies
├── .env               <- API key + SMTP config
├── smartbiz.db        <- SQLite DB (auto-created)
├── static/
│   └── index.html     <- Full frontend (single file)
└── README.md
```

## Features
- Professional email writing, with a one-click "send as email" action
- Quote/proposal generator
- Meeting summarizer
- Social media post generator
- Smart task manager
- Every tool can be tailored to your business type (restaurant, retail, consulting,
  tech, freelance, health, construction, education, or general) via the domain
  selector in the top bar - this changes the vocabulary and context the AI uses
- 3 languages (AR/FR/EN), full RTL support for Arabic
- Dark/light mode
- Full-screen app layout with a subtle branded background
- Chat history saved per user and grouped by date - click a date in the sidebar
  to see everything you worked on that day
- Clicking a tool in the sidebar resumes today's ongoing conversation for that
  tool instead of clearing it, so a half-finished task is always right where
  you left it
- Free demo mode (no login required): 5 free **tasks**, not 5 messages - a task
  that takes several back-and-forth messages only uses up one slot, and the
  quota only drops when you start something new

## Notes
- Groq API is free - 8,000 requests/day
- SQLite - local database (no setup needed)
- JWT - secure authentication
- Demo task tracking is kept in server memory per IP address, so it resets if
  the server restarts (this is expected for a lightweight demo limiter)
