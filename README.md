# ConstructIQ AI

> **Next-Generation Multimodal Construction Intelligence & Structural Engineering Platform**

ConstructIQ AI is a full-stack, enterprise-grade civil engineering intelligence system designed to streamline construction workflows. It integrates computer vision and multimodal large language models to automate hazard inspections, structural crack diagnostics, project scheduling, property valuation, climate advisories, and daily field reporting.

---

## Key Capabilities

| Module | AI Modality | Description |
|---|---|---|
| **Multi-Site Safety Command Hub** | Multimodal Vision + Telegram Sync | Simultaneous live monitoring of ~10 construction sites with dedicated CCTV screens, albums, and automated OSHA hazard detection. |
| **Telegram Field Labour Ingestion** | Telegram Bot API | Field labourers snap photos and send via Telegram (`#site1`, etc.); photos automatically route to site screens with instant safety alerts returned. |
| **Crack Diagnostics** | Structural Vision | Detects and categorizes surface vs. structural cracks, assessing risk severity and remediation procedures. |
| **Project Planner** | Reasoning LLM | Generates detailed phase timelines, critical path dependencies, material schedules, and cost estimates. |
| **Property Valuation** | Predictive AI | Calculates real estate valuations, price-per-sqft estimates, and comparable market analysis trends. |
| **Weather Risk Advisory** | Environmental AI | Assesses environmental forecasts to provide work-safety windows and activity-specific mitigation advisories. |
| **Daily Site Reporting** | Multimodal LLM | Converts raw site notes and optional inspection photos into standardized daily progress reports. |
| **Engineering Assistant** | Knowledge Assistant | Interactive AI consultation on structural codes, building standards, materials, and troubleshooting. |

---

## Architecture & Dual-Provider Support

ConstructIQ AI supports two operational runtime modes configured seamlessly via environment variables:

1. **Local Edge Inference (Offline / Private)**:
   - Uses [Ollama](https://ollama.com) running locally.
   - **Text & Reasoning**: `llama3.2` (3.2B parameters)
   - **Vision & Diagnostics**: `llava:7b` (7B parameters)
   - Zero external API costs, 100% data privacy on-premise.

2. **Cloud Production Deployment**:
   - Uses Google Gemini API (`gemini-3.5-flash-lite` / `gemini-2.0-flash-lite`)
   - Scalable, low-latency, and serverless-friendly for platforms like Render and Vercel.

---

## Repository Structure

```
constructiq-ai/
├── backend/
│   ├── main.py              # FastAPI application & unified AI routing engine
│   ├── requirements.txt     # Backend Python dependencies
│   ├── render.yaml          # Render web service deployment configuration
│   └── .env                 # Backend runtime environment configuration
└── frontend/
    ├── src/
    │   ├── App.jsx           # Master navigation & workspace dashboard
    │   ├── App.css           # Workspace & component styling
    │   ├── index.css         # Global design tokens & styling
    │   ├── main.jsx          # React 19 entry point
    │   ├── features/         # Feature modules
    │   │   ├── SafetyMonitoring.jsx
    │   │   ├── CrackDetection.jsx
    │   │   ├── ProjectPlanner.jsx
    │   │   ├── PropertyValuation.jsx
    │   │   ├── WeatherAdvisory.jsx
    │   │   ├── DailySiteReport.jsx
    │   │   └── AIAssistant.jsx
    │   ├── components/       # Shared UI components
    │   │   ├── ImageUploader.jsx
    │   │   ├── ResultCard.jsx
    │   │   └── Spinner.jsx
    │   └── services/
    │       └── api.js        # Typed HTTP client service
    ├── vercel.json          # Vercel SPA routing configuration
    ├── vite.config.js       # Vite build & proxy settings
    └── package.json         # Frontend dependencies & scripts
```

---

## Local Setup & Development

### 1. Prerequisites
- **Python**: 3.11 or higher
- **Node.js**: 18+ and **npm**: 9+
- *(Optional for local AI)*: [Ollama](https://ollama.com)

### 2. Clone Repository
```bash
git clone https://github.com/shivamojha23/constructiq-ai.git
cd constructiq-ai
```

### 3. Backend Setup
```bash
cd backend
python -m venv .venv

# Activate virtual environment
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

Create `backend/.env`:
```env
APP_HOST=0.0.0.0
APP_PORT=8000
ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

# Choose AI Provider:
# Option A: Local Ollama
USE_OLLAMA=true
OLLAMA_BASE_URL=http://localhost:11434

# Option B: Cloud Gemini
# USE_OLLAMA=false
# GEMINI_API_KEY=your_gemini_api_key_here

# Optional: Real Telegram Bot Integration
# TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
```

Start backend:
```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

### 4. Frontend Setup
```bash
cd frontend
npm install
npm run dev
```

Visit `http://localhost:5173` to access the ConstructIQ AI workspace.

---

## Production Deployment Guide

### Option 1: Ultra-Fast Global Edge Deployment (Recommended — Instant 0s Load Time)
This setup guarantees the fastest possible page load (0ms cold start) by serving the frontend from Vercel's global edge network and connecting to the FastAPI backend.

1. **Deploy Frontend to Vercel**:
   - Log into [vercel.com](https://vercel.com) using your GitHub account.
   - Click **Add New** > **Project** and select your repository: **`Shivam-Khandare/constructiq-ai`**.
   - Root directory is pre-configured via `vercel.json` (or set Root Directory to `frontend`).
   - Add Environment Variable:
     - `VITE_API_BASE_URL` = Your backend URL (e.g. `https://constructiq-api.onrender.com`)
   - Click **Deploy**. Your app is immediately live with edge CDN acceleration!

2. **Deploy Backend to Render / Railway**:
   - Log into [render.com](https://render.com) > **New** > **Blueprint** (connect `render.yaml`) or **Web Service**.
   - Root directory: `backend`.
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Add Environment Variables:
     - `GEMINI_API_KEY`: `<Your Gemini Key>`
     - `ALLOWED_ORIGINS`: `*`
     - `TELEGRAM_BOT_TOKEN`: `<Your Telegram Bot Token>`

### Option 2: Unified Single-Service Full-Stack Deployment (Render / Railway / Docker)
Because FastAPI is configured to automatically serve the pre-compiled `frontend/dist` static assets, you can run the entire system on a single port on Render or Railway:
1. Build frontend: `npm --prefix frontend install && npm --prefix frontend run build`
2. Run backend: `cd backend && uvicorn main:app --host 0.0.0.0 --port 8000`
3. Opening `http://your-app-url` serves the full ConstructIQ AI interactive UI with all API endpoints active.

---

## Author & License

Developed and maintained by **Shivam Khandare** ([@Shivam-Khandare](https://github.com/Shivam-Khandare)).  
Licensed under the MIT License.
