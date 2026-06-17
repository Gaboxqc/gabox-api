# GaboxAPI

This is the backend behind my portfolio site. Instead of hardcoding my projects and certifications into the frontend, I built a proper API so I can manage everything from one place and update the site without touching any frontend code.

It's a FastAPI + PostgreSQL app deployed on Vercel. Has full multilingual support — projects and certifications store their titles and descriptions in separate translation tables, which is what makes the three-language thing on the frontend actually work.

🌐 **Live:** [api.gabrielmayorga.dev](https://api.gabrielmayorga.dev) — the `/docs` route has the full interactive Swagger UI if you want to poke around.

**Frontend repo:** [github.com/Gaboxqc/Portfolio](https://github.com/Gaboxqc/Portfolio)

---

## Stack

- **FastAPI** — clean, fast, and the automatic docs are a huge plus
- **SQLModel** — handles both the ORM and the Pydantic schemas in one place
- **PostgreSQL** — the database
- **Alembic** — migrations
- **Vercel** — serverless deployment, zero config

---

## Endpoints

All routes are under `/portfolio`. GET endpoints are public. Anything that writes data needs an `X-API-KEY` header.

### Projects

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/portfolio/projects` | Filterable by `search`, `project_type_id`, `difficulty_id`, `tag_id` |
| `GET` | `/portfolio/project/{id}` | Single project |
| `POST` | `/portfolio/project` | 🔒 requires API key |
| `PATCH` | `/portfolio/project/{id}` | 🔒 requires API key |
| `DELETE` | `/portfolio/project/{id}` | 🔒 requires API key |

### Certifications

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/portfolio/certifications` | Filterable by `year`, `academy_id`, `category_id`, `tag_id`. Paginated |
| `GET` | `/portfolio/certification/{id}` | Single certification |
| `POST` | `/portfolio/certification` | 🔒 |
| `PATCH` | `/portfolio/certification/{id}` | 🔒 |
| `DELETE` | `/portfolio/certification/{id}` | 🔒 |

### Courses

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/portfolio/courses` | Filterable by `category_id`, `tag_id`. Paginated |
| `POST` | `/portfolio/courses` | 🔒 |
| `PATCH` | `/portfolio/courses/{id}` | 🔒 |
| `DELETE` | `/portfolio/courses/{id}` | 🔒 |

There are also endpoints for tags, categories, academies, languages, project types, and difficulty levels. Check `/docs` for the full list — it's easier to browse there anyway.

---

## How the data is structured

Each main resource (project, certification, course) has a core record with the metadata, a translations table for the multilingual content, and a join table for tags.

```
Project
  ├── ProjectTranslation   (title + description in each language)
  ├── Tags                 (many-to-many)
  ├── ProjectType
  └── DifficultyLevel

Certification / Course follow the same idea
  ├── Translation
  ├── Tags
  ├── Academy
  └── Category
```

---

## Running it locally

You'll need Python 3.10+ and a PostgreSQL database.

```bash
git clone https://github.com/Gaboxqc/GaboxAPI.git
cd GaboxAPI
pip install -r requirements.txt
```

Create a `.env` file:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/gaboxapi
API_MASTER_KEY=your-secret-key
```

Then run the migrations and start the server:

```bash
alembic upgrade head
uvicorn main:app --reload
```

API is at `http://localhost:8000`, docs at `http://localhost:8000/docs`.

---

## Auth

Write endpoints check for an `X-API-KEY` header:

```
X-API-KEY: your-secret-key
```

The key comes from `API_MASTER_KEY` in your environment. If that variable is missing, the server throws a 500 — I set it up that way on purpose so write access is never accidentally left open.

---

## Deploying

There's a `vercel.json` already set up. Just connect the repo on Vercel, add `DATABASE_URL` and `API_MASTER_KEY` as environment variables, and deploy. CORS is already configured for `gabrielmayorga.dev` and `localhost:5173`.

---

## License

MIT
