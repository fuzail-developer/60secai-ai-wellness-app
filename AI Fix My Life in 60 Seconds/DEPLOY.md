# Deploy Helper

## One-Click Links

- Render: https://render.com/deploy?repo=<YOUR_GITHUB_REPO_URL>
- Railway: https://railway.app/new?referralCode=<YOUR_CODE>

## Quick Commands

```bash
# 1) Push project to GitHub first
git init
git add .
git commit -m "deploy prep"
git branch -M main
git remote add origin <YOUR_GITHUB_REPO_URL>
git push -u origin main
```

```bash
# 2) Required env vars in hosting dashboard
SECRET_KEY=<generate-random-secret>
DATABASE_URL=sqlite:///app.db
OPENAI_API_KEY=<optional>
```

## Procfile

This project already includes:

```text
web: gunicorn app:app
```

## App Name

`create-a-production-ready-flask-web-app-called-60secai-ai-fix-my`
