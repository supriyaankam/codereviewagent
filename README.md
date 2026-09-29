# Code Review Agent

An AI-powered Code Review Agent that learns from a team's previous code reviews and uses that knowledge to provide personalized, consistent, and context-aware code review feedback.

## Project Structure

```
code-review-agent/
├── frontend/       # React.js application
├── backend/        # Python FastAPI application
├── README.md
└── .gitignore
```

## Tech Stack

| Layer          | Technology                  |
|----------------|-----------------------------|
| Frontend       | React.js (JavaScript)       |
| Backend        | Python + FastAPI             |
| Database       | Firebase Firestore           |
| Authentication | Firebase Authentication      |
| Styling        | Bootstrap                    |
| API            | REST                         |

## Getting Started

### Backend

```bash
cd backend
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Backend runs at: http://localhost:8000  
Health check: http://localhost:8000/api/health

### Frontend

```bash
cd frontend
npm install
npm start
```

Frontend runs at: http://localhost:3000
