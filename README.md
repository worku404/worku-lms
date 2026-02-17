# Gold-EDU E-Learning Platform

A production-oriented Django learning management system with course publishing, enrollment, module-based learning, token-secured REST APIs, and in-page AI assistance.

## Preview


![Homepage Preview](./docs/images/preview-home.png)
![Course Workspace Preview](./docs/images/preview-workspace.png)
![API Developer Tools Preview](./docs/images/preview-api-tools.png)

---

## Table of Contents

- [Overview](#overview)
- [Feature Highlights](#feature-highlights)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [Web Routes](#web-routes)
- [API Overview](#api-overview)
- [API Quick Start](#api-quick-start)
- [Security Notes](#security-notes)
- [Documentation](#documentation)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

Gold-EDU is a university-focused e-learning platform built with Django.  
It supports public discovery, guided learning, instructor content management, and external integration through REST APIs.

---

## Feature Highlights

### Learning Platform
- Course catalog with subject filtering and keyword search
- Student dashboard for enrolled courses
- Course detail and module-based learning workspace
- Learning content blocks:
  - Text
  - Video
  - Image
  - File
- Instructor management panel for course/module/content CRUD
- Module/content ordering support

### API (Django REST Framework)
- Router-based endpoints for `courses` and `subjects`
- Pagination (`page`, `page_size`, max 50)
- Subject enrichment:
  - `total_courses`
  - `popular_courses`
- Course actions:
  - `POST /api/courses/{id}/enroll/`
  - `GET /api/courses/{id}/contents/` (requires enrollment)

### Authentication & Developer Experience
- Token authentication (`/api/token-auth/`)
- Basic auth fallback for quick testing
- Developer token dashboard (`/api/developer/token-ui/`)
  - view/copy token
  - rotate token
  - ready-to-use request examples
- Auto token creation for newly created users (signals)

### UI/UX
- Responsive layout and polished component system
- Theme toggle (light/dark mode)
- API docs page styling improvements (overflow-safe + responsive)
- AI assistant sidebar with Gemini integration

---

## Tech Stack

- **Backend:** Django, Django REST Framework
- **Database:** SQLite (default)
- **Cache:** Redis (`django-redis`, configured)
- **Frontend:** Django Templates, modern CSS, vanilla JS
- **Media:** Pillow
- **Video Embeds:** `django-embed-video`
- **AI Integration:** Gemini API via `requests`

---

## Project Structure

```text
e-learning/
├─ edu/
│  ├─ assistant/                 # AI assistant app
│  ├─ courses/
│  │  ├─ api/                    # DRF viewsets, serializers, permissions, routes
│  │  ├─ templates/              # web + API developer UI templates
│  │  ├─ static/css/             # tokens/components/pages styles
│  │  └─ signals.py              # auto token creation
│  ├─ students/                  # student registration/enrollment/learning views
│  ├─ edu/                       # project settings + root urls
│  └─ manage.py
├─ api_examples/                 # API automation examples
├─ docs/
│  └─ api.md                     # full API reference
├─ requirements.txt
└─ README.md
```

---

## Getting Started

### 1) Clone repository

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd e-learning
```

### 2) Create virtual environment

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS/Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3) Install dependencies

```bash
pip install -r requirements.txt
```

### 4) Run migrations and seed subjects

```bash
cd edu
python manage.py migrate
python manage.py loaddata courses/fixtures/subjects.json
```

### 5) Create a superuser (recommended)

```bash
python manage.py createsuperuser
```

### 6) Run server

```bash
python manage.py runserver
```

Open: `http://127.0.0.1:8000/`

---

## Environment Variables

Create `.env` in `edu/` (or project root):

```env
# Gemini API keys (fallback order)
API1_KEY=your_key_here
API2_KEY=your_key_here
API3_KEY=your_key_here
API4_KEY=your_key_here
```

Notes:
- If keys are missing/invalid, assistant requests fail gracefully.
- Never commit real credentials.

---

## Web Routes

### Public
- `/` → course catalog
- `/course/<slug>/` → course detail

### Auth
- `/accounts/login/`
- `/accounts/logout/`

### Student
- `/students/register/`
- `/students/courses/`
- `/students/course/<course_id>/`
- `/students/enroll-course/`

### Instructor
- `/course/mine/`
- `/course/create/`
- `/course/<pk>/edit/`
- `/course/<pk>/module/`

### Assistant
- `/assistant/`
- `/assistant/llm/generate/` (POST)

---

## API Overview

Base URL: `/api/`

Core endpoints:
- `GET /api/subjects/`
- `GET /api/subjects/{id}/`
- `GET /api/courses/`
- `GET /api/courses/{id}/`
- `POST /api/courses/{id}/enroll/` (auth required)
- `GET /api/courses/{id}/contents/` (auth + enrolled required)
- `POST /api/token-auth/`
- `GET /api/developer/token-ui/` (login required UI)

---

## API Quick Start

### 1) Get token (PowerShell)
```powershell
$body = @{
  username = "your_username"
  password = "your_password"
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/token-auth/" `
  -ContentType "application/json" `
  -Body $body
```

### 2) Enroll in a course
```powershell
curl.exe -X POST http://127.0.0.1:8000/api/courses/1/enroll/ -H "Authorization: Token your_generated_token_value"
```

### 3) Access protected course contents
```powershell
curl.exe http://127.0.0.1:8000/api/courses/1/contents/ -H "Authorization: Token your_generated_token_value"
```

---

## Security Notes

- Treat API tokens as secrets.
- Rotate tokens immediately if exposed.
- Use HTTPS in production.
- Prefer token auth for integrations; use basic auth for local diagnostics only.

---

## Documentation

- Full API guide: [`docs/api.md`](./docs/api.md)
- API automation example: `api_examples/enroll_all.py`

---

## Roadmap

- [ ] Assignments and quizzes
- [ ] Progress analytics dashboard
- [ ] Notifications and reminders
- [ ] OpenAPI schema + generated client SDK
- [ ] Production deployment guide (Docker + Nginx + PostgreSQL)

---

## Contributing

1. Fork the repository  
2. Create a feature branch  
3. Commit changes  
4. Push branch  
5. Open Pull Request

---

## License

This project is licensed under the terms of the LICENSE file in this repository.