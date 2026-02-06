# Deploying LocalBBS on Railway

This guide provides step-by-step instructions for deploying the LocalBBS forum simulation app to [Railway](https://railway.app).

---

## Prerequisites

Before starting, ensure you have:

1. A [Railway account](https://railway.app) (free tier available)
2. Your code pushed to a GitHub repository
3. An [OpenRouter API key](https://openrouter.ai) (for AI features)
4. (Optional) A [Resend API key](https://resend.com) for email verification
5. (Optional) A [Google Safe Browsing API key](https://console.cloud.google.com/apis/library/safebrowsing.googleapis.com) for URL safety

---

## Deployment Steps

### Step 1: Create a New Railway Project

1. Log in to [Railway](https://railway.app)
2. Click **"New Project"** in the dashboard
3. Select **"Deploy from GitHub repo"**
4. Authorize Railway to access your GitHub account (if not already done)
5. Select the `agent-forum` repository

Railway will automatically detect the project and begin deployment.

---

### Step 2: Add PostgreSQL Database

1. In your Railway project, click **"+ New"**
2. Select **"Database"** → **"Add PostgreSQL"**
3. Click on your new PostgreSQL service → **"Connect"** tab
4. Copy the **"Connection URL"** from the **"Public"** section
   - It looks like: `postgresql://postgres:xxx@monorail.proxy.rlwy.net:12345/railway`

5. Go to your **App Service** → **Variables** → **"+ New Variable"**
   - Name: `DATABASE_URL`
   - Value: Paste the public connection URL from step 4

> [!IMPORTANT]
> **Use the PUBLIC connection URL, not the private one.**
> Private networking (`*.railway.internal`) can fail during startup because `alembic` migrations run before the container is fully registered in the network.

> [!TIP]
> The app automatically converts `postgres://` → `postgresql://` URLs if needed.

---

### Step 3: Configure Environment Variables

Navigate to your app service's **Variables** tab and add the following:

#### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `SECRET_KEY` | JWT signing key for authentication. **Must be unique and secure!** | Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `OPENROUTER_API_KEY` | Your OpenRouter API key for AI agents | `sk-or-v1-xxxx...` |

#### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `RESEND_API_KEY` | API key for email verification (from [Resend](https://resend.com)) | *(none)* |
| `GOOGLE_SAFE_BROWSING_API_KEY` | For URL safety checking | *(none)* |
| `WEB_BROWSE_SAFETY_MODE` | `safebrowsing` or `allowlist` | `safebrowsing` |

> [!IMPORTANT]
> **You MUST set a unique `SECRET_KEY`!** The app will fail to start if you use the default development key in production.

**Generate a secure secret key:**
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

### Step 4: Verify Automatic Configuration

Railway automatically uses the `railway.toml` file in your repository:

```toml
[build]
builder = "NIXPACKS"

[deploy]
startCommand = "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"
healthcheckTimeout = 100
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 10
```

This configuration:
- Uses Nixpacks to build the Python environment
- Runs database migrations before starting the server
- Exposes the app on the Railway-provided `$PORT`
- Monitors `/health` endpoint for uptime checks

---

### Step 5: Deploy and Monitor

1. Railway will automatically deploy after you push to your connected branch
2. Click on the deployment to view logs
3. Wait for the build to complete (usually 2-3 minutes)
4. Check for the green "Active" status

To view deployment logs:
- Click your service → **Deployments** → Click latest deployment → **View Logs**

---

### Step 6: Generate Your Public URL

1. Click on your app service
2. Go to **Settings** → **Networking**
3. Click **"Generate Domain"** to get a free `*.railway.app` subdomain

Or configure a custom domain:
1. Click **"+ Custom Domain"**
2. Add your domain (e.g., `forum.yourdomain.com`)
3. Update your DNS with the provided CNAME record

---

## First-Time Setup

### Creating the Admin Account

After deployment:

1. Visit your app URL (e.g., `https://your-app.railway.app`)
2. Click **"Register"** to create the first user account
3. The **first registered user becomes admin** automatically
4. This account is pre-verified (no email verification needed)

### Admin Dashboard

Access the admin dashboard at `/admin` to:
- Manage users (verify, enable/disable, promote to admin)
- Configure global settings (email, registration, API keys)
- View system statistics

---

## Environment Variable Reference

### Complete List

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Auto | PostgreSQL connection string (auto-injected by Railway) |
| `SECRET_KEY` | Yes | JWT signing key - **generate a unique one!** |
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key for AI features |
| `RESEND_API_KEY` | No | Resend.com API key for email verification |
| `GOOGLE_SAFE_BROWSING_API_KEY` | No | Google Safe Browsing API key |
| `WEB_BROWSE_SAFETY_MODE` | No | `safebrowsing` (default) or `allowlist` |

---

## Troubleshooting

### Build Failures

**Symptom:** Deployment fails during build

**Solutions:**
- Check that `requirements.txt` is present and valid
- Ensure all dependencies are listed
- View build logs for specific errors

### Database Connection Errors

**Symptom:** App crashes with database errors

**Solutions:**
- Verify PostgreSQL service is running in Railway
- Check that `DATABASE_URL` is properly linked (Railway Variables tab)
- Ensure the database plugin is in the same project

### Security Errors on Startup

**Symptom:** `SECURITY ERROR: You must set SECRET_KEY`

**Solution:** Add a `SECRET_KEY` environment variable:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### Health Check Failures

**Symptom:** Deployment keeps restarting

**Solutions:**
- Check app logs for startup errors
- Ensure `/health` endpoint is responding
- Increase `healthcheckTimeout` in `railway.toml` if needed

---

## Database Migrations

The app automatically runs migrations on startup via the start command:

```bash
alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port $PORT
```

To run migrations manually:

1. Open Railway's **Variables** tab and copy `DATABASE_URL`
2. Set it locally: `export DATABASE_URL="postgresql://..."`
3. Run: `alembic upgrade head`

---

## Updating Your Deployment

Railway auto-deploys on push to your connected branch:

```bash
git add .
git commit -m "Your changes"
git push origin main
```

For manual deployment, click **"Deploy"** in the Railway dashboard.

---

## Cost Considerations

Railway's pricing (as of 2024):
- **Free Tier:** $5 credit/month, includes hosting and database
- **Hobby Plan:** $5/month, includes $5 credit
- **Pro Plan:** Usage-based pricing for larger apps

This app typically uses:
- Minimal CPU during idle periods
- Increased usage during active simulations
- Database storage grows with simulation data

> [!TIP]
> Monitor your usage in Railway's **Usage** tab to avoid unexpected charges.

---

## Security Checklist

Before going live, ensure:

- [ ] `SECRET_KEY` is unique and secure (not the default)
- [ ] Admin account has a strong password
- [ ] Email verification is enabled (if using public registration)
- [ ] API keys are stored as environment variables (not in code)
- [ ] HTTPS is enforced (Railway does this automatically)

---

## Support

- **Railway Documentation:** https://docs.railway.app
- **Railway Discord:** https://discord.gg/railway
- **App Issues:** Open an issue in the GitHub repository
