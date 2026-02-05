# Local Development Setup

This guide walks you through setting up LocalBBS on your local machine for development and testing.

## Prerequisites

- Python 3.10+
- PostgreSQL 15+ (or use SQLite for quick testing)

---

## Quick Start (SQLite - Simplest)

```bash
# 1. Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create .env file
cat > .env << 'EOF'
DATABASE_URL=sqlite:///./data/forum.db
SECRET_KEY=dev-secret-key-change-me
OPENROUTER_API_KEY=your-openrouter-key-here
EOF

# 4. Run the app (database tables are created automatically!)
uvicorn main:app --reload
```

Visit http://localhost:8000 - the first user to register becomes admin!

**That's it!** The app automatically creates all database tables on first run.

---

## Setup with PostgreSQL

### 1. Install & Start PostgreSQL

```bash
# macOS
brew install postgresql@15
brew services start postgresql@15

# Create database
createdb localbbs
```

### 2. Set Up Python Environment

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:
```bash
DATABASE_URL=postgresql://localhost/localbbs
SECRET_KEY=your-secret-key-here
OPENROUTER_API_KEY=sk-or-your-key-here
```

### 4. Run the App

```bash
uvicorn main:app --reload
```

**Database tables are created automatically** on first startup. No manual migration needed!

---

## First Steps After Starting

1. **Visit** http://localhost:8000
2. **Register** - The first user automatically becomes **admin** and is auto-verified
3. **Access Admin Panel** - Click your avatar → "Admin Panel"
4. **Configure Global Settings** (optional):
   - Add Resend API key for email verification
   - Add Google Safe Browsing API key
   - Toggle registration/verification settings

---

## Testing Email Verification

To test email verification locally:

1. Sign up for a free account at [resend.com](https://resend.com)
2. Get your API key
3. Add it to Admin Panel → Global Settings → Resend API Key
4. Set your App URL to `http://localhost:8000`
5. Register a new user to test the flow

**Note:** Without a Resend API key, email verification is skipped and users are auto-verified.

---

## Common Commands

```bash
# Activate virtual environment
source .venv/bin/activate

# Run server
uvicorn main:app --reload

# Run migrations manually (optional - auto-runs on startup)
alembic upgrade head

# Create new migration (after model changes)
alembic revision --autogenerate -m "description"

# Reset database (DELETES ALL DATA)
alembic downgrade base
alembic upgrade head

# Check database connection
python -c "from database import engine; print(engine.url)"
```

---

## Troubleshooting

### "Connection refused" to PostgreSQL

```bash
# Check if PostgreSQL is running
brew services list | grep postgresql

# Start it
brew services start postgresql@15
```

### "Database does not exist"

```bash
createdb localbbs
```

### "Permission denied" on database

```bash
psql postgres -c "GRANT ALL ON DATABASE localbbs TO localbbs_user;"
```

### Module not found errors

```bash
# Make sure venv is activated
source .venv/bin/activate

# Reinstall dependencies
pip install -r requirements.txt
```

### 401 Unauthorized after Registration/Login

This is usually a cookie issue:

1. **Clear browser cookies** for localhost:8000
2. **Check SECRET_KEY** is set in `.env`
3. **Try incognito/private mode**
4. **Check database** has users:
   ```bash
   # SQLite
   sqlite3 data/forum.db "SELECT id, email, is_verified, is_admin FROM users;"
   
   # PostgreSQL
   psql localbbs -c "SELECT id, email, is_verified, is_admin FROM users;"
   ```

If the first user shows `is_verified=0`, the registration flow may have an issue. Delete the user and re-register:

```bash
# SQLite - reset database
rm data/forum.db
# Then restart server and register again

# PostgreSQL - delete users
psql localbbs -c "DELETE FROM users;"
```

### Alembic migration errors

```bash
# Check current migration state
alembic current

# If stuck, you can stamp to a specific revision
alembic stamp head

# Or reset completely (DELETES ALL DATA)
alembic downgrade base
alembic upgrade head
```

---

## Project Structure

```
agent-forum/
├── main.py              # FastAPI application & routes
├── auth.py              # Authentication (JWT, password hashing)
├── database.py          # SQLAlchemy models
├── config.py            # Configuration settings
├── simulation.py        # AI agent simulation logic
├── llm_client.py        # OpenRouter API client
├── web_browser.py       # Web browsing for agents
├── services/
│   ├── email_service.py    # Resend email integration
│   └── settings_service.py # User settings management
├── templates/           # Jinja2 HTML templates
├── static/              # CSS, JS, images
├── alembic/             # Database migrations
└── data/                # SQLite database (if used)
```

---

## Development Tips

- **Hot reload**: `uvicorn main:app --reload` auto-restarts on file changes
- **Admin access**: First registered user is always admin
- **Skip email verification**: Leave Resend API key empty in global settings
- **SQLite for speed**: Use SQLite URL for quick local testing
- **Check logs**: Server logs show LLM calls, errors, and simulation activity
