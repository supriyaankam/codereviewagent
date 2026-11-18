# 🤖 Code Review Agent

> An AI-powered Code Review Agent that learns from a team's previous code reviews and uses that knowledge to provide **personalized, consistent, and context-aware** code review feedback.

[![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green?logo=fastapi)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-blue?logo=react)](https://reactjs.org)
[![Firebase](https://img.shields.io/badge/Firebase-Firestore%20%2B%20Auth-orange?logo=firebase)](https://firebase.google.com)
[![Groq](https://img.shields.io/badge/AI-Groq%20LLM-purple)](https://groq.com)

---

## 📸 Screenshots

### 🔐 Login
![Login Page](screenshots/01_login.png)

### 📊 Dashboard
Real-time statistics from Firestore — total reviews, issues found, critical issues, accepted/rejected suggestions, and recent review history.

![Dashboard](screenshots/02_dashboard.png)

### 🔍 Code Review
Paste code, select language and project name, then click **Review Code** to get AI-powered analysis.

![Code Review](screenshots/03_code_review.png)

### 📋 Review Results
Structured results with severity badges (Critical, Warning, Info), source tool labels (AST/Pylint/Bandit/AI), line numbers, explanations, and suggested fixes.

![Review Results](screenshots/04_review_results.png)

### 🔧 AI Auto-Fix
One-click AI code correction — shows the fixed code, a change log, and automatically re-analyzes the corrected code.

![AI Auto-Fix](screenshots/05_auto_fix_result.png)

### 📁 Upload Project (Auto-Fix)
Upload a single file for the existing Auto-Fix flow, or upload a folder/ZIP as a project. Project uploads preserve relative paths and support analysis, scoring, per-finding fix proposals, explicit application, and reverting the latest accepted change.

![Auto-Fix Upload](screenshots/06_autofix_upload.png)

### 📜 Review History
Complete history of all past reviews with project name, language, risk level, issue count, status, date, and AI summary.

![Review History](screenshots/07_review_history.png)

---

## ✨ Features

- **🔍 AI Code Review** — Groq LLM analyzes code with context from team standards and past reviews; AI-only findings include confidence labels
- **🛠 Static Analysis** — Python AST, Pylint, and Bandit, plus parser-backed syntax and literal-credential checks for the other bundled languages
- **📈 Quality Score** — A deterministic 0–100 score with category breakdown, findings, and measurable Python complexity
- **🧑‍🎓 Beginner Explanations** — Toggle between concise technical findings and plain-language explanations
- **🔧 AI Fix Proposals** — Generate a proposal for an individual finding, compare it with the current source, and explicitly apply it to the editor
- **🔧 AI Auto-Fix** — Existing upload/manual correction workflow remains available and generates new downloadable output without overwriting source files
- **📁 Project Upload** — Upload single files and ZIP archives, or nested project folders up to the configured chunked-upload limit
- **🏷 Team Standards** — Define team-specific coding rules that the AI checks against
- **🧠 Memory Layer** — Hindsight stores accepted/rejected feedback so future reviews reflect team preferences
- **👍 Human Feedback** — Accept, Reject, or Modify each AI suggestion with reasons
- **📊 Live Dashboard** — Real Firestore-backed statistics updated after every review
- **📜 Review History** — Persistent history of all reviews across sessions
- **🔐 Authentication** — Firebase Email/Password and Google sign-in

### Review behavior and score

Python submissions use Python AST, Pylint, Bandit, and syntax-tree-based literal credential checks. JavaScript/JSX, TypeScript/TSX, Java, C#, Go, Ruby, PHP, C, C++, Rust, Kotlin, and Swift receive Tree-sitter syntax validation, literal credential checks, and enabled team-standard checks. Those non-Python checks do not provide general undefined-name, semantic, lint, or vulnerability analysis; Groq AI review supplements them. AI-reported line locations and explicitly named symbols are checked against the submitted source, and AI-only findings retain confidence labels. Other languages receive AI review only. The AI receives at most the first 12,000 characters for review; static checks inspect the full submitted source (up to 100,000 characters). Individual finding fixes send a bounded, location-targeted source region plus relevant context (14,000 characters by default), rather than the whole large file. Python regions prefer complete function, class, or statement boundaries; other parser-supported languages prefer a complete syntax-tree statement or declaration. Proposed fixes are re-analyzed, and a fix is not marked verified when the available analyzer cannot check its finding type. Whole-file/manual conversion requests remain limited to the original 14,000-character service limit. Submitted code is never executed.

The score starts at 100 and subtracts 20 for each unique critical finding, 12 for error, 5 for warning, and 1 for info. Static and AI duplicates are counted once within each file; lower-confidence AI-only suggestions are excluded. Python function cyclomatic complexity above 10 adds 2 points per step, capped at 30 per file, without double-counting complexity findings. The project score aggregates these per-file penalties and clamps the result to 0–100. The score includes only analyzed files; its coverage and failed/skipped file counts are shown. It is a review aid, not a guarantee that code is bug-free. Complexity is reported as unavailable for other languages and invalid Python.

### Large project folder uploads

The Auto-Fix page can select a folder (in browsers that support directory selection) and upload its files sequentially in 16 MiB chunks, or upload a ZIP archive (500 MiB compressed / 500 MiB extracted source limit, 20 MiB per source file). The backend writes uploads to temporary disk storage, preserves relative paths, verifies folder chunks with SHA-256, and binds project sessions to the authenticated Firebase user. Interrupted folder uploads can resume by selecting the same folder again in the same browser profile. Folder uploads default to a 4 GiB aggregate limit; this is a configured ceiling, not a guarantee that a hosting environment has enough capacity.

After upload, the file list marks unsupported extensions, hidden/dependency/generated paths, binary content, and source files over 20 MiB as skipped by default. Users may select up to 200 supported files; analysis is sent in sequential requests of at most 20 files and 64 MiB of source content. Larger selections are split into bounded requests. Static analysis checks each selected file; AI review is issued one file at a time and uses at most the first 12,000 characters, with no full-project prompt. Unselected files are not sent to the AI provider. Each finding has a **Fix this issue** action: it generates a proposal for a bounded source region, which the user must review and explicitly apply. Python fixes prefer the complete containing function/class and fall back to a smaller AST statement only when needed; the proposal is mapped back using an exact source snapshot and applied only after the source hash and patch region are verified. Unsupported or failed static validation requires explicit approval. Applied fixes update only the temporary uploaded project copy, create a backup, and can be restored. The default source-plus-context budget per finding-level fix is 14,000 characters and can be lowered with `AUTOFIX_MAX_CONTEXT_CHARS`; large files are not sent whole to the fix model. The legacy single-file/ZIP Auto-Fix endpoint accepts 50 MiB single files and 500 MiB ZIP archives (500 MiB total extracted source; 20 MiB per source entry).

On the Code Review page, the individual **Suggest a fix for this finding** action also uses location-targeted source extraction for large files. The separate whole-file **Fix Code** / conversion action and legacy single-file Auto-Fix request retain the 14,000-character limit.

Finding-level AutoFix sends one Groq request for the selected finding; it does not issue a separate fix request for every finding. If Groq returns HTTP 429, AutoFix does not retry automatically: it reports the quota category and `Retry-After`/reset guidance when provided, and the user may retry after the stated wait. The Groq SDK's implicit retries are disabled for AutoFix requests to avoid consuming additional quota unexpectedly.

Configure these values in `backend/.env` (also listed in [`backend/.env.example`](backend/.env.example)):

| Setting | Default | Purpose |
|---|---:|---|
| `MAX_PROJECT_UPLOAD_BYTES` | `4294967296` | Aggregate folder upload limit in bytes |
| `PROJECT_UPLOAD_CHUNK_BYTES` | `16777216` | Chunk size; must be between 64 KiB and 16 MiB |
| `PROJECT_UPLOAD_TTL_HOURS` | `24` | Expiration for uploaded projects and incomplete sessions |
| `PROJECT_UPLOAD_TEMP_DIR` | OS temporary directory | Optional upload storage path or mounted volume |
| `PROJECT_UPLOAD_SKIP_DIRS` | empty | Additional comma-separated directory names to skip |
| `PROJECT_MAX_ANALYSIS_FILES` | `200` | Maximum selected files per analysis pass (200 hard ceiling) |
| `PROJECT_MAX_ANALYSIS_REQUEST_FILES` | `20` | Maximum files per API request (20 hard ceiling) |
| `PROJECT_MAX_ANALYSIS_BATCH_BYTES` | `67108864` | Maximum source bytes per request (128 MiB hard ceiling) |
| `PROJECT_MAX_ANALYSIS_FILE_BYTES` | `20971520` | Maximum source file size to analyze (20 MiB hard ceiling) |
| `AUTOFIX_MAX_CONTEXT_CHARS` | `14000` | Maximum targeted source region plus context sent for a finding-level AI fix (1000–14000) |

Expired sessions are cleaned hourly and when a new upload is started. Provide enough free disk for the project plus the file currently being assembled (up to roughly twice the configured project limit for a single-file project). The deployment must keep the upload volume available to the backend instance for the session lifetime; local temporary storage may be ephemeral. This implementation uses process-local locking, so run one backend worker per upload volume; multi-worker/multi-instance scaling needs shared storage plus cross-process coordination or an object-storage upload provider. This repository has no production reverse-proxy/API-gateway configuration. Configure any hosting request-body limit to allow at least the configured `PROJECT_UPLOAD_CHUNK_BYTES` plus headers (16 MiB by default), and verify per-request timeouts. The 500 MiB legacy ZIP endpoint still processes extracted entries/results in memory; use the resumable project-folder/ZIP flow for large projects and verify available memory before enabling large legacy ZIP requests. Browser folder uploads require HTTPS or localhost for Web Crypto checksums. Firebase Authentication must be configured in both frontend and backend for the new folder-upload API. No object-storage provider is configured here.

All project fixes are generated as proposals and are applied only after the user explicitly selects **Apply fix** or **Approve and apply unverified fix**. Every applied project fix creates a backup and updates only the temporary uploaded project copy—not the user's local folder. Revert the latest change or reanalyze the updated project to refresh findings and score. The comparison uses line-level additions, removals, and replacements with language syntax highlighting.

---

## 🏗 Tech Stack

| Layer          | Technology                   |
|----------------|------------------------------|
| Frontend       | React 18 + Vite + Bootstrap  |
| Backend        | Python 3.11 + FastAPI        |
| Database       | Firebase Firestore           |
| Authentication | Firebase Authentication      |
| AI Model       | Groq API (LLaMA / GPT-OSS)   |
| Static Analysis| Python AST + Pylint + Bandit |
| Memory         | Hindsight (Firestore-backed) |

---

## 📂 Project Structure

```
code-review-agent/
├── frontend/                  # React.js application
│   ├── src/
│   │   ├── pages/             # Dashboard, CodeReview, AutoFix, ReviewHistory, TeamStandards
│   │   ├── components/        # Layout, ProtectedRoute
│   │   ├── context/           # AuthContext
│   │   ├── services/          # firebase.js, authService, firestoreService
│   │   └── styles/            # CSS modules
│   └── .env.example
├── backend/                   # Python FastAPI application
│   ├── app/
│   │   ├── routers/           # review, reviews, feedback, standards, dashboard, fix, analyze
│   │   ├── services/          # ai_reviewer, firebase_service, code_analyzer, auto_fix_service
│   │   ├── analyzers/         # python_analyzer, security_analyzer, base
│   │   └── main.py
│   ├── firebase/              # Place serviceAccountKey.json here (not committed)
│   ├── requirements.txt
│   └── .env.example
├── screenshots/               # UI screenshots
├── README.md
└── .gitignore
```

---

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- Node.js 20+
- Firebase project with Firestore + Authentication enabled
- Groq API key (free at [console.groq.com](https://console.groq.com))

---

### Backend Setup

```bash
cd backend

# Create and activate virtual environment
python -m venv venv

# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
```

Edit `backend/.env`:

```env
FIREBASE_SERVICE_ACCOUNT_PATH=./firebase/serviceAccountKey.json
FIREBASE_STORAGE_BUCKET=<exact bucket name from Firebase Console>
GROQ_API_KEY=your_groq_api_key_here
```

Place your Firebase service account JSON at:
```
backend/firebase/serviceAccountKey.json
```

Create a Cloud Storage bucket in the Firebase Console and set
`FIREBASE_STORAGE_BUCKET` to the exact bucket name shown under **Storage**. The
backend writes project files to Cloud Storage using its Admin SDK credentials;
project metadata, analysis summaries/findings, and scores are stored in
Firestore. Source archives are not stored in Firestore. If the bucket setting
is missing, project upload remains local to backend temporary storage and is
not durable across server cleanup or replacement.

Deploy the included owner-scoped rules after selecting your Firebase project:

```powershell
firebase use YOUR_FIREBASE_PROJECT_ID
firebase deploy --only firestore:rules,storage
```

The backend uses authenticated, checksummed chunks for folder uploads. The
configured 4 GiB ceiling is only a software limit: actual support depends on
available backend temporary disk, hosting request limits/timeouts, bucket
permissions, and Storage quota/billing. The project ZIP route accepts up to
500 MiB compressed and extracts at most 500 MiB of supported source files, with
a 20 MiB per-entry limit. The backend checks available local disk space before
accepting uploads, but a deployment must still be validated at the intended
size.

Start the backend:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

Backend runs at: **http://127.0.0.1:8001**
API docs: **http://127.0.0.1:8001/docs**

---

### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Copy environment template
cp .env.example .env
```

Edit `frontend/.env`:

```env
VITE_FIREBASE_API_KEY=your_api_key
VITE_FIREBASE_AUTH_DOMAIN=your_project.firebaseapp.com
VITE_FIREBASE_PROJECT_ID=your_project_id
VITE_FIREBASE_STORAGE_BUCKET=your_project.appspot.com
VITE_FIREBASE_MESSAGING_SENDER_ID=your_sender_id
VITE_FIREBASE_APP_ID=your_app_id
```

Start the frontend:

```bash
npm run dev
```

Frontend runs at: **http://localhost:3000**

Run frontend tests and checks:

```bash
npm test
npm run lint
npm run build
```

Run backend unit tests from `backend/`:

```bash
python -m unittest discover -s tests -v
```

---

## 🔌 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/review` | Full AI + static code review |
| `GET`  | `/api/dashboard` | Real-time dashboard statistics |
| `GET`  | `/api/reviews` | Review history for a user |
| `POST` | `/api/reviews/fix` | AI auto-fix code |
| `POST` | `/api/fix/upload` | Upload file/ZIP for batch fix |
| `POST` | `/api/feedback` | Submit Accept/Reject/Modify feedback |
| `GET`  | `/api/standards` | Get team standards |
| `POST` | `/api/standards` | Create team standard |
| `GET`  | `/api/firebase/health` | Firebase connection test |
| `GET`  | `/api/groq-test` | Groq API connection test |

---

## 🔒 Security

- API keys and service account credentials are **never committed** to Git
- Submitted code is **never executed** — only parsed and analyzed as text
- Firebase Admin SDK runs **server-side only** — credentials never sent to frontend
- Firestore security rules protect user data

---

## 📄 License

MIT License — feel free to use, modify, and distribute.

---

*Built with ❤️ using React, FastAPI, Firebase, and Groq AI*
